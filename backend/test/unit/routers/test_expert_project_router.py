"""专家项目路由的外部投递失败合同。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

import server.routers.expert_project_router as router


@pytest.mark.asyncio
async def test_compiler_profile_list_returns_default_and_opt_in_methods() -> None:
    response = await router.list_expert_compiler_profiles_route()

    assert response["default_profile_id"] == "apprenticeship"
    assert [(item["profile_id"], item["display_name"]) for item in response["items"]] == [
        ("apprenticeship", "教材笔记法"),
        ("reasoning_assets", "领域推导资产法"),
    ]


@pytest.mark.parametrize(
    ("payload", "expected_profile_id"),
    [
        (
            router.CreateExpertProjectRequest(
                name="默认方法项目",
                description="验证旧 API 默认行为",
                model_spec="provider:model",
                material_unit_ids=["unit-1"],
            ),
            "apprenticeship",
        ),
        (
            router.CreateExpertProjectRequest(
                name="领域推导项目",
                description="验证显式方法选择",
                model_spec="provider:model",
                compiler_profile_id="reasoning_assets",
                material_unit_ids=["unit-1"],
            ),
            "reasoning_assets",
        ),
    ],
)
@pytest.mark.asyncio
async def test_create_route_passes_default_or_explicit_compiler_profile(
    monkeypatch: pytest.MonkeyPatch,
    payload: router.CreateExpertProjectRequest,
    expected_profile_id: str,
) -> None:
    received: list[str] = []

    async def create_project(_db, **kwargs):
        received.append(kwargs["compiler_profile_id"])
        return SimpleNamespace(to_dict=lambda: {"project_id": "project-1", **kwargs})

    monkeypatch.setattr(router, "create_expert_project", create_project)

    response = await router.create_expert_project_route(
        payload,
        current_user=SimpleNamespace(uid="admin-1"),
        db=object(),
    )

    assert response["item"]["compiler_profile_id"] == expected_profile_id
    assert received == [expected_profile_id]


@pytest.mark.parametrize(
    ("request_kwargs", "expected_auto_advance"),
    [
        ({}, False),
        ({"json": {}}, False),
        ({"json": {"auto_advance": True}}, True),
    ],
)
@pytest.mark.asyncio
async def test_method_confirmation_accepts_legacy_body_and_returns_saved_mode(
    monkeypatch: pytest.MonkeyPatch,
    request_kwargs: dict,
    expected_auto_advance: bool,
) -> None:
    """无请求体、空请求体和显式开关都使用项目实际保存的推进模式。"""

    saved_auto_advance = False
    queued: list[tuple[str, int]] = []

    async def confirm_method(
        _db,
        *,
        project_id: str,
        created_by: str,
        auto_advance: bool,
    ) -> int:
        nonlocal saved_auto_advance
        assert project_id == "project-1"
        assert created_by == "admin-1"
        saved_auto_advance = auto_advance
        return 1

    class FakeRepository:
        def __init__(self, _db):
            pass

        async def get_project(self, project_id: str):
            assert project_id == "project-1"
            return SimpleNamespace(stage="material_learning", auto_advance=saved_auto_advance)

    async def enqueue_learning(project_id: str, method_version: int) -> None:
        queued.append((project_id, method_version))

    monkeypatch.setattr(router, "confirm_project_method", confirm_method)
    monkeypatch.setattr(router, "ExpertRepository", FakeRepository)
    monkeypatch.setattr(router, "enqueue_pending_material_learning", enqueue_learning)

    async def current_user():
        return SimpleNamespace(uid="admin-1")

    async def db_session():
        return object()

    app = FastAPI()
    app.include_router(router.expert_projects, prefix="/api")
    app.dependency_overrides[router.get_admin_user] = current_user
    app.dependency_overrides[router.get_required_user] = current_user
    app.dependency_overrides[router.get_db] = db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/expert-projects/project-1/method/confirm", **request_kwargs)

    assert response.status_code == 200
    assert response.json() == {
        "project_id": "project-1",
        "method_version": 1,
        "status": "running",
        "auto_advance": expected_auto_advance,
    }
    assert queued == [("project-1", 1)]


@pytest.mark.asyncio
async def test_method_repair_creates_version_and_enqueues_calibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued: list[tuple[str, int]] = []

    async def repair_method(_db, *, project_id: str, created_by: str) -> int:
        assert project_id == "project-1"
        assert created_by == "admin-1"
        return 2

    async def enqueue_calibration(project_id: str, method_version: int) -> None:
        queued.append((project_id, method_version))

    monkeypatch.setattr(router, "repair_project_method", repair_method)
    monkeypatch.setattr(router, "enqueue_method_calibration", enqueue_calibration)

    response = await router.repair_project_method_route(
        "project-1",
        current_user=SimpleNamespace(uid="admin-1"),
        db=object(),
    )

    assert response == {"project_id": "project-1", "method_version": 2, "status": "running"}
    assert queued == [("project-1", 2)]


@pytest.mark.asyncio
async def test_method_repair_returns_conflict_for_invalid_project_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_repair(*_args, **_kwargs):
        raise ValueError("项目方法小样没有可执行的 Review 问题")

    monkeypatch.setattr(router, "repair_project_method", reject_repair)

    with pytest.raises(HTTPException) as exc:
        await router.repair_project_method_route(
            "project-1",
            current_user=SimpleNamespace(uid="admin-1"),
            db=object(),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "项目方法小样没有可执行的 Review 问题"


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
