"""资料库来源文件 ARQ 任务与启动恢复入口。"""

from __future__ import annotations

from yuxi.material_library.service import process_material_source_file
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
    return await process_material_source_file(file_id)


async def recover_material_source_file_jobs() -> None:
    """依据 PostgreSQL 事实重投尚未完成的来源文件。"""

    async with pg_manager.get_async_session_context() as db:
        source_files = await MaterialLibraryRepository(db).list_source_files_by_statuses({"pending", "processing"})
    for source_file in source_files:
        await enqueue_material_source_file(source_file.file_id)


__all__ = [
    "enqueue_material_source_file",
    "process_material_source_file_job",
    "recover_material_source_file_jobs",
]
