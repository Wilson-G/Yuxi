from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.expert.task_view import get_expert_task_summary, list_expert_task_summaries
from yuxi.services.task_service import tasker
from yuxi.storage.postgres.models_business import User

tasks = APIRouter(prefix="/tasks", tags=["tasks"])


@tasks.get("")
async def list_tasks(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """List Tasker records and read-only expert project projections."""

    is_admin = current_user.role in {"admin", "superadmin"}
    tasker_result = (
        await tasker.list_tasks(status=None, limit=limit)
        if is_admin
        else {"tasks": [], "summary": {"total": 0, "status_counts": {}, "type_counts": {}}}
    )
    expert_items = await list_expert_task_summaries(
        db,
        created_by=None if is_admin else str(current_user.uid),
    )
    all_items = [*tasker_result["tasks"], *expert_items]
    items = all_items
    if status:
        items = [item for item in items if item["status"] == status]
    items.sort(key=lambda item: (item.get("created_at") or "", item["id"]), reverse=True)
    status_counts = Counter(tasker_result["summary"]["status_counts"])
    status_counts.update(item["status"] for item in expert_items)
    type_counts = Counter(tasker_result["summary"]["type_counts"])
    type_counts.update(item["type"] for item in expert_items)
    total = int(tasker_result["summary"]["total"]) + len(expert_items)
    filtered_total = status_counts.get(status, 0) if status else total
    return {
        "tasks": items[:limit],
        "summary": {
            "total": total,
            "filtered_total": filtered_total,
            "status_counts": dict(status_counts),
            "type_counts": dict(type_counts),
        },
    }


@tasks.get("/{task_id}")
async def get_task(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve a single task by id."""
    if task_id.startswith("expert-project:"):
        task = await get_expert_task_summary(
            db,
            task_id.removeprefix("expert-project:"),
            created_by=None if current_user.role in {"admin", "superadmin"} else str(current_user.uid),
        )
        if task:
            return {"task": task}
    if current_user.role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=404, detail="Task not found")
    task = await tasker.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": task}


@tasks.post("/{task_id}/cancel")
async def cancel_task(task_id: str, current_user: User = Depends(get_admin_user)):
    """Request cancellation of a task."""
    success = await tasker.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Task cannot be cancelled")
    return {"task_id": task_id, "status": "cancelled"}


@tasks.delete("/{task_id}")
async def delete_task(task_id: str, current_user: User = Depends(get_admin_user)):
    """Delete a task by id."""
    success = await tasker.delete_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task_id, "status": "deleted"}
