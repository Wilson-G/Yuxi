"""专家项目 ARQ 任务与启动恢复入口。"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
from anthropic import APIConnectionError as AnthropicAPIConnectionError
from anthropic import InternalServerError as AnthropicInternalServerError
from anthropic import RateLimitError as AnthropicRateLimitError
from arq import Retry
from google.genai.errors import ClientError as GeminiClientError
from google.genai.errors import ServerError as GeminiServerError
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from sqlalchemy.exc import OperationalError
from urllib3.exceptions import ConnectTimeoutError, MaxRetryError, ProtocolError, ReadTimeoutError

from yuxi.expert.compilation_service import run_handout_compilation
from yuxi.expert.model_tasks import ExpertTransportError
from yuxi.expert.organization_service import initialize_organization_stage, run_organization_stage
from yuxi.expert.service import (
    exclude_failed_material_for_auto_advance,
    mark_material_learning_attention,
    mark_project_attention,
    prepare_expert_project,
    retry_material_learning,
    run_material_learning_stage,
    run_method_calibration,
    run_project_orientation,
    start_project_orientation,
)
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.minio.client import StorageError
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.logging_config import logger

# 首次执行之外最多重试 4 次；与 run_worker.WorkerSettings.max_tries 保持一致。
SYSTEM_TASK_MAX_RETRIES = 4
SYSTEM_TASK_MAX_TRIES = 1 + SYSTEM_TASK_MAX_RETRIES

# 传输类故障烧尽任务级预算后，错峰自动重投定向的上限与间隔：
# 免费上游分时段不可用，等窗口比立刻放弃更符合无人值守承诺；有界防循环。
ORIENTATION_AUTO_REROLL_LIMIT = 6
ORIENTATION_AUTO_REROLL_DEFER_SECONDS = 45 * 60
ORIENTATION_AUTO_REROLL_COUNTER_TTL_SECONDS = 24 * 60 * 60

# 单材料学习沿用同一错峰节奏：预算内重投拉回，耗尽后按推进模式结算（auto 排除交接 / manual 留给人工）。
MATERIAL_LEARNING_AUTO_REROLL_LIMIT = ORIENTATION_AUTO_REROLL_LIMIT
MATERIAL_LEARNING_AUTO_REROLL_DEFER_SECONDS = ORIENTATION_AUTO_REROLL_DEFER_SECONDS
MATERIAL_LEARNING_AUTO_REROLL_COUNTER_TTL_SECONDS = ORIENTATION_AUTO_REROLL_COUNTER_TTL_SECONDS

_project_job_locks: dict[str, asyncio.Lock] = {}


def _system_retry_defer(ctx: dict) -> int:
    """失败后的指数退避：第 n 次失败延迟 2**n 秒（2、4、8、16）。"""

    return 2 ** min(max(1, _job_try(ctx)), SYSTEM_TASK_MAX_RETRIES)


def _should_retry_system_failure(exc: Exception, ctx: dict) -> bool:
    """确定性业务失败（ValueError）重试必然复现，直接落需要处理；其余按剩余额度退避重试。"""

    if isinstance(exc, ValueError):
        return False
    return _job_try(ctx) < SYSTEM_TASK_MAX_TRIES


async def enqueue_project_preparation(project_id: str, *, token: str = "initial") -> None:
    """提交材料正文转换任务。"""

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_expert_project_preparation",
        project_id,
        _job_id=f"expert:prepare:{project_id}:{token}",
    )


async def enqueue_project_orientation(project_id: str, method_version: int) -> None:
    """提交当前项目方法版本的定向与小样任务。"""

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_expert_project_orientation",
        project_id,
        method_version,
        _job_id=f"expert:orientation:{project_id}:v{method_version}",
    )


async def enqueue_method_calibration(project_id: str, method_version: int) -> None:
    """提交当前版本的方法校准及同批小样复跑任务。"""

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_expert_method_calibration",
        project_id,
        method_version,
        _job_id=f"expert:method-calibration:{project_id}:v{method_version}",
    )


async def enqueue_material_learning(project_id: str, material_id: str, method_version: int) -> None:
    """使用项目、材料和方法版本提交一个稳定的单材料工作。"""

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_expert_material_learning",
        project_id,
        material_id,
        method_version,
        _job_id=f"expert:material:{project_id}:{material_id}:v{method_version}",
    )


async def enqueue_pending_material_learning(project_id: str, method_version: int) -> None:
    """只投递当前方法版本下尚未完成的材料工作。"""

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if (
            project is None
            or project.stage != "material_learning"
            or project.status != "running"
            or project.method_version != method_version
        ):
            return
        materials = await repo.list_project_materials(project_id)
        pending_ids = [
            material.material_id
            for material in materials
            if material.status in {"pending", "learning", "review_revise"}
        ]
    for material_id in pending_ids:
        await enqueue_material_learning(project_id, material_id, method_version)


async def enqueue_organization(project_id: str, organization_version: int) -> None:
    """提交候选主题、总编组织和讲义小样任务。"""

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_expert_organization",
        project_id,
        organization_version,
        _job_id=f"expert:organization:{project_id}:v{organization_version}",
    )


async def enqueue_handout_compilation(
    project_id: str,
    organization_version: int,
    *,
    token: str = "initial",
) -> None:
    """提交证据准备、串行讲义和全书收口任务。"""

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_expert_handout_compilation",
        project_id,
        organization_version,
        _job_id=f"expert:handouts:{project_id}:v{organization_version}:{token}",
    )


async def enqueue_resumed_expert_project(project_id: str) -> None:
    """按显式恢复后的项目事实投递当前阶段工作。"""

    async with pg_manager.get_async_session_context() as db:
        project = await ExpertRepository(db).get_project(project_id)
        if project is None:
            raise ValueError("专家项目不存在")
        stage = project.stage
        status = project.status
        method_version = project.method_version
        organization_version = project.organization_version
        calibration_pending = bool(project.method_calibration_baseline)

    if stage == "orientation" and status == "preparing":
        await enqueue_project_preparation(project_id, token="resume")
    elif stage == "orientation" and status == "running" and calibration_pending:
        await enqueue_method_calibration(project_id, method_version)
    elif stage == "orientation" and status == "running":
        await enqueue_project_orientation(project_id, method_version)
    elif stage == "material_learning" and status == "running":
        await enqueue_pending_material_learning(project_id, method_version)
    elif stage == "organization" and status == "preparing":
        organization_version = await initialize_organization_stage(project_id)
        await enqueue_organization(project_id, organization_version)
    elif stage == "organization" and status == "running":
        await enqueue_organization(project_id, organization_version)
    elif stage == "handout_compilation" and status == "running":
        await enqueue_handout_compilation(project_id, organization_version, token="resume")


async def process_expert_project_preparation(ctx, project_id: str) -> dict:
    """在 Worker 中把项目材料转换成可靠正文。"""

    async with _project_job_lock(project_id):
        try:
            return await prepare_expert_project(project_id)
        except Exception as exc:
            if _should_retry_system_failure(exc, ctx):
                logger.warning(
                    f"expert 材料准备将重试 project={project_id} try={_job_try(ctx)}: {exc!r}"
                )
                raise Retry(defer=_system_retry_defer(ctx)) from exc
            await mark_project_attention(project_id, str(exc))
            raise


async def process_expert_project_orientation(ctx, project_id: str, method_version: int) -> dict:
    """在 Worker 中执行项目定向、Prompt 设计和真实小样。"""

    async with _project_job_lock(project_id):
        try:
            return await run_project_orientation(project_id, method_version)
        except Exception as exc:
            if _should_retry_system_failure(exc, ctx):
                logger.warning(
                    f"expert 项目定向将重试 project={project_id} try={_job_try(ctx)}: {exc!r}"
                )
                raise Retry(defer=_system_retry_defer(ctx)) from exc
            await mark_project_attention(project_id, str(exc))
            if _is_transient_material_error(exc):
                await _schedule_orientation_auto_reroll(project_id)
            raise


async def _schedule_orientation_auto_reroll(project_id: str) -> None:
    """传输类故障烧尽任务预算后安排错峰自动重投；有界，超限留给人工。"""

    pool = await get_arq_pool()
    counter_key = f"expert:orientation:auto-reroll:{project_id}"
    rounds = int(await pool.incr(counter_key))
    await pool.expire(counter_key, ORIENTATION_AUTO_REROLL_COUNTER_TTL_SECONDS)
    if rounds > ORIENTATION_AUTO_REROLL_LIMIT:
        logger.warning(
            f"expert 定向自动重投 24h 内已达 {ORIENTATION_AUTO_REROLL_LIMIT} 轮上限，"
            f"等待人工处理 project={project_id}"
        )
        return
    async with pg_manager.get_async_session_context() as db:
        project = await ExpertRepository(db).get_project(project_id)
        if project is None or project.stage != "orientation":
            return
        method_version = project.method_version
    logger.warning(
        f"expert 定向传输故障烧尽预算，{ORIENTATION_AUTO_REROLL_DEFER_SECONDS // 60} 分钟后自动重投 "
        f"project={project_id} v{method_version} 第 {rounds}/{ORIENTATION_AUTO_REROLL_LIMIT} 轮"
    )
    await pool.enqueue_job(
        "process_expert_project_orientation_reroll",
        project_id,
        method_version,
        rounds,
        _job_id=f"expert:orientation:{project_id}:reroll:{rounds}",
        _defer_by=timedelta(seconds=ORIENTATION_AUTO_REROLL_DEFER_SECONDS),
    )


async def process_expert_project_orientation_reroll(
    ctx,
    project_id: str,
    method_version: int,
    round_index: int = 0,
) -> dict:
    """错峰重投：把因传输故障落 attention 的定向项目按既有代表材料拉回队列。"""

    del ctx
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if (
            project is None
            or project.stage != "orientation"
            or project.status != "needs_attention"
            or project.method_version != method_version
            or not project.representative_material_ids
        ):
            return {"project_id": project_id, "reroll": round_index, "ignored": True}
        version = await start_project_orientation(
            db,
            project_id=project_id,
            created_by=project.created_by,
            representative_material_ids=list(project.representative_material_ids),
        )
        await db.commit()
    await enqueue_project_orientation(project_id, version)
    logger.warning(f"expert 定向自动重投已提交 project={project_id} v{method_version}→v{version}")
    return {"project_id": project_id, "method_version": version, "reroll": round_index}


async def process_expert_method_calibration(ctx, project_id: str, method_version: int) -> dict:
    """在 Worker 中由总编收敛方法并重跑固定小样。"""

    async with _project_job_lock(project_id):
        try:
            result = await run_method_calibration(project_id, method_version)
            next_method_version = result.get("next_method_version")
            if isinstance(next_method_version, int) and next_method_version > method_version:
                # 自动模式开启了下一轮校准：必须用新版本号投递，否则同一 job_id 会被队列去重拒投。
                await enqueue_method_calibration(project_id, next_method_version)
            else:
                await enqueue_pending_material_learning(project_id, method_version)
            return result
        except Exception as exc:
            if _should_retry_system_failure(exc, ctx):
                logger.warning(
                    f"expert 方法校准将重试 project={project_id} try={_job_try(ctx)}: {exc!r}"
                )
                raise Retry(defer=_system_retry_defer(ctx)) from exc
            await mark_project_attention(project_id, str(exc))
            raise


async def process_expert_material_learning(
    ctx,
    project_id: str,
    material_id: str,
    method_version: int,
) -> dict:
    """在 Worker 中执行一份材料，并由持久化事实决定阶段交接。"""

    try:
        result = await run_material_learning_stage(project_id, material_id, method_version)
        if result.get("status") in {"preparing", "ignored"}:
            organization_version = await _enqueue_material_learning_handoff(project_id)
        else:
            organization_version = None
        if organization_version is not None:
            result["organization_version"] = organization_version
        return result
    except Exception as exc:
        if _is_transient_material_error(exc) and _job_try(ctx) < SYSTEM_TASK_MAX_TRIES:
            logger.warning(
                f"expert 单材料学习将重试 project={project_id} material={material_id} "
                f"try={_job_try(ctx)}: {exc!r}"
            )
            raise Retry(defer=_system_retry_defer(ctx)) from exc
        await mark_material_learning_attention(project_id, material_id, method_version, str(exc))
        if _is_transient_material_error(exc):
            await _schedule_material_learning_auto_reroll(project_id, material_id, method_version)
        raise


async def _schedule_material_learning_auto_reroll(
    project_id: str,
    material_id: str,
    method_version: int,
) -> None:
    """单材料传输故障烧尽任务预算后安排错峰自动重投；预算耗尽按推进模式结算。"""

    pool = await get_arq_pool()
    counter_key = f"expert:material:auto-reroll:{project_id}:{material_id}"
    rounds = int(await pool.incr(counter_key))
    await pool.expire(counter_key, MATERIAL_LEARNING_AUTO_REROLL_COUNTER_TTL_SECONDS)
    if rounds > MATERIAL_LEARNING_AUTO_REROLL_LIMIT:
        logger.warning(
            f"expert 材料学习自动重投 24h 内已达 {MATERIAL_LEARNING_AUTO_REROLL_LIMIT} 轮上限，"
            f"开始结算 project={project_id} material={material_id}"
        )
        await _settle_exhausted_material_reroll(project_id, material_id, method_version)
        return
    logger.warning(
        f"expert 材料学习传输故障烧尽预算，"
        f"{MATERIAL_LEARNING_AUTO_REROLL_DEFER_SECONDS // 60} 分钟后自动重投 "
        f"project={project_id} material={material_id} v{method_version} "
        f"第 {rounds}/{MATERIAL_LEARNING_AUTO_REROLL_LIMIT} 轮"
    )
    await pool.enqueue_job(
        "process_expert_material_learning_reroll",
        project_id,
        material_id,
        method_version,
        rounds,
        _job_id=f"expert:material:{project_id}:{material_id}:reroll:{rounds}",
        _defer_by=timedelta(seconds=MATERIAL_LEARNING_AUTO_REROLL_DEFER_SECONDS),
    )


async def _settle_exhausted_material_reroll(project_id: str, material_id: str, method_version: int) -> None:
    """重投预算耗尽后的结算：自动推进项目排除失败材料并交接，手动推进保持原地等人工。"""

    should_start_organization = await exclude_failed_material_for_auto_advance(
        project_id, material_id, method_version
    )
    if not should_start_organization:
        logger.warning(f"expert 材料学习重投耗尽，等待人工处理 project={project_id} material={material_id}")
        return
    organization_version = await initialize_organization_stage(project_id)
    await enqueue_organization(project_id, organization_version)
    logger.warning(
        f"expert 材料学习重投耗尽，已自动排除失败材料并进入组织阶段 "
        f"project={project_id} material={material_id} org_v{organization_version}"
    )


async def process_expert_material_learning_reroll(
    ctx,
    project_id: str,
    material_id: str,
    method_version: int,
    round_index: int = 0,
) -> dict:
    """错峰重投：把因传输故障落 attention 的单材料学习拉回队列；现场已变时忽略。"""

    del ctx
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        material = await repo.get_material(material_id)
        if (
            project is None
            or project.stage != "material_learning"
            or project.status != "needs_attention"
            or project.method_version != method_version
            or material is None
            or material.project_id != project_id
            or material.status != "needs_attention"
        ):
            return {
                "project_id": project_id,
                "material_id": material_id,
                "reroll": round_index,
                "ignored": True,
            }
        created_by = project.created_by
        await retry_material_learning(
            db,
            project_id=project_id,
            material_id=material_id,
            created_by=created_by,
        )
    await enqueue_material_learning(project_id, material_id, method_version)
    logger.warning(
        f"expert 材料学习自动重投已提交 project={project_id} material={material_id} "
        f"v{method_version} 第 {round_index} 轮"
    )
    return {
        "project_id": project_id,
        "material_id": material_id,
        "method_version": method_version,
        "reroll": round_index,
    }


async def process_expert_organization(ctx, project_id: str, organization_version: int) -> dict:
    """在 Worker 中准备候选研究并由总编串行形成组织方案。"""

    async with _project_job_lock(project_id):
        try:
            if await _project_stage_is_stale(
                project_id,
                stage="organization",
                organization_version=organization_version,
            ):
                result = {"project_id": project_id, "ignored": True}
            else:
                result = await run_organization_stage(project_id, organization_version)
            await _enqueue_handout_compilation_handoff(project_id, organization_version)
            return result
        except Exception as exc:
            if _should_retry_system_failure(exc, ctx):
                logger.warning(
                    f"expert 组织阶段将重试 project={project_id} try={_job_try(ctx)}: {exc!r}"
                )
                raise Retry(defer=_system_retry_defer(ctx)) from exc
            await mark_project_attention(project_id, str(exc))
            raise


async def process_expert_handout_compilation(ctx, project_id: str, organization_version: int) -> dict:
    """在 Worker 中完成正式讲义、全书收口和候选 Skill。"""

    async with _project_job_lock(project_id):
        if await _project_stage_is_stale(
            project_id,
            stage="handout_compilation",
            organization_version=organization_version,
        ):
            return {"project_id": project_id, "ignored": True}
        try:
            return await run_handout_compilation(project_id, organization_version)
        except Exception as exc:
            if _should_retry_system_failure(exc, ctx):
                logger.warning(
                    f"expert 讲义编译将重试 project={project_id} try={_job_try(ctx)}: {exc!r}"
                )
                raise Retry(defer=_system_retry_defer(ctx)) from exc
            await _mark_handout_compilation_attention(project_id, organization_version, str(exc))
            raise


async def recover_expert_project_jobs() -> None:
    """依据项目事实记录重投第 0 阶段尚未完成的工作。"""

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        projects = await repo.list_projects_by_stage_status(
            stage="orientation",
            statuses={"preparing", "running", "awaiting_confirmation"},
        )
        learning_projects = await repo.list_projects_by_stage_status(
            stage="material_learning",
            statuses={"running"},
        )
        organization_projects = await repo.list_projects_by_stage_status(
            stage="organization",
            statuses={"preparing", "running"},
        )
        compilation_projects = await repo.list_projects_by_stage_status(
            stage="handout_compilation",
            statuses={"running"},
        )

    for project in projects:
        if project.status == "preparing":
            await enqueue_project_preparation(project.project_id, token="recovery")
        elif project.method_calibration_baseline:
            await enqueue_method_calibration(project.project_id, project.method_version)
        elif project.status == "running" and project.method_version > 0:
            await enqueue_project_orientation(project.project_id, project.method_version)
    for project in learning_projects:
        await enqueue_pending_material_learning(project.project_id, project.method_version)
    for project in organization_projects:
        version = project.organization_version
        if project.status == "preparing":
            version = await initialize_organization_stage(project.project_id)
        await enqueue_organization(project.project_id, version)
    for project in compilation_projects:
        await enqueue_handout_compilation(
            project.project_id,
            project.organization_version,
            token="recovery",
        )


def _job_try(ctx: dict) -> int:
    try:
        return max(1, int(ctx.get("job_try", 1)))
    except (TypeError, ValueError):
        return 1


def _is_transient_material_error(exc: Exception) -> bool:
    """只识别当前供应商调用中可明确重试的瞬时失败。"""

    if isinstance(
        exc,
        (
            OperationalError,
            ConnectionError,
            TimeoutError,
            asyncio.TimeoutError,
            httpx.TransportError,
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            InternalServerError,
            AnthropicAPIConnectionError,
            AnthropicRateLimitError,
            AnthropicInternalServerError,
            GeminiServerError,
            ConnectTimeoutError,
            MaxRetryError,
            ProtocolError,
            ReadTimeoutError,
            # 专家调用层重放耗尽后抛出的统一传输失败信号。
            ExpertTransportError,
        ),
    ):
        return True
    if isinstance(exc, GeminiClientError):
        return exc.code == 429
    if isinstance(exc, StorageError):
        return exc.retryable
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code in {502, 503, 504}
    return False


async def _enqueue_material_learning_handoff(project_id: str) -> int | None:
    """在材料任务重试时继续尚未完成的组织阶段交接。"""

    async with pg_manager.get_async_session_context() as db:
        project = await ExpertRepository(db).get_project(project_id)
        if project is None or project.stage != "organization" or project.status not in {"preparing", "running"}:
            return None

    organization_version = await initialize_organization_stage(project_id)
    await enqueue_organization(project_id, organization_version)
    return organization_version


async def _enqueue_handout_compilation_handoff(project_id: str, organization_version: int) -> None:
    """按已提交的项目事实补投当前组织版本的讲义编译。"""

    async with pg_manager.get_async_session_context() as db:
        project = await ExpertRepository(db).get_project(project_id)
        if (
            project is None
            or project.stage != "handout_compilation"
            or project.status != "running"
            or project.organization_version != organization_version
        ):
            return

    await enqueue_handout_compilation(project_id, organization_version)


def _project_job_lock(project_id: str) -> asyncio.Lock:
    """返回当前 Worker 进程内同一专家项目共用的执行锁。"""

    lock = _project_job_locks.get(project_id)
    if lock is None:
        lock = asyncio.Lock()
        _project_job_locks[project_id] = lock
    return lock


async def _project_stage_is_stale(
    project_id: str,
    *,
    stage: str,
    organization_version: int,
) -> bool:
    """识别前序 runner 已完成后才取得锁的过期阶段任务。"""

    async with pg_manager.get_async_session_context() as db:
        project = await ExpertRepository(db).get_project(project_id)
        if project is None:
            return False
        return (
            project.stage != stage
            or project.status != "running"
            or project.organization_version != organization_version
        )


async def _mark_handout_compilation_attention(
    project_id: str,
    organization_version: int,
    error: str,
) -> None:
    """把讲义阶段外层失败落到现有主题或全书重跑入口。"""

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None or project.status == "paused":
            return
        topics = await repo.list_topics(project_id, organization_version)
        actionable_statuses = {"pending", "preparing_evidence", "evidence_ready", "writing"}
        actionable_topic = next((topic for topic in topics if topic.status in actionable_statuses), None)

        if actionable_topic is not None:
            await repo.update_topic(
                actionable_topic.topic_id,
                status="needs_attention",
                error_message=error,
            )
            message = f"《{actionable_topic.title}》执行中断，请检查原因后重跑"
        elif topics and all(topic.status == "passed" for topic in topics):
            message = "全书收口执行失败，请检查原因后重跑"
        else:
            message = "讲义编译执行失败，请处理或重跑未通过的讲义"

        await repo.update_project(
            project_id,
            status="needs_attention",
            message=message,
            error_message=error,
        )


__all__ = [
    "enqueue_material_learning",
    "enqueue_pending_material_learning",
    "enqueue_handout_compilation",
    "enqueue_method_calibration",
    "enqueue_organization",
    "enqueue_project_orientation",
    "enqueue_project_preparation",
    "enqueue_resumed_expert_project",
    "process_expert_material_learning",
    "process_expert_method_calibration",
    "process_expert_handout_compilation",
    "process_expert_organization",
    "process_expert_project_orientation",
    "process_expert_project_preparation",
    "recover_expert_project_jobs",
]
