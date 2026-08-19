"""资料库、目录与来源文件的 PostgreSQL 访问边界。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_material import MaterialDirectory, MaterialLibrary, MaterialSourceFile
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


__all__ = ["MaterialLibraryRepository"]
