"""资料库目录、上传、正文处理与预览用例。"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.config.options import system_options
from yuxi.knowledge.parser.unified import is_supported_file_extension
from yuxi.material_library.boundary import material_boundary_tasks
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.ocr_service import parse_document
from yuxi.storage.minio.client import MinIOClient, get_minio_client
from yuxi.storage.postgres.manager import pg_manager

SOURCE_PENDING_STATUS = "pending"
SOURCE_PROCESSING_STATUS = "processing"
SOURCE_READY_STATUS = "ready"
SOURCE_ATTENTION_STATUS = "needs_attention"
UNIT_PENDING_STATUS = "pending"
UNIT_PROCESSING_STATUS = "processing"
UNIT_READY_STATUS = "ready"
UNIT_ATTENTION_STATUS = "needs_attention"

UNIT_MODE_MODEL_CONFIRM = "model_confirm"
UNIT_MODE_WHOLE_FILE = "whole_file"
UNIT_MODE_CHOICES = frozenset({UNIT_MODE_MODEL_CONFIRM, UNIT_MODE_WHOLE_FILE})


async def create_material_library(db: AsyncSession, *, name: str, created_by: str) -> dict[str, Any]:
    """创建一个空资料库。"""

    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("资料库名称不能为空")
    library = await MaterialLibraryRepository(db).create_library(
        library_id=uuid4().hex,
        name=normalized_name,
        created_by=created_by,
    )
    await db.commit()
    return library.to_dict()


async def list_material_libraries(db: AsyncSession) -> list[dict[str, Any]]:
    """列出管理员可管理的全部资料库。"""

    libraries = await MaterialLibraryRepository(db).list_libraries()
    return [library.to_dict() for library in libraries]


async def get_material_library_detail(db: AsyncSession, *, library_id: str) -> dict[str, Any]:
    """读取资料库、目录树和来源文件事实。"""

    repo = MaterialLibraryRepository(db)
    library = await _require_library(repo, library_id)
    directories = await repo.list_directories(library_id)
    source_files = await repo.list_source_files(library_id)
    return {
        **library.to_dict(),
        "directories": [directory.to_dict() for directory in directories],
        "files": [source_file.to_dict() for source_file in source_files],
    }


async def create_material_directory(
    db: AsyncSession,
    *,
    library_id: str,
    name: str,
    parent_id: str | None,
) -> dict[str, Any]:
    """在资料库中创建一个目录。"""

    directory_name = _normalize_directory_name(name)
    repo = MaterialLibraryRepository(db)
    await _require_library(repo, library_id)

    parent_path = ""
    if parent_id is not None:
        parent = await repo.get_directory(parent_id)
        if parent is None or parent.library_id != library_id:
            raise LookupError("父目录不存在")
        parent_path = parent.relative_path
    relative_path = f"{parent_path}/{directory_name}".strip("/")
    if await repo.get_directory_by_path(library_id, relative_path) is not None:
        raise ValueError("同名目录已存在")

    directory = await repo.create_directory(
        directory_id=uuid4().hex,
        library_id=library_id,
        parent_id=parent_id,
        name=directory_name,
        relative_path=relative_path,
    )
    await db.commit()
    return directory.to_dict()


async def delete_material_directory(db: AsyncSession, *, library_id: str, directory_id: str) -> None:
    """删除一个没有子目录和来源文件的目录。"""

    repo = MaterialLibraryRepository(db)
    directory = await repo.get_directory(directory_id)
    if directory is None or directory.library_id != library_id:
        raise LookupError("目录不存在")
    if not await repo.directory_is_empty(directory_id):
        raise ValueError("目录非空，不能删除")
    await repo.delete_directory(directory_id)
    await db.commit()


async def create_material_source_file(
    db: AsyncSession,
    *,
    library_id: str,
    relative_path: str,
    filename: str,
    content: bytes,
    created_by: str,
    minio_client: MinIOClient | None = None,
    unit_mode: str = UNIT_MODE_MODEL_CONFIRM,
) -> dict[str, Any]:
    """保存一份原始文件并建立等待处理的 PostgreSQL 事实。"""

    if unit_mode not in UNIT_MODE_CHOICES:
        raise ValueError(f"未知的单元确认方式: {unit_mode}")

    normalized_path = _normalize_source_relative_path(relative_path or filename)
    original_filename = PurePosixPath(normalized_path).name
    if not is_supported_file_extension(original_filename):
        raise ValueError(f"不支持的文件类型: {PurePosixPath(original_filename).suffix.lower()}")

    repo = MaterialLibraryRepository(db)
    await _require_library(repo, library_id)
    if await repo.get_source_file_by_path(library_id, normalized_path) is not None:
        raise ValueError("同一路径的来源文件已存在")

    directory = await _ensure_directory_path(repo, library_id, PurePosixPath(normalized_path).parent)
    file_id = uuid4().hex
    object_prefix = source_file_object_prefix(library_id, file_id)
    source_object = f"{object_prefix}/original/{original_filename}"
    bucket_name = MinIOClient.MATERIAL_BUCKETS["files"]
    client = minio_client or get_minio_client()
    await client.aupload_file(bucket_name=bucket_name, object_name=source_object, data=content)

    source_file = await repo.create_source_file(
        file_id=file_id,
        library_id=library_id,
        directory_id=directory.directory_id if directory is not None else None,
        original_filename=original_filename,
        relative_path=normalized_path,
        file_type=PurePosixPath(original_filename).suffix.lower(),
        file_size=len(content),
        source_bucket=bucket_name,
        source_object=source_object,
        status=SOURCE_PENDING_STATUS,
        unit_status=UNIT_PENDING_STATUS,
        unit_mode=unit_mode,
        progress=0,
        line_count=0,
        error_message=None,
        created_by=created_by,
    )
    await db.commit()
    return source_file.to_dict()


async def list_material_source_files(db: AsyncSession, *, library_id: str) -> list[dict[str, Any]]:
    """按相对路径列出资料库来源文件。"""

    repo = MaterialLibraryRepository(db)
    await _require_library(repo, library_id)
    source_files = await repo.list_source_files(library_id)
    source_files.sort(key=lambda item: (item.relative_path, item.file_id))
    return [source_file.to_dict() for source_file in source_files]


async def get_material_source_text(db: AsyncSession, *, library_id: str, file_id: str) -> dict[str, Any]:
    """读取来源文件的完整规范化 Markdown。"""

    source_file = await _require_source_file(MaterialLibraryRepository(db), library_id, file_id)
    if source_file.status != SOURCE_READY_STATUS or not source_file.markdown_bucket or not source_file.markdown_object:
        raise ValueError("来源文件正文尚未就绪")
    markdown = await get_minio_client().adownload_file(source_file.markdown_bucket, source_file.markdown_object)
    return {
        "file_id": source_file.file_id,
        "relative_path": source_file.relative_path,
        "line_count": source_file.line_count,
        "content": markdown.decode("utf-8"),
    }


async def list_material_units(
    db: AsyncSession,
    *,
    library_id: str,
    file_id: str | None = None,
) -> list[dict[str, Any]]:
    """列出来源和边界均已就绪的可选材料单元。"""

    repo = MaterialLibraryRepository(db)
    await _require_library(repo, library_id)
    if file_id is not None:
        await _require_source_file(repo, library_id, file_id)
    rows = await repo.list_ready_material_units(library_id, file_id=file_id)
    return [
        {
            **unit.to_dict(),
            "source_filename": source_file.original_filename,
            "source_path": source_file.relative_path,
        }
        for unit, source_file in rows
    ]


async def get_material_unit_text(db: AsyncSession, *, library_id: str, unit_id: str) -> dict[str, Any]:
    """按来源行范围读取一个已就绪单元的完整 Markdown 正文。"""

    repo = MaterialLibraryRepository(db)
    unit = await repo.get_material_unit(unit_id)
    if unit is None:
        raise LookupError("材料单元不存在")
    source_file = await _require_source_file(repo, library_id, unit.file_id)
    if (
        unit.status != UNIT_READY_STATUS
        or source_file.status != SOURCE_READY_STATUS
        or source_file.unit_status != UNIT_READY_STATUS
        or not source_file.markdown_bucket
        or not source_file.markdown_object
    ):
        raise ValueError("材料单元尚未就绪")

    markdown = await get_minio_client().adownload_file(source_file.markdown_bucket, source_file.markdown_object)
    lines = markdown.decode("utf-8").splitlines(keepends=True)
    if unit.end_line > len(lines):
        raise ValueError("材料单元来源行范围已失效")
    return {
        **unit.to_dict(),
        "source_filename": source_file.original_filename,
        "source_path": source_file.relative_path,
        "content": "".join(lines[unit.start_line - 1 : unit.end_line]),
    }


async def reset_material_source_file(db: AsyncSession, *, library_id: str, file_id: str) -> dict[str, Any]:
    """把一份需要处理的来源文件重置为等待处理。"""

    repo = MaterialLibraryRepository(db)
    source_file = await _require_source_file(repo, library_id, file_id, for_update=True)
    if source_file.status != SOURCE_ATTENTION_STATUS:
        raise ValueError("只有需要处理的来源文件可以重试")
    if await repo.source_file_has_expert_references(file_id):
        raise ValueError("来源文件已被专家项目引用，不能重新处理")
    source_file = await repo.update_source_file(
        file_id,
        status=SOURCE_PENDING_STATUS,
        unit_status=UNIT_PENDING_STATUS,
        progress=0,
        error_message=None,
        markdown_bucket=None,
        markdown_object=None,
        line_count=0,
    )
    await db.commit()
    return source_file.to_dict()


async def reset_material_source_units(db: AsyncSession, *, library_id: str, file_id: str) -> dict[str, Any]:
    """只清除并重跑一份正文已经就绪的边界确认结果。"""

    repo = MaterialLibraryRepository(db)
    source_file = await _require_source_file(repo, library_id, file_id, for_update=True)
    if source_file.status != SOURCE_READY_STATUS or source_file.unit_status not in {
        UNIT_PENDING_STATUS,
        UNIT_READY_STATUS,
        UNIT_ATTENTION_STATUS,
    }:
        raise ValueError("只有正文就绪且边界确认未在处理的来源文件可以重跑")
    if not source_file.markdown_bucket or not source_file.markdown_object:
        raise ValueError("来源文件规范化 Markdown 不存在")
    if await repo.source_file_has_expert_references(file_id):
        raise ValueError("来源文件已被专家项目引用，不能重新确认材料单元")

    await repo.delete_material_units(file_id)
    source_file = await repo.update_source_file(
        file_id,
        unit_status=UNIT_PENDING_STATUS,
        error_message=None,
    )
    await db.commit()
    return source_file.to_dict()


async def mark_material_source_enqueue_failed(db: AsyncSession, *, file_id: str, error: str) -> dict[str, Any]:
    """记录来源文件投递失败，让用户可以显式重试这一份文件。"""

    source_file = await MaterialLibraryRepository(db).update_source_file(
        file_id,
        status=SOURCE_ATTENTION_STATUS,
        error_message=error,
    )
    await db.commit()
    return source_file.to_dict()


async def mark_material_unit_enqueue_failed(db: AsyncSession, *, file_id: str, error: str) -> None:
    """记录单元确认投递失败，让用户可以显式重试边界确认。"""

    await MaterialLibraryRepository(db).update_source_file(
        file_id,
        unit_status=UNIT_ATTENTION_STATUS,
        error_message=error,
    )
    await db.commit()


async def delete_material_source_file(
    db: AsyncSession,
    *,
    library_id: str,
    file_id: str,
    minio_client: MinIOClient | None = None,
) -> None:
    """删除一份当前没有下游引用的资料库来源文件。"""

    repo = MaterialLibraryRepository(db)
    source_file = await _require_source_file(repo, library_id, file_id, for_update=True)
    if source_file.status == SOURCE_PROCESSING_STATUS or source_file.unit_status == UNIT_PROCESSING_STATUS:
        raise ValueError("来源文件正在处理，不能删除")
    if await repo.source_file_has_expert_references(file_id):
        raise ValueError("来源文件已被专家项目引用，不能删除")

    prefix = source_file_object_prefix(library_id, file_id)
    client = minio_client or get_minio_client()
    await client.adelete_objects_by_prefix(MinIOClient.MATERIAL_BUCKETS["files"], prefix)
    await client.adelete_objects_by_prefix(MinIOClient.MATERIAL_BUCKETS["images"], prefix)
    await repo.delete_source_file(file_id)
    await db.commit()


async def process_material_source_file(file_id: str) -> dict[str, Any]:
    """解析一份来源文件并保存完整规范化 Markdown。"""

    async with pg_manager.get_async_session_context() as db:
        repo = MaterialLibraryRepository(db)
        source_file = await repo.get_source_file(file_id)
        if source_file is None:
            return {"file_id": file_id, "ignored": True}
        if source_file.status == SOURCE_READY_STATUS:
            return {
                "file_id": file_id,
                "status": SOURCE_READY_STATUS,
                "unit_status": source_file.unit_status,
            }
        if source_file.status not in {SOURCE_PENDING_STATUS, SOURCE_PROCESSING_STATUS}:
            return {"file_id": file_id, "ignored": True}
        source_file = await repo.update_source_file(
            file_id,
            status=SOURCE_PROCESSING_STATUS,
            progress=10,
            error_message=None,
        )
        await db.commit()
        snapshot = {
            "library_id": source_file.library_id,
            "source_bucket": source_file.source_bucket,
            "source_object": source_file.source_object,
            "relative_path": source_file.relative_path,
            "unit_mode": source_file.unit_mode,
        }

    try:
        source_url = _minio_source(snapshot["source_bucket"], snapshot["source_object"])
        object_prefix = source_file_object_prefix(snapshot["library_id"], file_id)
        markdown = await parse_document(
            source_url,
            params={
                "image_bucket": MinIOClient.MATERIAL_BUCKETS["images"],
                "image_prefix": f"{object_prefix}/images",
            },
        )
        normalized_markdown = normalize_material_markdown(markdown)
        if not normalized_markdown.strip():
            raise ValueError("文档解析结果为空")

        markdown_object = f"{object_prefix}/normalized/content.md"
        markdown_bucket = MinIOClient.MATERIAL_BUCKETS["files"]
        await get_minio_client().aupload_file(
            bucket_name=markdown_bucket,
            object_name=markdown_object,
            data=normalized_markdown.encode("utf-8"),
            content_type="text/markdown",
        )

        line_count = len(normalized_markdown.splitlines())
        whole_file_mode = snapshot["unit_mode"] == UNIT_MODE_WHOLE_FILE

        async with pg_manager.get_async_session_context() as db:
            repo = MaterialLibraryRepository(db)
            if whole_file_mode:
                # 跳过模型确认：整份文件作为一个自然单元直接就绪。
                await repo.create_material_units(
                    file_id,
                    [
                        {
                            "unit_id": uuid4().hex,
                            "title": PurePosixPath(snapshot["relative_path"]).stem
                            or snapshot["relative_path"],
                            "start_line": 1,
                            "end_line": line_count,
                            "status": UNIT_READY_STATUS,
                        }
                    ],
                )
            source_file = await repo.update_source_file(
                file_id,
                status=SOURCE_READY_STATUS,
                unit_status=UNIT_READY_STATUS if whole_file_mode else UNIT_PENDING_STATUS,
                progress=100,
                markdown_bucket=markdown_bucket,
                markdown_object=markdown_object,
                line_count=line_count,
                error_message=None,
            )
            await db.commit()
        return {
            "file_id": file_id,
            "status": source_file.status,
            "unit_status": source_file.unit_status,
        }
    except Exception as exc:
        async with pg_manager.get_async_session_context() as db:
            source_file = await MaterialLibraryRepository(db).update_source_file(
                file_id,
                status=SOURCE_ATTENTION_STATUS,
                error_message=str(exc),
            )
            await db.commit()
        return {"file_id": file_id, "status": source_file.status, "error": source_file.error_message}


async def process_material_source_units(file_id: str) -> dict[str, Any]:
    """确认一份就绪正文的自然主题边界并保存最终材料单元。"""

    async with pg_manager.get_async_session_context() as db:
        repo = MaterialLibraryRepository(db)
        source_file = await repo.get_source_file(file_id)
        if source_file is None:
            return {"file_id": file_id, "ignored": True}
        if source_file.status != SOURCE_READY_STATUS:
            return {"file_id": file_id, "ignored": True}
        if source_file.unit_status == UNIT_READY_STATUS:
            return {"file_id": file_id, "unit_status": UNIT_READY_STATUS}
        if source_file.unit_status not in {UNIT_PENDING_STATUS, UNIT_PROCESSING_STATUS}:
            return {"file_id": file_id, "ignored": True}
        if not source_file.markdown_bucket or not source_file.markdown_object:
            source_file = await repo.update_source_file(
                file_id,
                unit_status=UNIT_ATTENTION_STATUS,
                error_message="来源文件规范化 Markdown 不存在",
            )
            await db.commit()
            return {
                "file_id": file_id,
                "unit_status": source_file.unit_status,
                "error": source_file.error_message,
            }

        source_file = await repo.update_source_file(
            file_id,
            unit_status=UNIT_PROCESSING_STATUS,
            error_message=None,
        )
        model_spec = str((await system_options.get(db))["default_model"]).strip()
        await db.commit()
        snapshot = {
            "source_path": source_file.relative_path,
            "markdown_bucket": source_file.markdown_bucket,
            "markdown_object": source_file.markdown_object,
            "line_count": source_file.line_count,
            "model_spec": model_spec,
        }

    try:
        markdown_bytes = await get_minio_client().adownload_file(
            snapshot["markdown_bucket"],
            snapshot["markdown_object"],
        )
        markdown = markdown_bytes.decode("utf-8")
        actual_line_count = len(markdown.splitlines())
        if actual_line_count != snapshot["line_count"]:
            raise ValueError("规范化 Markdown 行数与来源文件事实不一致")

        units = await material_boundary_tasks.confirm_units(
            model_spec=snapshot["model_spec"],
            source_path=snapshot["source_path"],
            markdown=markdown,
        )

        async with pg_manager.get_async_session_context() as db:
            repo = MaterialLibraryRepository(db)
            await repo.create_material_units(
                file_id,
                [
                    {
                        "unit_id": uuid4().hex,
                        "title": unit.title,
                        "start_line": unit.start_line,
                        "end_line": unit.end_line,
                        "status": UNIT_READY_STATUS,
                    }
                    for unit in units
                ],
            )
            source_file = await repo.update_source_file(
                file_id,
                unit_status=UNIT_READY_STATUS,
                error_message=None,
            )
            await db.commit()
        return {"file_id": file_id, "unit_status": source_file.unit_status, "unit_count": len(units)}
    except Exception as exc:
        async with pg_manager.get_async_session_context() as db:
            source_file = await MaterialLibraryRepository(db).update_source_file(
                file_id,
                unit_status=UNIT_ATTENTION_STATUS,
                error_message=str(exc),
            )
            await db.commit()
        return {
            "file_id": file_id,
            "unit_status": source_file.unit_status,
            "error": source_file.error_message,
        }


def source_file_object_prefix(library_id: str, file_id: str) -> str:
    """返回一份来源文件全部 MinIO 产物的稳定对象前缀。"""

    return f"material-libraries/{library_id}/source-files/{file_id}"


def normalize_material_markdown(markdown: str) -> str:
    """只统一换行和文末空白，保留正文内容与结构。"""

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    return f"{normalized}\n" if normalized else ""


async def _ensure_directory_path(
    repo: MaterialLibraryRepository,
    library_id: str,
    path: PurePosixPath,
):
    if str(path) in {"", "."}:
        return None

    parent = None
    parts: list[str] = []
    for name in path.parts:
        parts.append(name)
        relative_path = "/".join(parts)
        directory = await repo.ensure_directory(
            directory_id=uuid4().hex,
            library_id=library_id,
            parent_id=parent.directory_id if parent is not None else None,
            name=name,
            relative_path=relative_path,
        )
        parent = directory
    return parent


async def _require_library(repo: MaterialLibraryRepository, library_id: str):
    library = await repo.get_library(library_id)
    if library is None:
        raise LookupError("资料库不存在")
    return library


async def _require_source_file(
    repo: MaterialLibraryRepository,
    library_id: str,
    file_id: str,
    *,
    for_update: bool = False,
):
    """读取资料库来源文件，并在写入边界按需锁定。"""

    source_file = await repo.lock_source_file(file_id) if for_update else await repo.get_source_file(file_id)
    if source_file is None or source_file.library_id != library_id:
        raise LookupError("来源文件不存在")
    return source_file


def _normalize_directory_name(name: str) -> str:
    normalized = name.strip()
    if not normalized or normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise ValueError("目录名称非法")
    return normalized


def _normalize_source_relative_path(relative_path: str) -> str:
    normalized = relative_path.strip().replace("\\", "/")
    raw_parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if not raw_parts or normalized.startswith("/") or ".." in raw_parts:
        raise ValueError("来源文件相对路径非法")
    return "/".join(raw_parts)


def _minio_source(bucket_name: str, object_name: str) -> str:
    return f"minio://{bucket_name}/{quote(object_name, safe='/')}"


__all__ = [
    "SOURCE_ATTENTION_STATUS",
    "SOURCE_PENDING_STATUS",
    "SOURCE_PROCESSING_STATUS",
    "SOURCE_READY_STATUS",
    "UNIT_ATTENTION_STATUS",
    "UNIT_PENDING_STATUS",
    "UNIT_PROCESSING_STATUS",
    "UNIT_READY_STATUS",
    "create_material_directory",
    "create_material_library",
    "create_material_source_file",
    "delete_material_directory",
    "delete_material_source_file",
    "get_material_library_detail",
    "get_material_source_text",
    "get_material_unit_text",
    "list_material_libraries",
    "list_material_source_files",
    "list_material_units",
    "mark_material_source_enqueue_failed",
    "mark_material_unit_enqueue_failed",
    "normalize_material_markdown",
    "process_material_source_file",
    "process_material_source_units",
    "reset_material_source_file",
    "reset_material_source_units",
    "source_file_object_prefix",
]
