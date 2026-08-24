"""专家项目 ARQ 恢复测试。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from anthropic import APITimeoutError as AnthropicAPITimeoutError
from anthropic import InternalServerError as AnthropicInternalServerError
from anthropic import RateLimitError as AnthropicRateLimitError
from arq import Retry
from google.genai.errors import ClientError as GeminiClientError
from google.genai.errors import ServerError as GeminiServerError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.expert import worker
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.services import task_service
from yuxi.services.run_worker import WorkerSettings
from yuxi.storage.minio.client import StorageError
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
        if stage == "material_learning":
            await ExpertRepository(db).create_materials(
                project_id,
                [
                    {
                        "material_id": f"{project_id}-material",
                        "title": f"{project_id} 材料",
                        "source_path": f"{project_id}.md",
                        "raw_path": f"raw/{project_id}.md",
                        "text_path": f"text/{project_id}.md",
                        "file_type": ".md",
                        "status": "pending",
                        "line_count": 1,
                    }
                ],
            )
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
        ("paused-orientation", "orientation", "paused", 2),
        ("paused-learning", "material_learning", "paused", 3),
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
    for project_id, stage, status in [
        ("organization", "organization", "running"),
        ("organization-awaiting", "organization", "awaiting_confirmation"),
        ("organization-attention", "organization", "needs_attention"),
        ("organization-paused", "organization", "paused"),
        ("handouts", "handout_compilation", "running"),
        ("handouts-attention", "handout_compilation", "needs_attention"),
        ("handouts-paused", "handout_compilation", "paused"),
    ]:
        await _add_project(
            worker_env,
            project_id,
            stage=stage,
            status=status,
            method_version=3,
            organization_version=2,
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
            ("process_expert_material_learning", "learning", "learning-material", 3),
            {"_job_id": "expert:material:learning:learning-material:v3"},
        ),
        (
            ("process_expert_organization", "organization", 2),
            {"_job_id": "expert:organization:organization:v2"},
        ),
        (
            ("process_expert_handout_compilation", "handouts", 2),
            {"_job_id": "expert:handouts:handouts:v2:recovery"},
        ),
    ]


@pytest.mark.asyncio
async def test_explicit_resume_enqueues_current_stage(worker_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """只有显式恢复动作会把暂停后的当前阶段重新投递。"""

    await _add_project(
        worker_env,
        "resumed-learning",
        stage="material_learning",
        status="running",
        method_version=4,
    )
    jobs: list[tuple[tuple, dict]] = []

    class FakePool:
        async def enqueue_job(self, *args, **kwargs) -> None:
            jobs.append((args, kwargs))

    async def fake_get_arq_pool():
        return FakePool()

    monkeypatch.setattr(worker, "get_arq_pool", fake_get_arq_pool)

    await worker.enqueue_resumed_expert_project("resumed-learning")

    assert jobs == [
        (
            (
                "process_expert_material_learning",
                "resumed-learning",
                "resumed-learning-material",
                4,
            ),
            {"_job_id": "expert:material:resumed-learning:resumed-learning-material:v4"},
        )
    ]


@pytest.mark.asyncio
async def test_method_calibration_waits_for_confirmation_without_queueing_materials(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """方法校准结束后等待用户确认，不提前投递材料学习任务。"""

    await _add_project(
        worker_env,
        "calibrated-project",
        stage="orientation",
        status="awaiting_confirmation",
        method_version=2,
    )

    async def finish_calibration(project_id: str, method_version: int) -> dict:
        return {"project_id": project_id, "method_version": method_version, "status": "awaiting_confirmation"}

    queued: list[tuple[str, str, int]] = []

    async def queue_material_learning(project_id: str, material_id: str, method_version: int) -> None:
        queued.append((project_id, material_id, method_version))

    monkeypatch.setattr(worker, "run_method_calibration", finish_calibration)
    monkeypatch.setattr(worker, "enqueue_material_learning", queue_material_learning)

    result = await worker.process_expert_method_calibration({"job_try": 1}, "calibrated-project", 2)

    assert result == {
        "project_id": "calibrated-project",
        "method_version": 2,
        "status": "awaiting_confirmation",
    }
    assert queued == []


@pytest.mark.asyncio
async def test_method_calibration_retry_queues_materials_from_committed_facts_after_runner_is_ignored(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_project(
        worker_env,
        "calibration-handoff-retry",
        stage="material_learning",
        status="running",
        method_version=2,
    )
    async with worker_env() as db:
        await ExpertRepository(db).update_project(
            "calibration-handoff-retry",
            stage="orientation",
            status="running",
        )
        await db.commit()
    runner_calls = 0
    enqueue_attempts = 0

    async def run_calibration(_project_id: str, _method_version: int) -> dict:
        nonlocal runner_calls
        runner_calls += 1
        if runner_calls == 1:
            async with worker_env() as db:
                await ExpertRepository(db).update_project(
                    "calibration-handoff-retry",
                    stage="material_learning",
                    status="running",
                )
                await db.commit()
            return {"project_id": "calibration-handoff-retry", "status": "running"}
        return {"project_id": "calibration-handoff-retry", "ignored": True}

    async def enqueue_material(_project_id: str, _material_id: str, _method_version: int) -> None:
        nonlocal enqueue_attempts
        enqueue_attempts += 1
        if enqueue_attempts == 1:
            raise ConnectionError("Redis 暂时不可用")

    monkeypatch.setattr(worker, "run_method_calibration", run_calibration)
    monkeypatch.setattr(worker, "enqueue_material_learning", enqueue_material)

    with pytest.raises(Retry):
        await worker.process_expert_method_calibration(
            {"job_try": 1},
            "calibration-handoff-retry",
            2,
        )

    result = await worker.process_expert_method_calibration(
        {"job_try": 2},
        "calibration-handoff-retry",
        2,
    )

    assert result == {"project_id": "calibration-handoff-retry", "ignored": True}
    assert runner_calls == 2
    assert enqueue_attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "project_method_version", "job_method_version"),
    [
        ("awaiting_confirmation", 2, 2),
        ("needs_attention", 2, 2),
        ("paused", 2, 2),
        ("running", 3, 2),
    ],
)
async def test_method_calibration_handoff_respects_project_fact_boundaries(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    project_method_version: int,
    job_method_version: int,
) -> None:
    project_id = f"calibration-boundary-{status}-{project_method_version}"
    await _add_project(
        worker_env,
        project_id,
        stage="material_learning",
        status=status,
        method_version=project_method_version,
    )
    queued: list[str] = []

    async def stale_calibration(_project_id: str, _method_version: int) -> dict:
        return {"project_id": project_id, "ignored": True}

    async def enqueue_material(_project_id: str, material_id: str, _method_version: int) -> None:
        queued.append(material_id)

    monkeypatch.setattr(worker, "run_method_calibration", stale_calibration)
    monkeypatch.setattr(worker, "enqueue_material_learning", enqueue_material)

    await worker.process_expert_method_calibration({"job_try": 1}, project_id, job_method_version)

    assert queued == []


@pytest.mark.asyncio
async def test_independent_material_jobs_do_not_share_project_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    call_count = 0
    active = 0
    max_active = 0

    async def fake_run_material_learning_stage(project_id: str, material_id: str, method_version: int) -> dict:
        nonlocal active, call_count, max_active
        assert project_id == "serialized-project"
        assert material_id in {"material-1", "material-2"}
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

    first = asyncio.create_task(
        worker.process_expert_material_learning({"job_try": 1}, "serialized-project", "material-1", 1)
    )
    await first_started.wait()
    second = asyncio.create_task(
        worker.process_expert_material_learning({"job_try": 1}, "serialized-project", "material-2", 1)
    )
    await asyncio.sleep(0)
    calls_before_release = call_count
    release_first.set()
    await asyncio.gather(first, second)

    assert calls_before_release == 2
    assert call_count == 2
    assert max_active == 2


@pytest.mark.parametrize(
    "job_name",
    ["process_expert_material_learning", "process_expert_organization"],
)
def test_retryable_expert_jobs_do_not_keep_arq_results(job_name: str) -> None:
    job = next(function for function in WorkerSettings.functions if getattr(function, "name", "") == job_name)

    assert job.keep_result_s == 0


@pytest.mark.asyncio
async def test_material_job_retry_finishes_organization_handoff_after_first_enqueue_failure(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_project(
        worker_env,
        "handoff-retry",
        stage="organization",
        status="running",
        method_version=1,
        organization_version=1,
    )
    enqueue_attempts = 0

    async def stale_material_job(_project_id: str, _material_id: str, _method_version: int) -> dict:
        return {"project_id": "handoff-retry", "status": "ignored"}

    async def enqueue_organization(_project_id: str, _organization_version: int) -> None:
        nonlocal enqueue_attempts
        enqueue_attempts += 1
        if enqueue_attempts == 1:
            raise ConnectionError("Redis 暂时不可用")

    monkeypatch.setattr(worker, "run_material_learning_stage", stale_material_job)
    monkeypatch.setattr(worker, "enqueue_organization", enqueue_organization)

    with pytest.raises(Retry):
        await worker.process_expert_material_learning(
            {"job_try": 1},
            "handoff-retry",
            "handoff-retry-material",
            1,
        )

    result = await worker.process_expert_material_learning(
        {"job_try": 2},
        "handoff-retry",
        "handoff-retry-material",
        1,
    )

    assert result["organization_version"] == 1
    assert enqueue_attempts == 2


@pytest.mark.asyncio
async def test_material_job_retry_restarts_handoff_after_initialization_failure(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_project(
        worker_env,
        "handoff-initialization-retry",
        stage="organization",
        status="preparing",
        method_version=1,
    )
    initialization_attempts = 0
    enqueued: list[tuple[str, int]] = []

    async def stale_material_job(_project_id: str, _material_id: str, _method_version: int) -> dict:
        return {"project_id": "handoff-initialization-retry", "status": "ignored"}

    original_initialize = worker.initialize_organization_stage

    async def initialize_with_first_failure(project_id: str) -> int:
        nonlocal initialization_attempts
        initialization_attempts += 1
        if initialization_attempts == 1:
            raise ConnectionError("PostgreSQL 暂时不可用")
        return await original_initialize(project_id)

    async def record_enqueue(project_id: str, organization_version: int) -> None:
        enqueued.append((project_id, organization_version))

    monkeypatch.setattr(worker, "run_material_learning_stage", stale_material_job)
    monkeypatch.setattr(worker, "initialize_organization_stage", initialize_with_first_failure)
    monkeypatch.setattr(worker, "enqueue_organization", record_enqueue)

    with pytest.raises(Retry):
        await worker.process_expert_material_learning(
            {"job_try": 1},
            "handoff-initialization-retry",
            "material-1",
            1,
        )

    result = await worker.process_expert_material_learning(
        {"job_try": 2},
        "handoff-initialization-retry",
        "material-1",
        1,
    )

    assert result["organization_version"] == 1
    assert initialization_attempts == 2
    assert enqueued == [("handoff-initialization-retry", 1)]


@pytest.mark.asyncio
async def test_organization_retry_queues_handouts_from_committed_facts_after_runner_is_ignored(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_project(
        worker_env,
        "organization-handoff-retry",
        stage="organization",
        status="running",
        method_version=1,
        organization_version=2,
    )
    runner_calls = 0
    enqueue_attempts = 0

    async def run_organization(_project_id: str, _organization_version: int) -> dict:
        nonlocal runner_calls
        runner_calls += 1
        async with worker_env() as db:
            await ExpertRepository(db).update_project(
                "organization-handoff-retry",
                stage="handout_compilation",
                status="running",
            )
            await db.commit()
        return {
            "project_id": "organization-handoff-retry",
            "stage": "handout_compilation",
            "status": "running",
        }

    async def enqueue_handouts(_project_id: str, _organization_version: int, *, token: str = "initial") -> None:
        nonlocal enqueue_attempts
        assert token == "initial"
        enqueue_attempts += 1
        if enqueue_attempts == 1:
            raise ConnectionError("Redis 暂时不可用")

    monkeypatch.setattr(worker, "run_organization_stage", run_organization)
    monkeypatch.setattr(worker, "enqueue_handout_compilation", enqueue_handouts)

    with pytest.raises(Retry):
        await worker.process_expert_organization(
            {"job_try": 1},
            "organization-handoff-retry",
            2,
        )

    result = await worker.process_expert_organization(
        {"job_try": 2},
        "organization-handoff-retry",
        2,
    )

    assert result == {"project_id": "organization-handoff-retry", "ignored": True}
    assert runner_calls == 1
    assert enqueue_attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "status", "project_organization_version", "job_organization_version"),
    [
        ("organization", "awaiting_confirmation", 2, 2),
        ("organization", "needs_attention", 2, 2),
        ("organization", "paused", 2, 2),
        ("handout_compilation", "running", 3, 2),
    ],
)
async def test_organization_handoff_respects_project_fact_boundaries(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    status: str,
    project_organization_version: int,
    job_organization_version: int,
) -> None:
    project_id = f"organization-boundary-{status}-{project_organization_version}"
    await _add_project(
        worker_env,
        project_id,
        stage=stage,
        status=status,
        method_version=1,
        organization_version=project_organization_version,
    )
    queued: list[tuple[str, int]] = []

    async def enqueue_handouts(project_id: str, organization_version: int, *, token: str = "initial") -> None:
        assert token == "initial"
        queued.append((project_id, organization_version))

    monkeypatch.setattr(worker, "enqueue_handout_compilation", enqueue_handouts)

    result = await worker.process_expert_organization(
        {"job_try": 1},
        project_id,
        job_organization_version,
    )

    assert result == {"project_id": project_id, "ignored": True}
    assert queued == []


@pytest.mark.asyncio
async def test_material_stage_outer_failure_has_finite_arq_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    attention: list[tuple[str, str, int, str]] = []

    async def fail_material_stage(project_id: str, material_id: str, method_version: int) -> dict:
        del project_id, material_id, method_version
        raise TimeoutError("材料阶段数据库连接中断")

    async def record_attention(project_id: str, material_id: str, method_version: int, error: str) -> None:
        attention.append((project_id, material_id, method_version, error))

    monkeypatch.setattr(worker, "run_material_learning_stage", fail_material_stage)
    monkeypatch.setattr(worker, "mark_material_learning_attention", record_attention)

    with pytest.raises(Retry):
        await worker.process_expert_material_learning({"job_try": 1}, "material-retry-project", "material-1", 1)
    assert attention == []

    with pytest.raises(TimeoutError, match="数据库连接中断"):
        await worker.process_expert_material_learning(
            {"job_try": worker.SYSTEM_TASK_MAX_TRIES},
            "material-retry-project",
            "material-1",
            1,
        )
    assert attention == [("material-retry-project", "material-1", 1, "材料阶段数据库连接中断")]


@pytest.mark.asyncio
async def test_orientation_retry_backs_off_exponentially_then_marks_attention(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连接抖动按 2/4/8/16 秒指数退避重试 4 次；第 5 次失败才标记需要处理。"""

    project_id = "orientation-retry-backoff"
    await _add_project(
        worker_env,
        project_id,
        stage="orientation",
        status="running",
        method_version=1,
    )
    pool = RerollCountingPool()
    _install_reroll_pool(monkeypatch, pool)

    async def fail_orientation(_project_id: str, _method_version: int) -> dict:
        raise ConnectionError("网络抖动连接中断")

    monkeypatch.setattr(worker, "run_project_orientation", fail_orientation)

    for job_try in range(1, worker.SYSTEM_TASK_MAX_RETRIES + 1):
        with pytest.raises(Retry) as exc_info:
            await worker.process_expert_project_orientation({"job_try": job_try}, project_id, 1)
        assert exc_info.value.defer_score == 2 ** job_try * 1000

    with pytest.raises(ConnectionError, match="网络抖动连接中断"):
        await worker.process_expert_project_orientation(
            {"job_try": worker.SYSTEM_TASK_MAX_TRIES}, project_id, 1
        )

    async with worker_env() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None and project.status == "needs_attention"
    assert project.error_message == "网络抖动连接中断"
    # 传输类失败烧尽预算后安排错峰自动重投。
    assert len(pool.jobs) == 1
    assert pool.jobs[0][0][0] == "process_expert_project_orientation_reroll"


