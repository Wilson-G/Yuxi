"""专家学习项目与材料的 PostgreSQL 访问边界。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import (
    ExpertMaterial,
    ExpertProject,
    ExpertResearchBatch,
    ExpertTopic,
)
from yuxi.utils.datetime_utils import utc_now_naive


class ExpertRepository:
    """集中读写专家项目、材料、候选研究和正式主题事实。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def create_project(
        self,
        *,
        project_id: str,
        name: str,
        description: str,
        model_spec: str,
        created_by: str,
        stage: str = "orientation",
        status: str = "pending",
    ) -> ExpertProject:
        """创建一个等待项目定向的专家项目。"""

        project = ExpertProject(
            project_id=project_id,
            name=name,
            description=description,
            model_spec=model_spec,
            stage=stage,
            status=status,
            created_by=created_by,
        )
        self.db.add(project)
        await self.db.flush()
        return project

    async def get_project(self, project_id: str, *, created_by: str | None = None) -> ExpertProject | None:
        """按项目 ID 读取项目，并可限定创建人。"""

        statement = select(ExpertProject).where(ExpertProject.project_id == project_id)
        if created_by is not None:
            statement = statement.where(ExpertProject.created_by == created_by)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def lock_project(self, project_id: str) -> ExpertProject | None:
        """锁定项目记录，供阶段确认和版本切换使用。"""

        result = await self.db.execute(
            select(ExpertProject).where(ExpertProject.project_id == project_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_projects(self, *, created_by: str | None = None) -> list[ExpertProject]:
        """列出全部项目或指定用户创建的项目。"""

        statement = select(ExpertProject)
        if created_by is not None:
            statement = statement.where(ExpertProject.created_by == created_by)
        statement = statement.order_by(ExpertProject.updated_at.desc(), ExpertProject.project_id.asc())
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def list_projects_by_stage_status(self, *, stage: str, statuses: set[str]) -> list[ExpertProject]:
        """列出 Worker 启动时需要恢复的项目。"""

        if not statuses:
            return []
        result = await self.db.execute(
            select(ExpertProject)
            .where(ExpertProject.stage == stage, ExpertProject.status.in_(statuses))
            .order_by(ExpertProject.updated_at.asc(), ExpertProject.project_id.asc())
        )
        return list(result.scalars().all())

    async def update_project(self, project_id: str, **changes: Any) -> ExpertProject | None:
        """更新项目字段，事务提交由调用服务负责。"""

        project = await self.get_project(project_id)
        if project is None:
            return None
        self._apply_changes(project, changes, immutable={"project_id", "created_by", "created_at", "updated_at"})
        await self.db.flush()
        return project

    async def create_materials(
        self,
        project_id: str,
        materials: list[dict[str, Any]],
    ) -> list[ExpertMaterial]:
        """批量创建一个项目下的自然材料单元。"""

        items = [ExpertMaterial(project_id=project_id, **material) for material in materials]
        self.db.add_all(items)
        await self.db.flush()
        return items

    async def list_project_materials(self, project_id: str) -> list[ExpertMaterial]:
        """按来源路径稳定列出项目材料。"""

        result = await self.db.execute(
            select(ExpertMaterial)
            .where(ExpertMaterial.project_id == project_id)
            .order_by(ExpertMaterial.source_path.asc(), ExpertMaterial.material_id.asc())
        )
        return list(result.scalars().all())

    async def get_material(self, material_id: str) -> ExpertMaterial | None:
        """按材料 ID 读取材料。"""

        result = await self.db.execute(select(ExpertMaterial).where(ExpertMaterial.material_id == material_id))
        return result.scalar_one_or_none()

    async def lock_material(self, material_id: str) -> ExpertMaterial | None:
        """锁定一份材料，供单份学习任务更新事实。"""

        result = await self.db.execute(
            select(ExpertMaterial).where(ExpertMaterial.material_id == material_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def update_material(self, material_id: str, **changes: Any) -> ExpertMaterial | None:
        """更新一份材料的状态或产物字段。"""

        material = await self.get_material(material_id)
        if material is None:
            return None
        self._apply_changes(
            material,
            changes,
            immutable={"material_id", "project_id", "created_at", "updated_at"},
        )
        await self.db.flush()
        return material

    async def list_materials_by_status(self, project_id: str, status: str) -> list[ExpertMaterial]:
        """列出项目中处于指定处理状态的材料。"""

        result = await self.db.execute(
            select(ExpertMaterial)
            .where(
                ExpertMaterial.project_id == project_id,
                ExpertMaterial.status == status,
            )
            .order_by(ExpertMaterial.source_path.asc(), ExpertMaterial.material_id.asc())
        )
        return list(result.scalars().all())

    async def replace_research_batches(
        self,
        project_id: str,
        organization_version: int,
        batches: list[dict[str, Any]],
    ) -> list[ExpertResearchBatch]:
        """替换一个项目指定组织版本的全部候选研究批次。"""

        await self.db.execute(
            delete(ExpertResearchBatch).where(
                ExpertResearchBatch.project_id == project_id,
                ExpertResearchBatch.organization_version == organization_version,
            )
        )
        items = [
            ExpertResearchBatch(
                project_id=project_id,
                organization_version=organization_version,
                **batch,
            )
            for batch in batches
        ]
        self.db.add_all(items)
        await self.db.flush()
        return items

    async def list_research_batches(
        self,
        project_id: str,
        organization_version: int,
    ) -> list[ExpertResearchBatch]:
        """列出一个项目指定组织版本的候选研究批次。"""

        result = await self.db.execute(
            select(ExpertResearchBatch)
            .where(
                ExpertResearchBatch.project_id == project_id,
                ExpertResearchBatch.organization_version == organization_version,
            )
            .order_by(ExpertResearchBatch.batch_id.asc())
        )
        return list(result.scalars().all())

    async def lock_research_batch(self, batch_id: str) -> ExpertResearchBatch | None:
        """锁定一个候选研究批次，供独立研究任务写入结果。"""

        result = await self.db.execute(
            select(ExpertResearchBatch).where(ExpertResearchBatch.batch_id == batch_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def update_research_batch(self, batch_id: str, **changes: Any) -> ExpertResearchBatch | None:
        """更新一个候选研究批次的状态或结果。"""

        result = await self.db.execute(select(ExpertResearchBatch).where(ExpertResearchBatch.batch_id == batch_id))
        batch = result.scalar_one_or_none()
        if batch is None:
            return None
        self._apply_changes(
            batch,
            changes,
            immutable={"batch_id", "project_id", "organization_version", "created_at", "updated_at"},
        )
        await self.db.flush()
        return batch

    async def replace_topics(
        self,
        project_id: str,
        organization_version: int,
        topics: list[dict[str, Any]],
    ) -> list[ExpertTopic]:
        """替换一个项目指定组织版本的全部正式主题。"""

        await self.db.execute(
            delete(ExpertTopic).where(
                ExpertTopic.project_id == project_id,
                ExpertTopic.organization_version == organization_version,
            )
        )
        items = [
            ExpertTopic(
                project_id=project_id,
                organization_version=organization_version,
                **topic,
            )
            for topic in topics
        ]
        self.db.add_all(items)
        await self.db.flush()
        return items

    async def list_topics(self, project_id: str, organization_version: int) -> list[ExpertTopic]:
        """按总编顺序列出一个项目指定组织版本的主题。"""

        result = await self.db.execute(
            select(ExpertTopic)
            .where(
                ExpertTopic.project_id == project_id,
                ExpertTopic.organization_version == organization_version,
            )
            .order_by(ExpertTopic.position.asc(), ExpertTopic.topic_key.asc(), ExpertTopic.topic_id.asc())
        )
        return list(result.scalars().all())

    async def lock_topic(self, topic_id: str) -> ExpertTopic | None:
        """锁定一个主题，供串行总编任务写入正式讲义。"""

        result = await self.db.execute(select(ExpertTopic).where(ExpertTopic.topic_id == topic_id).with_for_update())
        return result.scalar_one_or_none()

    async def update_topic(self, topic_id: str, **changes: Any) -> ExpertTopic | None:
        """更新一个主题的证据、讲义、Review 或状态。"""

        result = await self.db.execute(select(ExpertTopic).where(ExpertTopic.topic_id == topic_id))
        topic = result.scalar_one_or_none()
        if topic is None:
            return None
        self._apply_changes(
            topic,
            changes,
            immutable={"topic_id", "project_id", "organization_version", "created_at", "updated_at"},
        )
        await self.db.flush()
        return topic

    @staticmethod
    def _apply_changes(
        record: ExpertProject | ExpertMaterial | ExpertResearchBatch | ExpertTopic,
        changes: dict[str, Any],
        *,
        immutable: set[str],
    ) -> None:
        """只允许更新模型中的可变列，并同步更新时间。"""

        column_names = {column.name for column in record.__table__.columns}
        invalid = (set(changes) - column_names) | (set(changes) & immutable)
        if invalid:
            raise ValueError(f"不允许更新字段: {', '.join(sorted(invalid))}")
        for key, value in changes.items():
            setattr(record, key, value)
        record.updated_at = utc_now_naive()


__all__ = ["ExpertRepository"]
