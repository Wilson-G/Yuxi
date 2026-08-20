"""专家项目 ARQ 恢复测试。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from arq import Retry
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.expert import worker
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.services import task_service
from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def worker_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """创建恢复扫描使用的共享 SQLite 会话。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'expert-worker.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(worker.pg_manager, "get_async_session_context", session_context)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _add_project(
    factory,
    project_id: str,
    *,
    stage: str,
    status: str,
    method_version: int,
    organization_version: int = 0,
    method_confirmed: bool = False,
) -> None:
    async with factory() as db:
        project = await ExpertRepository(db).create_project(
            project_id=project_id,
            name=project_id,
            description="恢复测试",
            model_spec="provider:model",
            created_by="user-1",
            stage=stage,
            status=status,
        )
        project.method_version = method_version
        project.organization_version = organization_version
        if method_confirmed:
            project.method_confirmed_at = utc_now_naive()
            project.method_calibration_completed_at = utc_now_naive()
            project.method_calibration_baseline = [{"material_alias": "M001"}]
        await db.commit()


async def _add_topic(factory, project_id: str, *, topic_id: str, status: str) -> None:
    async with factory() as db:
        await ExpertRepository(db).replace_topics(
            project_id,
            1,
            [
                {
                    "topic_id": topic_id,
                    "topic_key": topic_id,
                    "title": f"主题 {topic_id}",
                    "section": "测试分区",
                    "reader_question": "应该怎样判断？",
                    "purpose": "验证中断后的重跑入口",
                    "boundaries": "资料不足时停止",
                    "position": 1,
                    "material_ids": [],
                    "evidence_requirements": [],
                    "is_sample": False,
                    "status": status,
                }
            ],
        )
        await db.commit()