@pytest.mark.asyncio
async def test_orientation_value_error_goes_directly_to_attention(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确定性业务失败（ValueError）重试必然复现：首次失败即落需要处理，不再烧重试额度。"""

    project_id = "orientation-value-error-attention"
    await _add_project(
        worker_env,
        project_id,
        stage="orientation",
        status="running",
        method_version=1,
    )

    async def fail_orientation(_project_id: str, _method_version: int) -> dict:
        raise ValueError("项目 Prompt 包缺少阶段内容: skill_author")

    monkeypatch.setattr(worker, "run_project_orientation", fail_orientation)

    with pytest.raises(ValueError, match="缺少阶段内容"):
        await worker.process_expert_project_orientation({"job_try": 1}, project_id, 1)

    async with worker_env() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None and project.status == "needs_attention"
    assert project.error_message == "项目 Prompt 包缺少阶段内容: skill_author"


def _anthropic_status_error(error_type, status_code: int):
    request = httpx.Request("POST", "https://model.example/v1/messages")
    response = httpx.Response(status_code, request=request)
    return error_type("模型服务暂时不可用", response=response, body=None)


@pytest.mark.parametrize(
    "error",
    [
        AnthropicAPITimeoutError(httpx.Request("POST", "https://model.example/v1/messages")),
        _anthropic_status_error(AnthropicRateLimitError, 429),
        _anthropic_status_error(AnthropicInternalServerError, 503),
        GeminiClientError(429, {"error": {"message": "请求限流"}}),
        GeminiServerError(503, {"error": {"message": "服务暂时不可用"}}),
        StorageError("MinIO 暂时不可用", retryable=True),
    ],
)
def test_supported_model_and_minio_transient_errors_are_retryable(error: Exception) -> None:
    assert worker._is_transient_material_error(error) is True


def test_deterministic_minio_error_is_not_retryable() -> None:
    assert worker._is_transient_material_error(StorageError("规范化正文对象不存在")) is False


@pytest.mark.asyncio
async def test_material_contract_failure_goes_directly_to_attention(monkeypatch: pytest.MonkeyPatch) -> None:
    attention: list[tuple[str, str, int, str]] = []

    async def fail_contract(project_id: str, material_id: str, method_version: int) -> dict:
        del project_id, material_id, method_version
        raise ValueError("笔记响应不符合 JSONL 合同")

    async def record_attention(project_id: str, material_id: str, method_version: int, error: str) -> None:
        attention.append((project_id, material_id, method_version, error))

    monkeypatch.setattr(worker, "run_material_learning_stage", fail_contract)
    monkeypatch.setattr(worker, "mark_material_learning_attention", record_attention)

    with pytest.raises(ValueError, match="JSONL 合同"):
        await worker.process_expert_material_learning(
            {"job_try": 1},
            "material-contract-project",
            "material-1",
            2,
        )

    assert attention == [("material-contract-project", "material-1", 2, "笔记响应不符合 JSONL 合同")]


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


class RerollCountingPool:
    """模拟 arq 连接：计数器、TTL 与投递记录。"""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.jobs: list[tuple[tuple, dict]] = []

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.ttls[key] = ttl

    async def enqueue_job(self, *args, **kwargs) -> None:
        self.jobs.append((args, kwargs))


def _install_reroll_pool(monkeypatch: pytest.MonkeyPatch, pool: RerollCountingPool) -> None:
    async def fake_get_arq_pool():
        return pool

    monkeypatch.setattr(worker, "get_arq_pool", fake_get_arq_pool)


async def _set_representatives(factory, project_id: str, reps: list[str]) -> None:
    async with factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        assert project is not None
        project.representative_material_ids = reps
        await db.commit()


@pytest.mark.asyncio
async def test_orientation_auto_reroll_schedules_one_deferred_round(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_project(
        worker_env,
        "reroll-a",
        stage="orientation",
        status="needs_attention",
        method_version=9,
    )
    await _set_representatives(worker_env, "reroll-a", ["m1", "m2", "m3", "m4"])
    pool = RerollCountingPool()
    _install_reroll_pool(monkeypatch, pool)

    await worker._schedule_orientation_auto_reroll("reroll-a")

    assert len(pool.jobs) == 1
    args, kwargs = pool.jobs[0]
    assert args[:3] == ("process_expert_project_orientation_reroll", "reroll-a", 9)
    assert kwargs["_job_id"] == "expert:orientation:reroll-a:reroll:1"
    assert kwargs["_defer_by"].total_seconds() == worker.ORIENTATION_AUTO_REROLL_DEFER_SECONDS
    counter_key = "expert:orientation:auto-reroll:reroll-a"
    assert pool.counters[counter_key] == 1
    assert pool.ttls[counter_key] == worker.ORIENTATION_AUTO_REROLL_COUNTER_TTL_SECONDS


@pytest.mark.asyncio
async def test_orientation_auto_reroll_stops_at_daily_limit(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_project(
        worker_env,
        "reroll-limit",
        stage="orientation",
        status="needs_attention",
        method_version=2,
    )
    pool = RerollCountingPool()
    _install_reroll_pool(monkeypatch, pool)

    for _ in range(worker.ORIENTATION_AUTO_REROLL_LIMIT + 1):
        await worker._schedule_orientation_auto_reroll("reroll-limit")

    assert len(pool.jobs) == worker.ORIENTATION_AUTO_REROLL_LIMIT


@pytest.mark.asyncio
async def test_orientation_reroll_handler_rescues_project_with_stored_samples(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_project(
        worker_env,
        "reroll-run",
        stage="orientation",
        status="needs_attention",
        method_version=9,
    )
    await _set_representatives(worker_env, "reroll-run", ["m1", "m2", "m3", "m4"])
    rescued: dict[str, object] = {}
    enqueued: list[tuple[str, int]] = []

    async def fake_start(db, *, project_id, created_by, representative_material_ids):
        rescued.update(
            id=project_id,
            by=created_by,
            reps=list(representative_material_ids),
        )
        return 10

    async def fake_enqueue(project_id: str, version: int) -> None:
        enqueued.append((project_id, version))

    monkeypatch.setattr(worker, "start_project_orientation", fake_start)
    monkeypatch.setattr(worker, "enqueue_project_orientation", fake_enqueue)

    result = await worker.process_expert_project_orientation_reroll({}, "reroll-run", 9, 1)

    assert result == {"project_id": "reroll-run", "method_version": 10, "reroll": 1}
    assert rescued == {"id": "reroll-run", "by": "user-1", "reps": ["m1", "m2", "m3", "m4"]}
    assert enqueued == [("reroll-run", 10)]


@pytest.mark.asyncio
async def test_orientation_reroll_handler_ignores_projects_moved_on(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """人工已救活、已推进版本或已离开 attention 的项目不再被旧的重投任务触碰。"""

    await _add_project(
        worker_env,
        "reroll-moved",
        stage="orientation",
        status="needs_attention",
        method_version=9,
    )
    await _set_representatives(worker_env, "reroll-moved", ["m1", "m2", "m3", "m4"])

    async def forbidden_start(*_args, **_kwargs):
        raise AssertionError("项目状态已变化时不应再次创建方法版本")

    monkeypatch.setattr(worker, "start_project_orientation", forbidden_start)

    for mutate_status, mutate_version in [("running", 9), ("needs_attention", 10), ("ready", 9)]:
        async with worker_env() as db:
            repo = ExpertRepository(db)
            project = await repo.get_project("reroll-moved")
            assert project is not None
            project.status = mutate_status
            project.method_version = mutate_version
            await db.commit()

        result = await worker.process_expert_project_orientation_reroll({}, "reroll-moved", 9, 1)
        assert result == {"project_id": "reroll-moved", "reroll": 1, "ignored": True}


@pytest.mark.asyncio
async def test_orientation_final_transport_failure_marks_attention_and_schedules_reroll(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "orient-transport"
    await _add_project(
        worker_env,
        project_id,
        stage="orientation",
        status="running",
        method_version=8,
    )
    await _set_representatives(worker_env, project_id, ["m1", "m2", "m3", "m4"])
    pool = RerollCountingPool()
    _install_reroll_pool(monkeypatch, pool)

    async def failing_run(_project_id: str, _method_version: int) -> dict:
        raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")

    monkeypatch.setattr(worker, "run_project_orientation", failing_run)

    with pytest.raises(httpx.RemoteProtocolError):
        await worker.process_expert_project_orientation({"job_try": 5}, project_id, 8)

    async with worker_env() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None and project.status == "needs_attention"
    assert len(pool.jobs) == 1
    assert pool.jobs[0][0][0] == "process_expert_project_orientation_reroll"


@pytest.mark.asyncio
async def test_orientation_business_failure_does_not_auto_reroll(
    worker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "orient-business"
    await _add_project(
        worker_env,
        project_id,
        stage="orientation",
        status="running",
        method_version=8,
    )
    pool = RerollCountingPool()
    _install_reroll_pool(monkeypatch, pool)

    async def failing_run(_project_id: str, _method_version: int) -> dict:
        raise ValueError("项目方法没有从代表材料中选择 4～5 份试跑小样")

    monkeypatch.setattr(worker, "run_project_orientation", failing_run)

    with pytest.raises(ValueError):
        await worker.process_expert_project_orientation({"job_try": 5}, project_id, 8)

    async with worker_env() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None and project.status == "needs_attention"
    assert pool.jobs == []
