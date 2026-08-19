"""专家项目任务中心投影测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import yuxi.storage.postgres.models_material  # noqa: F401

from yuxi.expert.task_view import get_expert_task_summary
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.storage.postgres.models_business import Base

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def task_view_factory(tmp_path: Path):
    """创建任务投影使用的隔离数据库。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'expert-task-view.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("project_status", ["ready", "awaiting_confirmation", "paused"])
async def test_waiting_user_actions_are_not_completed(task_view_factory, project_status: str) -> None:
    """正文就绪、等待确认和暂停都属于等待操作，不属于成功终态。"""

    async with task_view_factory() as db:
        await ExpertRepository(db).create_project(
            project_id=f"project-{project_status}",
            name="等待操作项目",
            description="",
            model_spec="provider:model",
            created_by="admin-1",
            stage="release" if project_status == "ready" else "orientation",
            status=project_status,
        )
        await db.commit()

        task = await get_expert_task_summary(db, f"project-{project_status}")

    assert task is not None
    assert task["status"] == "waiting"
    assert task["completed_at"] is None


@pytest.mark.asyncio
async def test_enabled_project_is_the_only_successful_expert_projection(task_view_factory) -> None:
    async with task_view_factory() as db:
        await ExpertRepository(db).create_project(
            project_id="project-enabled",
            name="已启用项目",
            description="",
            model_spec="provider:model",
            created_by="admin-1",
            stage="release",
            status="enabled",
        )
        await db.commit()

        task = await get_expert_task_summary(db, "project-enabled")

    assert task is not None
    assert task["status"] == "success"
    assert task["completed_at"] is not None
