"""资料库来源文件 ARQ 任务与启动恢复入口。"""

from __future__ import annotations

from yuxi.material_library.service import (
    mark_material_unit_enqueue_failed,
    process_material_source_file,
    process_material_source_units,
)
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.postgres.manager import pg_manager


async def enqueue_material_source_file(file_id: str) -> None:
    """使用来源文件稳定标识提交解析任务。"""

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_material_source_file_job",
        file_id,
        _job_id=f"material-source:{file_id}",
    )


async def process_material_source_file_job(ctx, file_id: str) -> dict:
    """在 Worker 中处理一份来源文件。"""

    del ctx
    result = await process_material_source_file(file_id)
    if result.get("status") == "ready" and result.get("unit_status") in {"pending", "processing"}:
        try:
            await enqueue_material_source_units(file_id)
        except Exception as exc:
            async with pg_manager.get_async_session_context() as db:
                await mark_material_unit_enqueue_failed(
                    db,
                    file_id=file_id,
                    error=f"材料单元确认任务投递失败: {exc}",
                )
    return result


async def enqueue_material_source_units(file_id: str) -> None:
    """使用来源文件稳定标识提交自然边界确认任务。"""

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_material_source_units_job",
        file_id,
        _job_id=f"material-units:{file_id}",
    )


async def process_material_source_units_job(ctx, file_id: str) -> dict:
    """在 Worker 中确认并保存一份来源文件的材料单元。"""

    del ctx
    return await process_material_source_units(file_id)


async def recover_material_source_file_jobs() -> None:
    """依据 PostgreSQL 事实重投尚未完成的来源文件。"""

    async with pg_manager.get_async_session_context() as db:
        repo = MaterialLibraryRepository(db)
        source_files = await repo.list_source_files_by_statuses({"pending", "processing"})
        unit_sources = await repo.list_source_files_by_unit_statuses({"pending", "processing"})
    for source_file in source_files:
        await enqueue_material_source_file(source_file.file_id)
    for source_file in unit_sources:
        await enqueue_material_source_units(source_file.file_id)


__all__ = [
    "enqueue_material_source_file",
    "enqueue_material_source_units",
    "process_material_source_file_job",
    "process_material_source_units_job",
    "recover_material_source_file_jobs",
]
