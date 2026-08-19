"""专家项目私有材料与产物目录。"""

from __future__ import annotations

import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately

from yuxi.config import get_save_dir
from yuxi.knowledge.parser.unified import SUPPORTED_FILE_EXTENSIONS
from yuxi.services.ocr_service import parse_document
from yuxi.utils.paths import ensure_within_root

PROJECT_ID_RE = re.compile(r"^[a-f0-9]{32}$")
EXPERT_PROJECTS_DIR_NAME = "expert_projects"
MAX_ARCHIVE_FILES = int(os.getenv("EXPERT_PROJECT_MAX_ARCHIVE_FILES", "5000"))
MAX_ARCHIVE_EXPANDED_BYTES = int(os.getenv("EXPERT_PROJECT_MAX_ARCHIVE_EXPANDED_BYTES", str(2 * 1024 * 1024 * 1024)))
MATERIAL_EXTENSIONS = set(SUPPORTED_FILE_EXTENSIONS) - {".zip"}


@dataclass(frozen=True, slots=True)
class StoredMaterialSource:
    """已经安全写入项目私有目录的一份原始材料。"""

    material_id: str
    title: str
    source_path: str
    raw_path: str
    file_type: str


@dataclass(frozen=True, slots=True)
class ParsedMaterial:
    """一份材料转换后的可靠正文信息。"""

    text_path: str
    line_count: int
    token_count: int


def get_expert_projects_root() -> Path:
    """返回专家项目私有根目录并确保其存在。"""

    root = (get_save_dir() / EXPERT_PROJECTS_DIR_NAME).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_project_root(project_id: str) -> Path:
    """返回一个已校验边界的专家项目私有目录。"""

    return _project_root(project_id)


def create_project_workspace(project_id: str) -> Path:
    """为新项目创建私有材料和产物目录。"""

    root = _project_root(project_id)
    root.mkdir(parents=False, exist_ok=False)
    (root / "raw").mkdir()
    (root / "text").mkdir()
    return root


def create_project_output_workspace(project_id: str) -> Path:
    """为资料库选材的新项目创建仅保存编译产物的目录。"""

    root = _project_root(project_id)
    root.mkdir(parents=False, exist_ok=False)
    return root


def delete_project_workspace(project_id: str) -> None:
    """删除一个尚未发布或已明确删除的项目私有目录。"""

    shutil.rmtree(_project_root(project_id))


def store_uploaded_sources(project_id: str, uploads: list[tuple[str, Path]]) -> list[StoredMaterialSource]:
    """把直接上传或 ZIP 中的自然材料单元写入项目私有目录。"""

    project_root = _project_root(project_id)
    if not project_root.is_dir():
        raise ValueError("专家项目目录不存在")

    sources: list[StoredMaterialSource] = []
    seen_paths: set[str] = set()
    for upload_name, upload_path in uploads:
        source_name = _safe_source_path(upload_name)
        if Path(source_name).suffix.lower() == ".zip":
            sources.extend(_extract_archive(project_root, upload_path, seen_paths))
            continue
        sources.append(_store_source(project_root, source_name, upload_path.read_bytes(), seen_paths))

    if not sources:
        raise ValueError("上传内容中没有支持的材料文件")
    return sources


async def parse_material(project_id: str, source: StoredMaterialSource) -> ParsedMaterial:
    """通过现有文档解析入口把一份原始文件转换成完整 Markdown 正文。"""

    project_root = _project_root(project_id)
    raw_path = _resolve_project_path(project_root, source.raw_path)
    markdown = await parse_document(
        str(raw_path),
        params={"image_prefix": f"expert-projects/{project_id}/{source.material_id}"},
    )
    if not markdown.strip():
        raise ValueError("材料未能转换出可读正文")

    relative_text_path = f"text/{source.material_id}.md"
    text_path = _resolve_project_path(project_root, relative_text_path)
    temp_path = text_path.with_name(f".{text_path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temp_path.write_text(markdown, encoding="utf-8")
    os.replace(temp_path, text_path)

    return ParsedMaterial(
        text_path=relative_text_path,
        line_count=len(markdown.splitlines()),
        token_count=count_tokens_approximately(
            [HumanMessage(content=markdown)],
            use_usage_metadata_scaling=False,
        ),
    )


def read_material_text(project_id: str, relative_path: str) -> str:
    """读取当前项目内一份已经转换完成的完整正文。"""

    path = _resolve_project_path(_project_root(project_id), relative_path)
    if not path.is_file():
        raise ValueError("材料正文不存在")
    return path.read_text(encoding="utf-8")


def _extract_archive(
    project_root: Path,
    archive_path: Path,
    seen_paths: set[str],
) -> list[StoredMaterialSource]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) > MAX_ARCHIVE_FILES:
                raise ValueError(f"ZIP 文件数量不能超过 {MAX_ARCHIVE_FILES}")
            if sum(member.file_size for member in members) > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError("ZIP 解压后的内容过大")

            stored = []
            for member in members:
                source_path = _safe_source_path(member.filename)
                if _is_ignored_archive_metadata(source_path):
                    continue
                if _zip_entry_is_symlink(member):
                    raise ValueError(f"ZIP 不允许包含符号链接: {member.filename}")
                stored.append(_store_source(project_root, source_path, archive.read(member), seen_paths))
            return stored
    except zipfile.BadZipFile as exc:
        raise ValueError("上传文件不是有效 ZIP") from exc


def _store_source(
    project_root: Path,
    source_path: str,
    content: bytes,
    seen_paths: set[str],
) -> StoredMaterialSource:
    suffix = Path(source_path).suffix.lower()
    if suffix not in MATERIAL_EXTENSIONS:
        raise ValueError(f"不支持的材料文件类型: {source_path}")
    if source_path.casefold() in seen_paths:
        raise ValueError(f"上传内容包含重复路径: {source_path}")
    seen_paths.add(source_path.casefold())

    material_id = uuid.uuid4().hex
    relative_raw_path = f"raw/{material_id}{suffix}"
    raw_path = _resolve_project_path(project_root, relative_raw_path)
    raw_path.write_bytes(content)
    return StoredMaterialSource(
        material_id=material_id,
        title=Path(source_path).stem,
        source_path=source_path,
        raw_path=relative_raw_path,
        file_type=suffix,
    )


def _project_root(project_id: str) -> Path:
    if not PROJECT_ID_RE.fullmatch(str(project_id or "")):
        raise ValueError("无效的专家项目 ID")
    root = get_expert_projects_root()
    return ensure_within_root((root / project_id).resolve(), root, error_message="专家项目路径越界")


def _resolve_project_path(project_root: Path, relative_path: str) -> Path:
    pure = PurePosixPath(str(relative_path or ""))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ValueError("专家项目文件路径无效")
    target = project_root.joinpath(*pure.parts).resolve()
    return ensure_within_root(target, project_root, error_message="专家项目文件路径越界")


def _safe_source_path(value: str) -> str:
    normalized = str(value or "").replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ValueError(f"上传内容包含不安全路径: {value}")
    return pure.as_posix()


def _is_ignored_archive_metadata(source_path: str) -> bool:
    parts = PurePosixPath(source_path).parts
    return "__MACOSX" in parts or PurePosixPath(source_path).name == ".DS_Store"


def _zip_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


__all__ = [
    "ParsedMaterial",
    "StoredMaterialSource",
    "create_project_output_workspace",
    "create_project_workspace",
    "delete_project_workspace",
    "get_expert_projects_root",
    "get_project_root",
    "parse_material",
    "read_material_text",
    "store_uploaded_sources",
]
