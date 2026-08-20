"""资料库来源文件任务恢复与投影测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.material_library import service, worker
from yuxi.material_library.task_view import list_material_file_task_summaries
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.storage.postgres.models_business import Base

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def worker_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """创建恢复扫描使用的共享 SQLite 会话。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'material-worker.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with factory() as db:
            yield db

    monkeypatch.setattr(worker.pg_manager, "get_async_session_context", session_context)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _add_source(
    factory,
    file_id: str,
    status: str,
    progress: int,
    *,
    unit_status: str = "pending",
) -> None:
    async with factory() as db:
        repo = MaterialLibraryRepository(db)
        if await repo.get_library("library-1") is None:
            await repo.create_library(library_id="library-1", name="资料库", created_by="admin-1")
        await repo.create_source_file(
            file_id=file_id,
            library_id="library-1",
            directory_id=None,
            original_filename=f"{file_id}.md",
            relative_path=f"{file_id}.md",
            file_type=".md",
            file_size=10,
            source_bucket="materials",
            source_object=f"material-libraries/library-1/source-files/{file_id}/original/{file_id}.md",
            markdown_bucket="materials" if status == "ready" else None,
            markdown_object=(
                f"material-libraries/library-1/source-files/{file_id}/normalized/content.md"
                if status == "ready"
                else None
            ),
            status=status,
            unit_status=unit_status,
            progress=progress,
            line_count=3 if status == "ready" else 0,
            error_message="解析失败" if status == "needs_attention" else None,
            created_by="admin-1",
        )
        await db.commit()


async def test_stable_job_id_and_recovery_only_requeue_unfinished_files(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for file_id, status, progress in [
        ("pending-file", "pending", 0),
        ("processing-file", "processing", 10),
        ("unit-pending-file", "ready", 100),
        ("unit-processing-file", "ready", 100),
        ("ready-file", "ready", 100),
        ("attention-file", "needs_attention", 10),
    ]:
        unit_status = {
            "unit-processing-file": "processing",
            "ready-file": "ready",
        }.get(file_id, "pending")
        await _add_source(worker_env, file_id, status, progress, unit_status=unit_status)

    jobs: list[tuple[tuple, dict]] = []

    class FakePool:
        async def enqueue_job(self, *args, **kwargs):
            jobs.append((args, kwargs))

    async def fake_get_arq_pool():
        return FakePool()

    monkeypatch.setattr(worker, "get_arq_pool", fake_get_arq_pool)

    await worker.recover_material_source_file_jobs()
    await worker.enqueue_material_source_file("pending-file")

    assert {args[1]: kwargs["_job_id"] for args, kwargs in jobs[:2]} == {
        "pending-file": "material-source:pending-file",
        "processing-file": "material-source:processing-file",
    }
    assert {args[1]: kwargs["_job_id"] for args, kwargs in jobs[2:4]} == {
        "unit-pending-file": "material-units:unit-pending-file",
        "unit-processing-file": "material-units:unit-processing-file",
    }
    assert jobs[-1] == (
        ("process_material_source_file_job", "pending-file"),
        {"_job_id": "material-source:pending-file"},
    )


async def test_enqueue_failures_require_explicit_single_file_retry(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_source(worker_env, "enqueue-failed-file", "pending", 0)
    await _add_source(worker_env, "unit-enqueue-failed-file", "ready", 100)

    async with worker_env() as db:
        await service.mark_material_source_enqueue_failed(
            db,
            file_id="enqueue-failed-file",
            error="来源文件任务投递失败: redis unavailable",
        )
        await service.mark_material_unit_enqueue_failed(
            db,
            file_id="unit-enqueue-failed-file",
            error="材料单元确认任务投递失败: redis unavailable",
        )

    jobs: list[tuple[tuple, dict]] = []

    class FakePool:
        async def enqueue_job(self, *args, **kwargs):
            jobs.append((args, kwargs))

    async def fake_get_arq_pool():
        return FakePool()

    monkeypatch.setattr(worker, "get_arq_pool", fake_get_arq_pool)

    await worker.recover_material_source_file_jobs()

    async with worker_env() as db:
        source_file = await MaterialLibraryRepository(db).get_source_file("enqueue-failed-file")
        unit_source = await MaterialLibraryRepository(db).get_source_file("unit-enqueue-failed-file")
    assert source_file.status == "needs_attention"
    assert source_file.error_message == "来源文件任务投递失败: redis unavailable"
    assert unit_source.unit_status == "needs_attention"
    assert unit_source.error_message == "材料单元确认任务投递失败: redis unavailable"
    assert jobs == []


async def test_source_job_enqueues_boundary_confirmation_after_text_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueued: list[str] = []

    async def fake_process(file_id: str):
        return {"file_id": file_id, "status": "ready", "unit_status": "pending"}

    async def fake_enqueue(file_id: str):
        enqueued.append(file_id)

    monkeypatch.setattr(worker, "process_material_source_file", fake_process)
    monkeypatch.setattr(worker, "enqueue_material_source_units", fake_enqueue)

    result = await worker.process_material_source_file_job({}, "file-1")

    assert result == {"file_id": "file-1", "status": "ready", "unit_status": "pending"}
    assert enqueued == ["file-1"]


async def test_task_projection_uses_source_file_fact(worker_env) -> None:
    await _add_source(worker_env, "ready-file", "ready", 100, unit_status="ready")
    await _add_source(worker_env, "unit-pending-file", "ready", 100, unit_status="pending")
    await _add_source(worker_env, "unit-processing-file", "ready", 100, unit_status="processing")
    await _add_source(worker_env, "unit-attention-file", "ready", 100, unit_status="needs_attention")
    await _add_source(worker_env, "attention-file", "needs_attention", 10)

    async with worker_env() as db:
        tasks = await list_material_file_task_summaries(db)

    by_id = {task["id"]: task for task in tasks}
    assert by_id["material-source:ready-file"]["status"] == "success"
    assert by_id["material-source:ready-file"]["result"] == {"line_count": 3}
    assert by_id["material-source:unit-pending-file"]["status"] == "running"
    assert by_id["material-source:unit-processing-file"]["status"] == "running"
    assert by_id["material-source:unit-attention-file"]["status"] == "failed"
    assert by_id["material-source:unit-attention-file"]["completed_at"] is not None
    assert by_id["material-source:unit-attention-file"]["result"] is None
    assert by_id["material-source:attention-file"]["status"] == "failed"
    assert by_id["material-source:attention-file"]["error"] == "解析失败"