@pytest.mark.asyncio
async def test_recovery_only_requeues_unfinished_project_facts_without_tasker(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = [
        ("prepare", "orientation", "preparing", 0),
        ("orient", "orientation", "running", 2),
        ("calibration", "orientation", "running", 2, True),
        ("orientation-without-version", "orientation", "running", 0),
        ("ready", "orientation", "ready", 0),
        ("awaiting", "orientation", "awaiting_confirmation", 1),
        ("learning", "material_learning", "running", 3),
        ("learning-attention", "material_learning", "needs_attention", 3),
    ]
    for project_spec in specs:
        project_id, stage, status, method_version, *flags = project_spec
        await _add_project(
            worker_env,
            project_id,
            stage=stage,
            status=status,
            method_version=method_version,
            method_confirmed=bool(flags and flags[0]),
        )

    jobs: list[tuple[tuple, dict]] = []

    class FakePool:
        async def enqueue_job(self, *args, **kwargs) -> None:
            jobs.append((args, kwargs))

    async def fake_get_arq_pool():
        return FakePool()

    async def forbidden_tasker_enqueue(*_args, **_kwargs):
        raise AssertionError("专家项目恢复不能调用 API 进程内 Tasker")

    monkeypatch.setattr(worker, "get_arq_pool", fake_get_arq_pool)
    monkeypatch.setattr(task_service.tasker, "enqueue", forbidden_tasker_enqueue)

    await worker.recover_expert_project_jobs()

    assert jobs == [
        (
            ("process_expert_project_preparation", "prepare"),
            {"_job_id": "expert:prepare:prepare:recovery"},
        ),
        (
            ("process_expert_project_orientation", "orient", 2),
            {"_job_id": "expert:orientation:orient:v2"},
        ),
        (
            ("process_expert_method_calibration", "calibration", 2),
            {"_job_id": "expert:method-calibration:calibration:v2"},
        ),
        (
            ("process_expert_material_learning", "learning", 3),
            {"_job_id": "expert:materials:learning:v3:recovery"},
        ),
    ]


@pytest.mark.asyncio
async def test_organization_recovery_uses_a_new_job_id_after_interruption(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker 重启后不能复用已经耗尽重试次数的组织任务 ID。"""

    await _add_project(
        worker_env,
        "organization",
        stage="organization",
        status="running",
        method_version=1,
        organization_version=2,
    )
    jobs: list[tuple[tuple, dict]] = []

    class FakePool:
        async def enqueue_job(self, *args, **kwargs) -> None:
            jobs.append((args, kwargs))

    async def fake_get_arq_pool():
        return FakePool()

    monkeypatch.setattr(worker, "get_arq_pool", fake_get_arq_pool)

    await worker.recover_expert_project_jobs()

    assert jobs[0][0] == ("process_expert_organization", "organization", 2)
    assert jobs[0][1]["_job_id"].startswith("expert:organization:organization:v2:recovery-")


@pytest.mark.asyncio
async def test_method_calibration_queues_material_learning_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """方法校准进入批量学习后，Worker 必须投递材料学习任务。"""

    async def finish_calibration(project_id: str, method_version: int) -> dict:
        return {"project_id": project_id, "method_version": method_version, "status": "running"}

    queued: list[tuple[str, int]] = []

    async def queue_material_learning(project_id: str, method_version: int) -> None:
        queued.append((project_id, method_version))

    monkeypatch.setattr(worker, "run_method_calibration", finish_calibration)
    monkeypatch.setattr(worker, "enqueue_material_learning", queue_material_learning)

    result = await worker.process_expert_method_calibration({"job_try": 1}, "calibrated-project", 2)

    assert result == {"project_id": "calibrated-project", "method_version": 2, "status": "running"}
    assert queued == [("calibrated-project", 2)]


@pytest.mark.asyncio
async def test_duplicate_project_runners_wait_for_the_active_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    call_count = 0
    active = 0
    max_active = 0

    async def fake_run_material_learning_stage(project_id: str, method_version: int) -> dict:
        nonlocal active, call_count, max_active
        assert project_id == "serialized-project"
        assert method_version == 1
        call_count += 1
        active += 1
        max_active = max(max_active, active)
        if call_count == 1:
            first_started.set()
            await release_first.wait()
        active -= 1
        return {"project_id": project_id, "status": "needs_attention"}

    monkeypatch.setattr(worker, "run_material_learning_stage", fake_run_material_learning_stage)

    first = asyncio.create_task(worker.process_expert_material_learning({"job_try": 1}, "serialized-project", 1))
    await first_started.wait()
    second = asyncio.create_task(worker.process_expert_material_learning({"job_try": 1}, "serialized-project", 1))
    await asyncio.sleep(0)
    calls_before_release = call_count
    release_first.set()
    await asyncio.gather(first, second)

    assert calls_before_release == 1
    assert call_count == 2
    assert max_active == 1


@pytest.mark.asyncio
async def test_material_stage_outer_failure_has_finite_arq_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    attention: list[tuple[str, str]] = []

    async def fail_material_stage(project_id: str, method_version: int) -> dict:
        del project_id, method_version
        raise RuntimeError("材料阶段数据库连接中断")

    async def record_attention(project_id: str, error: str) -> None:
        attention.append((project_id, error))

    monkeypatch.setattr(worker, "run_material_learning_stage", fail_material_stage)
    monkeypatch.setattr(worker, "mark_project_attention", record_attention)

    with pytest.raises(Retry):
        await worker.process_expert_material_learning({"job_try": 1}, "material-retry-project", 1)
    assert attention == []

    with pytest.raises(RuntimeError, match="数据库连接中断"):
        await worker.process_expert_material_learning(
            {"job_try": worker.SYSTEM_TASK_MAX_TRIES},
            "material-retry-project",
            1,
        )
    assert attention == [("material-retry-project", "材料阶段数据库连接中断")]


@pytest.mark.asyncio
@pytest.mark.parametrize("topic_status", ["pending", "preparing_evidence", "evidence_ready", "writing"])
async def test_handout_outer_failure_marks_unfinished_topic_actionable(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
    topic_status: str,
) -> None:
    project_id = f"handout-retry-{topic_status}"
    topic_id = f"{topic_status}-topic"
    await _add_project(
        worker_env,
        project_id,
        stage="handout_compilation",
        status="running",
        method_version=1,
        organization_version=1,
    )
    await _add_topic(worker_env, project_id, topic_id=topic_id, status=topic_status)

    async def fail_handout_stage(_project_id: str, _organization_version: int) -> dict:
        raise RuntimeError("讲义模型连接中断")

    monkeypatch.setattr(worker, "run_handout_compilation", fail_handout_stage)

    with pytest.raises(RuntimeError, match="讲义模型连接中断"):
        await worker.process_expert_handout_compilation(
            {"job_try": worker.SYSTEM_TASK_MAX_TRIES},
            project_id,
            1,
        )

    async with worker_env() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        topics = await repo.list_topics(project_id, 1)
    assert project is not None and project.status == "needs_attention"
    assert project.error_message == "讲义模型连接中断"
    assert [(topic.topic_id, topic.status, topic.error_message) for topic in topics] == [
        (topic_id, "needs_attention", "讲义模型连接中断")
    ]


@pytest.mark.asyncio
async def test_handout_outer_failure_keeps_passed_topics_for_book_retry(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "handout-book-retry"
    topic_id = "passed-topic"
    await _add_project(
        worker_env,
        project_id,
        stage="handout_compilation",
        status="running",
        method_version=1,
        organization_version=1,
    )
    await _add_topic(worker_env, project_id, topic_id=topic_id, status="passed")

    async def fail_book_stage(_project_id: str, _organization_version: int) -> dict:
        raise RuntimeError("全书收口模型连接中断")

    monkeypatch.setattr(worker, "run_handout_compilation", fail_book_stage)

    with pytest.raises(RuntimeError, match="全书收口模型连接中断"):
        await worker.process_expert_handout_compilation(
            {"job_try": worker.SYSTEM_TASK_MAX_TRIES},
            project_id,
            1,
        )

    async with worker_env() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        topics = await repo.list_topics(project_id, 1)
    assert project is not None and project.status == "needs_attention"
    assert project.error_message == "全书收口模型连接中断"
    assert [(topic.topic_id, topic.status, topic.error_message) for topic in topics] == [(topic_id, "passed", None)]


@pytest.mark.asyncio
async def test_stale_handout_job_is_ignored_without_touching_finished_project(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "finished-handout-project"
    await _add_project(
        worker_env,
        project_id,
        stage="release",
        status="ready",
        method_version=1,
        organization_version=1,
    )

    async def forbidden_handout_stage(_project_id: str, _organization_version: int) -> dict:
        raise AssertionError("过期的重复 job 不应重新进入讲义编译")

    monkeypatch.setattr(worker, "run_handout_compilation", forbidden_handout_stage)

    result = await worker.process_expert_handout_compilation({"job_try": 1}, project_id, 1)

    async with worker_env() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert result == {"project_id": project_id, "ignored": True}
    assert project is not None and project.status == "ready"
    assert project.error_message is None
