"""资料库目录上传的 PostgreSQL 并发合同测试。"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.material_library.service import create_material_source_file
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.models_material import MaterialDirectory, MaterialLibrary, MaterialSourceFile

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class FakeMinio:
    """只记录上传成功，不引入对象存储并发变量。"""

    async def aupload_file(self, **_kwargs):
        return SimpleNamespace(url="minio://stored")


async def test_concurrent_uploads_create_shared_directory_path_once() -> None:
    """同一目录下的逐文件并发上传只能生成一条目录路径事实。"""

    database_url = os.environ["POSTGRES_URL"]
    schema_name = f"material_directory_test_{uuid4().hex}"
    setup_engine = create_async_engine(database_url)
    engine = None
    schema_created = False

    try:
        async with setup_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        schema_created = True

        engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection,
                    tables=[
                        MaterialLibrary.__table__,
                        MaterialDirectory.__table__,
                        MaterialSourceFile.__table__,
                    ],
                )
            )

        library_id = f"concurrent-{uuid4().hex}"
        upload_count = 6

        async with session_factory() as db:
            await MaterialLibraryRepository(db).create_library(
                library_id=library_id,
                name="并发目录测试",
                created_by="admin-1",
            )
            await db.commit()

        all_requests_saw_missing_root = asyncio.Barrier(upload_count)

        async def upload_file(index: int):
            async with session_factory() as db:
                repo = MaterialLibraryRepository(db)
                assert await repo.get_directory_by_path(library_id, "docs") is None
                await all_requests_saw_missing_root.wait()
                return await create_material_source_file(
                    db,
                    library_id=library_id,
                    relative_path=f"docs/intro/file-{index}.md",
                    filename=f"file-{index}.md",
                    content=f"file {index}".encode(),
                    created_by="admin-1",
                    minio_client=FakeMinio(),
                )

        results = await asyncio.wait_for(
            asyncio.gather(
                *(upload_file(index) for index in range(upload_count)),
                return_exceptions=True,
            ),
            timeout=10,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        assert not errors, [f"{type(error).__name__}: {error}" for error in errors]

        async with session_factory() as db:
            repo = MaterialLibraryRepository(db)
            directories = await repo.list_directories(library_id)
            source_files = await repo.list_source_files(library_id)

        assert [directory.relative_path for directory in directories] == ["docs", "docs/intro"]
        assert len(source_files) == upload_count
        assert {source_file.directory_id for source_file in source_files} == {directories[1].directory_id}
    finally:
        if engine is not None:
            await engine.dispose()
        if schema_created:
            async with setup_engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        await setup_engine.dispose()
