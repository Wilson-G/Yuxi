"""专家项目路由的外部投递失败合同。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import server.routers.expert_project_router as router


@pytest.mark.asyncio
async def test_resume_enqueue_failure_restores_paused_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复任务未投递时，项目仍可由用户再次恢复。"""

    statuses: list[str] = []

    async def resume_project(_db, *, project_id: str, created_by: str):
        assert project_id == "project-1"
        assert created_by == "admin-1"
        statuses.append("running")
        return SimpleNamespace(project_id=project_id, stage="material_learning", status="running")

    async def fail_enqueue(project_id: str) -> None:
        assert project_id == "project-1"
        raise ConnectionError("redis unavailable")

    async def pause_project(_db, *, project_id: str, created_by: str):
        assert project_id == "project-1"
        assert created_by == "admin-1"
        statuses.append("paused")
        return SimpleNamespace(project_id=project_id, status="paused")

    monkeypatch.setattr(router, "resume_expert_project", resume_project)
    monkeypatch.setattr(router, "enqueue_resumed_expert_project", fail_enqueue)
    monkeypatch.setattr(router, "pause_expert_project", pause_project)

    with pytest.raises(HTTPException) as exc:
        await router.resume_expert_project_route(
            "project-1",
            current_user=SimpleNamespace(uid="admin-1"),
            db=object(),
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "项目仍保持暂停，后台任务恢复后请再次重试"
    assert statuses == ["running", "paused"]
