"""专家材料包的文件存储、快速编译与 Skill 发布。"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import yaml

from yuxi.config import get_save_dir
from yuxi.expert.index import build_document_index
from yuxi.expert.prime_acp import run_prime_acp
from yuxi.utils.datetime_utils import utc_isoformat

if TYPE_CHECKING:
    from yuxi.services.task_service import TaskContext

EXPERT_PACKAGES_DIR_NAME = "expert_packages"
METADATA_FILE_NAME = "metadata.json"
SOURCE_DIR_NAME = "source"
COMPILED_DIR_NAME = "compiled"
SEARCH_INDEX_PATH = Path("nav") / "search-index.jsonl"
MAX_UPLOAD_BYTES = int(os.getenv("EXPERT_PACKAGE_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))
MAX_ARCHIVE_FILES = int(os.getenv("EXPERT_PACKAGE_MAX_ARCHIVE_FILES", "5000"))
MAX_ARCHIVE_EXPANDED_BYTES = int(os.getenv("EXPERT_PACKAGE_MAX_ARCHIVE_EXPANDED_BYTES", str(2 * 1024 * 1024 * 1024)))
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def get_expert_packages_root() -> Path:
    """返回专家材料包根目录并确保其存在。"""

    root = get_save_dir() / EXPERT_PACKAGES_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def create_expert_package(
    *,
    name: str,
    description: str,
    filename: str,
    content: bytes,
    created_by: str,
) -> dict[str, Any]:
    """保存一个 Markdown/ZIP 材料包并创建可编译项目。"""

    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("专家包名称不能为空")
    if not content:
        raise ValueError("材料包文件不能为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"材料包不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    package_id = _available_package_id(_slugify(clean_name))
    package_root = get_expert_packages_root() / package_id
    source_root = package_root / SOURCE_DIR_NAME
    source_root.mkdir(parents=True, exist_ok=False)
    try:
        _extract_materials(filename, content, source_root)
        source_files = _markdown_files(source_root)
        if not source_files:
            raise ValueError("材料包中没有 UTF-8 Markdown 文件")
        _validate_utf8_sources(source_files)

        now = utc_isoformat()
        metadata = {
            "id": package_id,
            "name": clean_name,
            "description": str(description or "").strip(),
            "created_by": str(created_by),
            "status": "uploaded",
            "stage": "materials",
            "compiler": "skill-index-v1",
            "version": 0,
            "active_version": None,
            "source_files": len(source_files),
            "source_bytes": sum(path.stat().st_size for path in source_files),
            "source_hash": _source_tree_hash(source_root, source_files),
            "skill_slug": None,
            "active": False,
            "active_task_id": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        _write_metadata(package_root, metadata)
        return metadata
    except Exception:
        shutil.rmtree(package_root, ignore_errors=True)
        raise


def list_expert_packages() -> list[dict[str, Any]]:
    """列出所有保存完好的专家材料包。"""

    items = []
    for path in get_expert_packages_root().iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            items.append(_read_metadata(path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return sorted(items, key=lambda item: (item.get("updated_at") or "", item["id"]), reverse=True)


def get_expert_package(package_id: str) -> dict[str, Any]:
    """读取一个专家材料包的事实状态。"""

    return _read_metadata(_package_root(package_id))


def update_expert_package(package_id: str, **changes: Any) -> dict[str, Any]:
    """原子更新专家材料包元数据。"""

    package_root = _package_root(package_id)
    metadata = _read_metadata(package_root)
    metadata.update(changes)
    metadata["updated_at"] = utc_isoformat()
    _write_metadata(package_root, metadata)
    return metadata


async def compile_expert_package(package_id: str, context: TaskContext) -> dict[str, Any]:
    """批量处理 Markdown 并编译为可由 Yuxi Agent 加载的 Skill。"""

    current = get_expert_package(package_id)
    version = int(current.get("version") or 0) + 1
    update_expert_package(
        package_id,
        status="processing",
        stage="indexing",
        active_task_id=context.task_id,
        version=version,
        progress=0,
        message="正在准备编译",
        error=None,
    )
    await context.set_progress(10, "正在清点 Markdown 材料")
    await context.raise_if_cancelled()

    package_root = _package_root(package_id)
    source_root = package_root / SOURCE_DIR_NAME
    source_files = _markdown_files(source_root)
    if not source_files:
        raise ValueError("材料目录中没有 Markdown 文件")

    await context.set_progress(35, f"正在为 {len(source_files)} 份材料建立检索索引")
    await context.raise_if_cancelled()
    rows = await asyncio.to_thread(_build_index_rows, source_root, source_files)

    await context.set_progress(70, "正在编译专家 Skill")
    await context.raise_if_cancelled()
    skill_root, release_hash = await asyncio.to_thread(
        _build_compiled_skill,
        package_root,
        source_root,
        source_files,
        rows,
    )

    metadata = update_expert_package(
        package_id,
        status="compiled",
        stage="release",
        active=False,
        active_task_id=None,
        compiler="skill-index-v1",
        error=None,
        compiled_path=str(skill_root.relative_to(package_root)),
        release_hash=release_hash,
        version=version,
        source_hash=_source_tree_hash(source_root, source_files),
        indexed_documents=len(rows),
        progress=100,
        message="专家 Skill 已编译，可激活使用",
    )
    await context.set_progress(100, "专家 Skill 已编译，可激活使用")
    return {
        "package_id": package_id,
        "status": metadata["status"],
        "documents": len(rows),
        "release_hash": release_hash,
        "version": version,
    }


async def compile_expert_package_with_prime(package_id: str, context: TaskContext) -> dict[str, Any]:
    """先完成确定性编译，再用 Prime ACP 生成可追溯的领域方法说明。"""

    result = await compile_expert_package(package_id, context)
    metadata = get_expert_package(package_id)
    package_root = _package_root(package_id)
    compiled_root = package_root / str(metadata.get("compiled_path") or COMPILED_DIR_NAME)
    session_dir = package_root / "prime-sessions" / f"v{metadata['version']}"
    update_expert_package(
        package_id,
        status="processing",
        stage="prime",
        active_task_id=context.task_id,
        compiler="prime-acp-v1",
        prime_session_dir=str(session_dir.relative_to(package_root)),
        message="正在通过 Prime ACP 生成领域方法说明",
    )
    await context.set_progress(80, "正在通过 Prime ACP 生成领域方法说明")
    await context.raise_if_cancelled()

    response = await run_prime_acp(
        cwd=compiled_root,
        session_dir=session_dir,
        prompt=_prime_compile_prompt(),
        provider=os.getenv("PRIME_AGENT_PROVIDER") or None,
        model=os.getenv("PRIME_AGENT_MODEL") or None,
    )
    method_path = compiled_root / "method.md"
    await asyncio.to_thread(method_path.write_text, response.text.rstrip() + "\n", encoding="utf-8")
    manifest_metadata = {**metadata, "compiler": "prime-acp-v1"}
    release_hash = await asyncio.to_thread(_write_release_manifest, compiled_root, manifest_metadata)
    updated = update_expert_package(
        package_id,
        status="compiled",
        stage="release",
        active=False,
        active_task_id=None,
        compiler="prime-acp-v1",
        release_hash=release_hash,
        prime_session_id=response.session_id,
        progress=100,
        message="Prime ACP 深度编译完成，可激活使用",
        error=None,
    )
    await context.set_progress(100, "Prime ACP 深度编译完成，可激活使用")
    return {
        **result,
        "status": updated["status"],
        "release_hash": release_hash,
        "compiler": updated["compiler"],
    }


def mark_expert_package_failed(package_id: str, error: str) -> dict[str, Any]:
    """记录编译或激活失败，并清除运行中的任务标识。"""

    return update_expert_package(
        package_id,
        status="failed",
        active_task_id=None,
        progress=100,
        message="专家 Skill 编译失败",
        error=str(error),
    )


def get_compiled_skill_dir(package_id: str) -> Path:
    """返回已经通过快速编译生成的 Skill 根目录。"""

    package_root = _package_root(package_id)
    metadata = _read_metadata(package_root)
    if metadata.get("status") not in {"compiled", "active"}:
        raise ValueError("专家包尚未完成编译")
    target = package_root / str(metadata.get("compiled_path") or COMPILED_DIR_NAME)
    if not (target / "SKILL.md").is_file() or not (target / SEARCH_INDEX_PATH).is_file():
        raise ValueError("专家包编译产物不完整")
    return target


def delete_expert_package(package_id: str) -> None:
    """删除尚未激活的专家材料包。"""

    package_root = _package_root(package_id)
    metadata = _read_metadata(package_root)
    if metadata.get("active"):
        raise ValueError("请先停用专家包再删除")
    shutil.rmtree(package_root)


def _extract_materials(filename: str, content: bytes, target: Path) -> None:
    normalized = Path(filename or "").name.lower()
    if normalized.endswith(".md") or normalized.endswith(".markdown"):
        (target / (Path(filename).name or "material.md")).write_bytes(content)
        return
    if not normalized.endswith(".zip"):
        raise ValueError("仅支持上传 Markdown 文件或包含 Markdown 的 ZIP")

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > MAX_ARCHIVE_FILES:
                raise ValueError(f"ZIP 文件数量不能超过 {MAX_ARCHIVE_FILES}")
            if sum(info.file_size for info in infos) > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError("ZIP 解压后的内容过大")
            for info in infos:
                relative = PurePosixPath(info.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"ZIP 包含不安全路径: {info.filename}")
                if _zip_entry_is_symlink(info):
                    raise ValueError(f"ZIP 不允许包含符号链接: {info.filename}")
                if relative.suffix.lower() not in {".md", ".markdown"}:
                    continue
                destination = target.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(info))
    except zipfile.BadZipFile as exc:
        raise ValueError("上传文件不是有效 ZIP") from exc


def _build_index_rows(source_root: Path, source_files: list[Path]) -> list[dict]:
    rows = []
    for path in source_files:
        relative_path = path.relative_to(source_root).as_posix()
        rows.append(build_document_index(relative_path, path.read_text(encoding="utf-8")))
    return rows


def _build_compiled_skill(
    package_root: Path,
    source_root: Path,
    source_files: list[Path],
    rows: list[dict],
) -> tuple[Path, str]:
    metadata = _read_metadata(package_root)
    target = package_root / COMPILED_DIR_NAME
    temp = Path(tempfile.mkdtemp(prefix=".compiled-", dir=package_root))
    try:
        references = temp / "references"
        for source in source_files:
            destination = references / source.relative_to(source_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

        index_path = temp / SEARCH_INDEX_PATH
        index_path.parent.mkdir(parents=True, exist_ok=True)
        _write_jsonl(index_path, rows)
        (temp / "nav" / "catalog.md").write_text(_catalog_markdown(metadata, rows), encoding="utf-8")
        (temp / "sources.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "path": row["path"],
                        "sha256": _file_hash(temp / row["path"]),
                    },
                    ensure_ascii=False,
                )
                + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
        (temp / "SKILL.md").write_text(_skill_markdown(metadata), encoding="utf-8")

        release_hash = _write_release_manifest(temp, metadata)

        if target.exists():
            shutil.rmtree(target)
        temp.rename(target)
        return target, release_hash
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def _skill_markdown(metadata: dict[str, Any]) -> str:
    description = metadata.get("description") or f"使用 {metadata['name']} 专家材料回答相关问题。"
    frontmatter = yaml.safe_dump(
        {
            "name": metadata["id"],
            "description": description,
            "version": str(metadata.get("version") or "0"),
            "tool_dependencies": ["expert_search"],
        },
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return (
        f"---\n{frontmatter}\n---\n\n"
        f"# {metadata['name']}\n\n"
        "收到与本领域有关的问题时，先调用 `expert_search`，`skill_slug` 使用本 Skill 的 name。\n\n"
        "1. 根据候选标题、命中词和摘要选择最相关的 1-3 份材料。\n"
        "2. 使用 `read_file` 读取搜索结果返回的完整路径，不要只凭摘要回答。\n"
        "3. 材料之间存在条件差异或冲突时分别说明，不把经验口径改写成通用事实。\n"
        "4. 回答末尾列出实际读取的材料标题或路径。\n"
        "5. 没有相关结果、原文不足或信息可能过时时，明确说明边界，不用模型常识补齐。\n\n"
        "完整材料目录位于 `nav/catalog.md`，原文位于 `references/`。"
        "若存在 `method.md`，可先阅读它了解领域术语和路径，\n"
        "但最终事实必须回到 `references/` 原文。\n"
    )


def _prime_compile_prompt() -> str:
    """返回 Prime ACP 的领域方法说明提示。"""

    return (
        "你是专家 Skill 的深度编译作者。当前工作目录是一个已经完成确定性整理的 Skill，"
        "其中 references/ 保存唯一事实原文，nav/catalog.md 是目录。"
        "请先阅读这些材料，再生成一份紧凑的 Markdown 方法说明。\n\n"
        "输出要求：\n"
        "1. 只输出 Markdown 正文，不要 YAML frontmatter、代码围栏或解释前后缀。\n"
        "2. 内容只来自 references/ 原文；不要用常识补齐，不要把不确定推断写成事实。\n"
        "3. 说明术语、判断顺序、关键条件、冲突边界和引用材料路径，帮助后续 Agent 更快定位原文。\n"
        "4. 不修改任何文件；宿主会把你的输出写入 method.md。"
    )


def _write_release_manifest(skill_root: Path, metadata: dict[str, Any]) -> str:
    """重算 Skill 发布清单与发布哈希。"""

    manifest_path = skill_root / "manifest.json"
    manifest_path.unlink(missing_ok=True)
    files = [
        {
            "path": path.relative_to(skill_root).as_posix(),
            "sha256": _file_hash(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(item for item in skill_root.rglob("*") if item.is_file())
    ]
    release_hash = hashlib.sha256(
        json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(
            {
                "name": metadata["id"],
                "compiler": metadata["compiler"],
                "version": metadata.get("version"),
                "source_hash": metadata["source_hash"],
                "release_hash": release_hash,
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return release_hash


def _catalog_markdown(metadata: dict[str, Any], rows: list[dict]) -> str:
    lines = [f"# {metadata['name']} 材料目录", ""]
    for row in rows:
        lines.append(f"- [{row['title']}](../{row['path']})")
        if row.get("headings"):
            lines.append(f"  - 章节：{' / '.join(row['headings'][:5])}")
    return "\n".join(lines).rstrip() + "\n"


def _package_root(package_id: str) -> Path:
    clean_id = str(package_id or "").strip()
    if not SLUG_RE.fullmatch(clean_id):
        raise ValueError("无效专家包 ID")
    root = get_expert_packages_root()
    target = (root / clean_id).resolve()
    if not target.is_relative_to(root) or not target.is_dir():
        raise ValueError("专家包不存在")
    return target


def _available_package_id(base: str) -> str:
    root = get_expert_packages_root()
    if not (root / base).exists():
        return base
    index = 2
    while (root / f"{base}-{index}").exists():
        index += 1
    return f"{base}-{index}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or f"expert-{uuid.uuid4().hex[:8]}"


def _read_metadata(package_root: Path) -> dict[str, Any]:
    path = package_root / METADATA_FILE_NAME
    if not path.is_file():
        raise ValueError("专家包元数据不存在")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("id") != package_root.name:
        raise ValueError("专家包元数据无效")
    return value


def _write_metadata(package_root: Path, metadata: dict[str, Any]) -> None:
    path = package_root / METADATA_FILE_NAME
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in {".md", ".markdown"}
    )


def _validate_utf8_sources(files: list[Path]) -> None:
    for path in files:
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Markdown 不是 UTF-8 编码: {path.name}") from exc


def _source_tree_hash(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _zip_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


__all__ = [
    "compile_expert_package",
    "create_expert_package",
    "delete_expert_package",
    "get_compiled_skill_dir",
    "get_expert_package",
    "list_expert_packages",
    "mark_expert_package_failed",
    "update_expert_package",
]
