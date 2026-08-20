"""专家项目 ARQ 任务与启动恢复入口。"""

from __future__ import annotations

import asyncio
import uuid

from arq import Retry

from yuxi.expert.compilation_service import run_handout_compilation
from yuxi.expert.organization_service import initialize_organization_stage, run_organization_stage
from yuxi.expert.service import (
    mark_project_attention,
    prepare_expert_project,
    run_method_calibration,
    run_material_learning_stage,
    run_project_orientation,
)
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.postgres.manager import pg_manager

SYSTEM_TASK_MAX_TRIES = 2
_project_job_locks: dict[str, asyncio.Lock] = {}


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
    """提交唯一一次方法校准及同批小样复跑任务。"""

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_expert_method_calibration",
        project_id,
        method_version,
        _job_id=f"expert:method-calibration:{project_id}:v{method_version}",
    )


async def enqueue_material_learning(project_id: str, method_version: int, *, token: str = "initial") -> None:
    """提交逐份材料学习阶段任务。"""

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_expert_material_learning",
        project_id,
        method_version,
        _job_id=f"expert:materials:{project_id}:v{method_version}:{token}",
    )


async def enqueue_organization(project_id: str, organization_version: int, *, token: str = "initial") -> None:
    """提交候选主题、总编组织和讲义小样任务。"""

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_expert_organization",
        project_id,
        organization_version,
        _job_id=f"expert:organization:{project_id}:v{organization_version}:{token}",
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


async def process_expert_project_preparation(ctx, project_id: str) -> dict:
    """在 Worker 中把项目材料转换成可靠正文。"""

    async with _project_job_lock(project_id):
        try:
            return await prepare_expert_project(project_id)
        except Exception as exc:
            if _job_try(ctx) < SYSTEM_TASK_MAX_TRIES:
                raise Retry(defer=5) from exc
            await mark_project_attention(project_id, str(exc))
            raise


async def process_expert_project_orientation(ctx, project_id: str, method_version: int) -> dict:
    """在 Worker 中执行项目定向、Prompt 设计和真实小样。"""

    async with _project_job_lock(project_id):
        try:
            return await run_project_orientation(project_id, method_version)
        except Exception as exc:
            if _job_try(ctx) < SYSTEM_TASK_MAX_TRIES:
                raise Retry(defer=10) from exc
            await mark_project_attention(project_id, str(exc))
            raise


async def process_expert_method_calibration(ctx, project_id: str, method_version: int) -> dict:
    """在 Worker 中由总编收敛方法并重跑固定小样。"""

    async with _project_job_lock(project_id):
        try:
            result = await run_method_calibration(project_id, method_version)
            if result.get("status") == "running":
                await enqueue_material_learning(project_id, method_version)
            return result
        except Exception as exc:
            if _job_try(ctx) < SYSTEM_TASK_MAX_TRIES:
                raise Retry(defer=10) from exc
            await mark_project_attention(project_id, str(exc))
            raise


async def process_expert_material_learning(ctx, project_id: str, method_version: int) -> dict:
    """在 Worker 中持续补位处理项目内独立材料任务。"""

    async with _project_job_lock(project_id):
        try:
            result = await run_material_learning_stage(project_id, method_version)
            if result.get("status") == "preparing":
                organization_version = await initialize_organization_stage(project_id)
                await enqueue_organization(project_id, organization_version)
                result["organization_version"] = organization_version
            return result
        except Exception as exc:
            if _job_try(ctx) < SYSTEM_TASK_MAX_TRIES:
                raise Retry(defer=10) from exc
            await mark_project_attention(project_id, str(exc))
            raise


async def process_expert_organization(ctx, project_id: str, organization_version: int) -> dict:
    """在 Worker 中准备候选研究并由总编串行形成组织方案。"""

    async with _project_job_lock(project_id):
        if await _project_stage_is_stale(
            project_id,
            stage="organization",
            organization_version=organization_version,
        ):
            return {"project_id": project_id, "ignored": True}
        try:
            return await run_organization_stage(project_id, organization_version)
        except Exception as exc:
            if _job_try(ctx) < SYSTEM_TASK_MAX_TRIES:
                raise Retry(defer=10) from exc
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
            if _job_try(ctx) < SYSTEM_TASK_MAX_TRIES:
                raise Retry(defer=10) from exc
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
        elif project.method_confirmed_at and project.method_calibration_baseline:
            await enqueue_method_calibration(project.project_id, project.method_version)
        elif project.status == "running" and project.method_version > 0:
            await enqueue_project_orientation(project.project_id, project.method_version)
    for project in learning_projects:
        await enqueue_material_learning(project.project_id, project.method_version, token="recovery")
    for project in organization_projects:
        version = project.organization_version
        if project.status == "preparing":
            version = await initialize_organization_stage(project.project_id)
        await enqueue_organization(project.project_id, version, token=f"recovery-{uuid.uuid4().hex}")
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
    "enqueue_handout_compilation",
    "enqueue_method_calibration",
    "enqueue_organization",
    "enqueue_project_orientation",
    "enqueue_project_preparation",
    "process_expert_material_learning",
    "process_expert_method_calibration",
    "process_expert_handout_compilation",
    "process_expert_organization",
    "process_expert_project_orientation",
    "process_expert_project_preparation",
    "recover_expert_project_jobs",
]
