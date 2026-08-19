"""把资料库来源文件事实投影为任务中心只读摘要。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.material_library_repository import MaterialLibraryRepository


async def list_material_file_task_summaries(db: AsyncSession) -> list[dict[str, Any]]:
    """列出全部来源文件的任务中心摘要。"""

    source_files = await MaterialLibraryRepository(db).list_source_files()
    return [_build_task(source_file) for source_file in source_files]


async def get_material_file_task_summary(db: AsyncSession, file_id: str) -> dict[str, Any] | None:
    """按来源文件 ID 读取一条任务中心摘要。"""

    source_file = await MaterialLibraryRepository(db).get_source_file(file_id)
    return _build_task(source_file) if source_file is not None else None


def _build_task(source_file: Any) -> dict[str, Any]:
    item = source_file.to_dict()
    return {
        "id": f"material-source:{source_file.file_id}",
        "name": f"资料处理：{source_file.original_filename}",
        "type": "material_source_file",
        "status": _task_status(source_file.status, source_file.unit_status),
        "progress": float(source_file.progress),
        "message": _task_message(source_file.status, source_file.unit_status),
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
        "started_at": item["created_at"],
        "completed_at": item["updated_at"]
        if _task_status(source_file.status, source_file.unit_status) in {"success", "failed"}
        else None,
        "payload": {
            "library_id": source_file.library_id,
            "file_id": source_file.file_id,
            "source_status": source_file.status,
            "unit_status": source_file.unit_status,
            "relative_path": source_file.relative_path,
        },
        "result": {"line_count": source_file.line_count}
        if _task_status(source_file.status, source_file.unit_status) == "success"
        else None,
        "error": source_file.error_message,
        "cancel_requested": False,
    }


def _task_status(source_status: str, unit_status: str) -> str:
    if source_status == "ready" and unit_status == "needs_attention":
        return "failed"
    if source_status == "ready" and unit_status != "ready":
        return "running"
    if source_status == "needs_attention":
        return "failed"
    if source_status == "ready":
        return "success"
    return "running"


def _task_message(source_status: str, unit_status: str) -> str:
    if source_status == "ready":
        return {
            "pending": "正文就绪，等待材料单元确认",
            "processing": "正在确认材料单元",
            "needs_attention": "材料单元需要处理",
            "ready": "正文和材料单元就绪",
        }.get(unit_status, unit_status)
    return {
        "pending": "等待处理",
        "processing": "正在生成完整正文",
        "needs_attention": "需要处理",
        "ready": "正文就绪",
    }.get(source_status, source_status)


__all__ = ["get_material_file_task_summary", "list_material_file_task_summaries"]
