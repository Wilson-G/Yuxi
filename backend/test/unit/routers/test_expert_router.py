"""专家材料包路由的任务投递测试。"""

from types import SimpleNamespace

import pytest

from server.routers import expert_router


@pytest.mark.asyncio
async def test_compile_route_uses_a_new_arq_job_id(monkeypatch: pytest.MonkeyPatch):
    updates: list[dict] = []
    jobs: list[tuple] = []

    monkeypatch.setattr(
        expert_router,
        "get_expert_package",
        lambda _package_id: {"id": "expert-test", "name": "验收专家", "active": False, "status": "compiled"},
    )
    monkeypatch.setattr(
        expert_router,
        "update_expert_package",
        lambda _package_id, **changes: updates.append(changes),
    )

    class FakePool:
        async def enqueue_job(self, *args, **kwargs):
            jobs.append((args, kwargs))

    async def fake_get_arq_pool():
        return FakePool()

    monkeypatch.setattr(expert_router, "get_arq_pool", fake_get_arq_pool)

    result = await expert_router.compile_expert_package_route("expert-test", "quick", SimpleNamespace())

    assert result["task_created"] is True
    assert updates == [
        {
            "status": "queued",
            "active_task_id": result["task_id"],
            "progress": 0,
            "message": "等待 Worker 执行",
            "error": None,
        }
    ]
    assert jobs == [
        (
            ("process_expert_package_compile", "expert-test", result["task_id"], "quick"),
            {"_job_id": f"expert-compile:expert-test:quick:{result['task_id']}"},
        )
    ]
    assert result["compiler"] == "quick"


@pytest.mark.asyncio
async def test_compile_route_reuses_active_compile_task(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        expert_router,
        "get_expert_package",
        lambda _package_id: {
            "id": "expert-test",
            "name": "验收专家",
            "active": False,
            "status": "queued",
            "active_task_id": "active-task",
        },
    )

    async def should_not_enqueue():
        raise AssertionError("已有活动任务时不应重新投递")

    monkeypatch.setattr(expert_router, "get_arq_pool", should_not_enqueue)

    result = await expert_router.compile_expert_package_route("expert-test", "quick", SimpleNamespace())

    assert result == {
        "status": "queued",
        "task_id": "active-task",
        "task_created": False,
        "message": "专家包已有编译任务在执行",
    }
