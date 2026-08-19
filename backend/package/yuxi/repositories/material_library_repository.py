"""资料库、目录与来源文件的 PostgreSQL 访问边界。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import ExpertMaterial
from yuxi.storage.postgres.models_material import MaterialDirectory, MaterialLibrary, MaterialSourceFile, MaterialUnit
from yuxi.utils.datetime_utils import utc_now_naive


class MaterialLibraryRepository:
    """集中读写资料库文件处理事实。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def create_library(self, *, library_id: str, name: str, created_by: str) -> MaterialLibrary:
        """创建一个空资料库。"""

        library = MaterialLibrary(library_id=library_id, name=name, created_by=created_by)
        self.db.add(library)
        await self.db.flush()
        return library

    async def get_library(self, library_id: str) -> MaterialLibrary | None:
        """按 ID 读取资料库。"""

        result = await self.db.execute(select(MaterialLibrary).where(MaterialLibrary.library_id == library_id))
        return result.scalar_one_or_none()

    async def list_libraries(self) -> list[MaterialLibrary]:
        """按最近更新时间列出资料库。"""

        result = await self.db.execute(
            select(MaterialLibrary).order_by(MaterialLibrary.updated_at.desc(), MaterialLibrary.library_id.asc())
        )
        return list(result.scalars().all())

    async def create_directory(
        self,
        *,
        directory_id: str,
        library_id: str,
        parent_id: str | None,
        name: str,
        relative_path: str,
    ) -> MaterialDirectory:
        """创建一个目录事实。"""

        directory = MaterialDirectory(
            directory_id=directory_id,
            library_id=library_id,
            parent_id=parent_id,
            name=name,
            relative_path=relative_path,
        )
        self.db.add(directory)
        await self.db.flush()
        return directory

    async def ensure_directory(
        self,
        *,
        directory_id: str,
        library_id: str,
        parent_id: str | None,
        name: str,
        relative_path: str,
    ) -> MaterialDirectory:
        """原子创建目录，路径已存在时返回同一条目录事实。"""

        statement = (
            insert(MaterialDirectory)
            .values(
                directory_id=directory_id,
                library_id=library_id,
                parent_id=parent_id,
                name=name,
                relative_path=relative_path,
            )
            .on_conflict_do_nothing(index_elements=["library_id", "relative_path"])
            .returning(MaterialDirectory)
        )
        directory = (await self.db.execute(statement)).scalar_one_or_none()
        if directory is not None:
            return directory

        directory = await self.get_directory_by_path(library_id, relative_path)
        if directory is None:
            raise RuntimeError(f"目录创建后无法读取: {relative_path}")
        return directory

    async def get_directory(self, directory_id: str) -> MaterialDirectory | None:
        """按 ID 读取目录。"""

        result = await self.db.execute(select(MaterialDirectory).where(MaterialDirectory.directory_id == directory_id))
        return result.scalar_one_or_none()

    async def get_directory_by_path(self, library_id: str, relative_path: str) -> MaterialDirectory | None:
        """按资料库内相对路径读取目录。"""

        result = await self.db.execute(
            select(MaterialDirectory).where(
                MaterialDirectory.library_id == library_id,
                MaterialDirectory.relative_path == relative_path,
            )
        )
        return result.scalar_one_or_none()

    async def list_directories(self, library_id: str) -> list[MaterialDirectory]:
        """按相对路径稳定列出资料库目录。"""

        result = await self.db.execute(
            select(MaterialDirectory)
            .where(MaterialDirectory.library_id == library_id)
            .order_by(MaterialDirectory.relative_path.asc(), MaterialDirectory.directory_id.asc())
        )
        return list(result.scalars().all())

    async def directory_is_empty(self, directory_id: str) -> bool:
        """检查目录是否没有子目录和来源文件。"""

        child_count = await self.db.scalar(
            select(func.count()).select_from(MaterialDirectory).where(MaterialDirectory.parent_id == directory_id)
        )
        file_count = await self.db.scalar(
            select(func.count()).select_from(MaterialSourceFile).where(MaterialSourceFile.directory_id == directory_id)
        )
        return int(child_count or 0) == 0 and int(file_count or 0) == 0

    async def delete_directory(self, directory_id: str) -> None:
        """删除一个已确认为空的目录。"""

        await self.db.execute(delete(MaterialDirectory).where(MaterialDirectory.directory_id == directory_id))

    async def create_source_file(self, **values: Any) -> MaterialSourceFile:
        """创建一份等待解析的来源文件事实。"""

        source_file = MaterialSourceFile(**values)
        self.db.add(source_file)
        await self.db.flush()
        return source_file

    async def get_source_file(self, file_id: str) -> MaterialSourceFile | None:
        """按 ID 读取来源文件。"""

        result = await self.db.execute(select(MaterialSourceFile).where(MaterialSourceFile.file_id == file_id))
        return result.scalar_one_or_none()

    async def lock_source_file(self, file_id: str) -> MaterialSourceFile | None:
        """锁定来源文件，供引用建立、删除和重新章节化互斥使用。"""

        result = await self.db.execute(
            select(MaterialSourceFile).where(MaterialSourceFile.file_id == file_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_source_file_by_path(self, library_id: str, relative_path: str) -> MaterialSourceFile | None:
        """按资料库内相对路径读取来源文件。"""

        result = await self.db.execute(
            select(MaterialSourceFile).where(
                MaterialSourceFile.library_id == library_id,
                MaterialSourceFile.relative_path == relative_path,
            )
        )
        return result.scalar_one_or_none()

    async def list_source_files(self, library_id: str | None = None) -> list[MaterialSourceFile]:
        """列出全部来源文件，或只列出一个资料库的来源文件。"""

        statement = select(MaterialSourceFile)
        if library_id is not None:
            statement = statement.where(MaterialSourceFile.library_id == library_id)
        statement = statement.order_by(MaterialSourceFile.updated_at.desc(), MaterialSourceFile.file_id.asc())
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def list_source_files_by_statuses(self, statuses: set[str]) -> list[MaterialSourceFile]:
        """列出 Worker 启动时需要恢复的来源文件。"""

        if not statuses:
            return []
        result = await self.db.execute(
            select(MaterialSourceFile)
            .where(MaterialSourceFile.status.in_(statuses))
            .order_by(MaterialSourceFile.updated_at.asc(), MaterialSourceFile.file_id.asc())
        )
        return list(result.scalars().all())

    async def list_source_files_by_unit_statuses(self, statuses: set[str]) -> list[MaterialSourceFile]:
        """列出正文就绪但单元确认尚未结束的来源文件。"""

        if not statuses:
            return []
        result = await self.db.execute(
            select(MaterialSourceFile)
            .where(
                MaterialSourceFile.status == "ready",
                MaterialSourceFile.unit_status.in_(statuses),
            )
            .order_by(MaterialSourceFile.updated_at.asc(), MaterialSourceFile.file_id.asc())
        )
        return list(result.scalars().all())

    async def update_source_file(self, file_id: str, **changes: Any) -> MaterialSourceFile | None:
        """更新一份来源文件的状态或正文位置。"""

        source_file = await self.get_source_file(file_id)
        if source_file is None:
            return None
        immutable = {
            "file_id",
            "library_id",
            "directory_id",
            "original_filename",
            "relative_path",
            "file_type",
            "file_size",
            "source_bucket",
            "source_object",
            "created_by",
            "created_at",
            "updated_at",
        }
        column_names = {column.name for column in source_file.__table__.columns}
        invalid = (set(changes) - column_names) | (set(changes) & immutable)
        if invalid:
            raise ValueError(f"不允许更新字段: {', '.join(sorted(invalid))}")
        for key, value in changes.items():
            setattr(source_file, key, value)
        source_file.updated_at = utc_now_naive()
        await self.db.flush()
        return source_file

    async def delete_source_file(self, file_id: str) -> None:
        """删除一份来源文件事实。"""

        await self.db.execute(delete(MaterialSourceFile).where(MaterialSourceFile.file_id == file_id))

    async def create_material_units(self, file_id: str, units: list[dict[str, Any]]) -> list[MaterialUnit]:
        """一次写入一份来源文件已经机械校验的最终单元。"""

        records = [MaterialUnit(file_id=file_id, **unit) for unit in units]
        self.db.add_all(records)
        await self.db.flush()
        return records

    async def get_material_unit(self, unit_id: str) -> MaterialUnit | None:
        """按 ID 读取一个材料单元。"""

        result = await self.db.execute(select(MaterialUnit).where(MaterialUnit.unit_id == unit_id))
        return result.scalar_one_or_none()

    async def list_ready_material_units(
        self,
        library_id: str,
        *,
        file_id: str | None = None,
    ) -> list[tuple[MaterialUnit, MaterialSourceFile]]:
        """只列出来源和单元都已确认完成的可选材料。"""

        statement = (
            select(MaterialUnit, MaterialSourceFile)
            .join(MaterialSourceFile, MaterialSourceFile.file_id == MaterialUnit.file_id)
            .where(
                MaterialSourceFile.library_id == library_id,
                MaterialSourceFile.status == "ready",
                MaterialSourceFile.unit_status == "ready",
                MaterialUnit.status == "ready",
            )
        )
        if file_id is not None:
            statement = statement.where(MaterialUnit.file_id == file_id)
        result = await self.db.execute(
            statement.order_by(
                MaterialSourceFile.relative_path.asc(),
                MaterialUnit.start_line.asc(),
                MaterialUnit.unit_id.asc(),
            )
        )
        return [(row[0], row[1]) for row in result.all()]

    async def list_ready_material_units_by_ids(
        self,
        unit_ids: list[str],
    ) -> list[tuple[MaterialUnit, MaterialSourceFile]]:
        """读取一组来源和边界都已就绪的材料单元。"""

        if not unit_ids:
            return []
        result = await self.db.execute(
            select(MaterialUnit, MaterialSourceFile)
            .join(MaterialSourceFile, MaterialSourceFile.file_id == MaterialUnit.file_id)
            .where(
                MaterialUnit.unit_id.in_(unit_ids),
                MaterialUnit.status == "ready",
                MaterialSourceFile.status == "ready",
                MaterialSourceFile.unit_status == "ready",
            )
            .with_for_update()
        )
        return [(row[0], row[1]) for row in result.all()]

    async def source_file_has_expert_references(self, file_id: str) -> bool:
        """判断来源文件是否已经被专家项目材料引用。"""

        count = await self.db.scalar(
            select(func.count()).select_from(ExpertMaterial).where(ExpertMaterial.source_file_id == file_id)
        )
        return int(count or 0) > 0

    async def delete_material_units(self, file_id: str) -> None:
        """删除一份来源文件的边界确认结果。"""

        await self.db.execute(delete(MaterialUnit).where(MaterialUnit.file_id == file_id))


__all__ = ["MaterialLibraryRepository"]
