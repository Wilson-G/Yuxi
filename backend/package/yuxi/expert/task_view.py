"""把专家项目事实投影为任务中心只读摘要。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.expert_repository import ExpertRepository


async def list_expert_task_summaries(
    db: AsyncSession,
    *,
    created_by: str | None = None,
) -> list[dict[str, Any]]:
    """列出全部专家项目的任务中心摘要，不创建 Tasker 记录。"""

    repo = ExpertRepository(db)
    projects = await repo.list_projects(created_by=created_by)
    return [await _build_task(repo, project) for project in projects]


async def get_expert_task_summary(
    db: AsyncSession,
    project_id: str,
    *,
    created_by: str | None = None,
) -> dict[str, Any] | None:
    """按项目 ID 读取一条专家任务中心摘要。"""

    repo = ExpertRepository(db)
    project = await repo.get_project(project_id, created_by=created_by)
    return await _build_task(repo, project) if project is not None else None


async def _build_task(repo: ExpertRepository, project: Any) -> dict[str, Any]:
    materials = await repo.list_project_materials(project.project_id)
    topics = (
        await repo.list_topics(project.project_id, project.organization_version)
        if project.organization_version > 0
        else []
    )
    return {
        "id": f"expert-project:{project.project_id}",
        "name": f"专家项目：{project.name}",
        "type": "expert_project",
        "status": _task_status(project.status),
        "progress": _project_progress(project, materials, topics),
        "message": project.message,
        "created_at": project.to_dict()["created_at"],
        "updated_at": project.to_dict()["updated_at"],
        "started_at": project.to_dict()["created_at"],
        "completed_at": project.to_dict()["updated_at"] if project.status == "enabled" else None,
        "payload": {
            "project_id": project.project_id,
            "stage": project.stage,
            "project_status": project.status,
        },
        "result": {"skill_slug": project.skill_slug} if project.skill_slug else None,
        "error": project.error_message,
        "cancel_requested": False,
    }


def _task_status(project_status: str) -> str:
    if project_status == "needs_attention":
        return "failed"
    if project_status == "enabled":
        return "success"
    if project_status in {"ready", "awaiting_confirmation", "paused"}:
        return "waiting"
    return "running"


def _project_progress(project: Any, materials: list[Any], topics: list[Any]) -> float:
    if project.stage == "orientation":
        if project.status == "awaiting_confirmation":
            return 20.0
        ready = sum(item.status in {"ready", "excluded"} for item in materials)
        return round(10 * ready / max(1, len(materials)), 1)
    if project.stage == "material_learning":
        terminal = sum(item.status in {"passed", "no_value", "needs_attention", "excluded"} for item in materials)
        return round(20 + 35 * terminal / max(1, len(materials)), 1)
    if project.stage == "organization":
        return 70.0 if project.status == "awaiting_confirmation" else 60.0
    if project.stage == "handout_compilation":
        passed = sum(topic.status == "passed" for topic in topics)
        return round(70 + 25 * passed / max(1, len(topics)), 1)
    if project.stage == "release":
        return 100.0
    return 0.0


__all__ = ["get_expert_task_summary", "list_expert_task_summaries"]
