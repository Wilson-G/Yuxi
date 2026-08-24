"""资料库来源文件处理合同测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.material_library import service
from yuxi.material_library.boundary import MaterialBoundaryNeedsAttention, MaterialRange
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.models_material import MaterialUnit

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class FakeMinio:
    def __init__(self):
        self.uploads: list[dict] = []
        self.deleted_prefixes: list[tuple[str, str]] = []
        self.objects: dict[tuple[str, str], bytes] = {}

    async def aupload_file(self, **kwargs):
        self.uploads.append(kwargs)
        self.objects[(kwargs["bucket_name"], kwargs["object_name"])] = kwargs["data"]
        return SimpleNamespace(url="minio://stored")

    async def adownload_file(self, bucket_name: str, object_name: str) -> bytes:
        return self.objects[(bucket_name, object_name)]

    async def adelete_objects_by_prefix(self, bucket_name: str, prefix: str) -> int:
        self.deleted_prefixes.append((bucket_name, prefix))
        return 1


@pytest_asyncio.fixture()
async def material_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """创建服务和 Worker 共用的 SQLite 事实库。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'materials.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(service.pg_manager, "get_async_session_context", session_context)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _create_library(factory, library_id: str = "library-1") -> None:
    async with factory() as db:
        await MaterialLibraryRepository(db).create_library(
            library_id=library_id,
            name="测试资料库",
            created_by="admin-1",
        )
        await db.commit()


async def _upload_source(factory, fake_minio: FakeMinio, relative_path: str = "books/bazi/chapter.md") -> dict:
    async with factory() as db:
        return await service.create_material_source_file(
            db,
            library_id="library-1",
            relative_path=relative_path,
            filename="chapter.md",
            content=b"raw source",
            created_by="admin-1",
            minio_client=fake_minio,
        )


async def test_upload_and_process_preserve_path_and_store_complete_markdown(
    material_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create_library(material_env)
    fake_minio = FakeMinio()
    item = await _upload_source(material_env, fake_minio)
    file_id = item["file_id"]
    object_prefix = f"material-libraries/library-1/source-files/{file_id}"

    assert item["relative_path"] == "books/bazi/chapter.md"
    assert fake_minio.uploads == [
        {
            "bucket_name": "materials",
            "object_name": f"{object_prefix}/original/chapter.md",
            "data": b"raw source",
        }
    ]

    async with material_env() as db:
        repo = MaterialLibraryRepository(db)
        directories = await repo.list_directories("library-1")
        source_file = await repo.get_source_file(file_id)
    assert [(directory.relative_path, directory.parent_id) for directory in directories] == [
        ("books", None),
        ("books/bazi", directories[0].directory_id),
    ]
    assert source_file.source_bucket == "materials"
    assert source_file.source_object == f"{object_prefix}/original/chapter.md"

    captured = {}

    async def fake_parse_document(source: str, params: dict | None = None) -> str:
        captured["source"] = source
        captured["params"] = params
        return "第一行\r\n第二行\r\n"

    monkeypatch.setattr(service, "parse_document", fake_parse_document)
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    result = await service.process_material_source_file(file_id)

    assert result == {"file_id": file_id, "status": "ready", "unit_status": "pending"}
    assert captured == {
        "source": f"minio://materials/{object_prefix}/original/chapter.md",
        "params": {
            "image_bucket": "kb-images",
            "image_prefix": f"{object_prefix}/images",
        },
    }
    assert fake_minio.uploads[-1] == {
        "bucket_name": "materials",
        "object_name": f"{object_prefix}/normalized/content.md",
        "data": "第一行\n第二行\n".encode(),
        "content_type": "text/markdown",
    }

    async with material_env() as db:
        source_file = await MaterialLibraryRepository(db).get_source_file(file_id)
    assert source_file.status == "ready"
    assert source_file.unit_status == "pending"
    assert source_file.progress == 100
    assert source_file.line_count == 2
    assert source_file.markdown_bucket == "materials"
    assert source_file.markdown_object == f"{object_prefix}/normalized/content.md"
    assert source_file.error_message is None


async def test_parse_failure_never_marks_source_ready(
    material_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create_library(material_env)
    fake_minio = FakeMinio()
    item = await _upload_source(material_env, fake_minio, "failed.md")

    async def fail_parse(*_args, **_kwargs):
        raise ValueError("document conversion failed")

    monkeypatch.setattr(service, "parse_document", fail_parse)
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    result = await service.process_material_source_file(item["file_id"])

    assert result == {
        "file_id": item["file_id"],
        "status": "needs_attention",
        "error": "document conversion failed",
    }
    async with material_env() as db:
        source_file = await MaterialLibraryRepository(db).get_source_file(item["file_id"])
    assert source_file.status == "needs_attention"
    assert source_file.markdown_bucket is None
    assert source_file.markdown_object is None
    assert source_file.error_message == "document conversion failed"


async def test_whole_file_mode_creates_single_ready_unit_without_model(
    material_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create_library(material_env)
    fake_minio = FakeMinio()
    async with material_env() as db:
        item = await service.create_material_source_file(
            db,
            library_id="library-1",
            relative_path="books/notes.md",
            filename="notes.md",
            content=b"raw source",
            created_by="admin-1",
            minio_client=fake_minio,
            unit_mode=service.UNIT_MODE_WHOLE_FILE,
        )

    async def fake_parse_document(source: str, params: dict | None = None) -> str:
        return "第一行\n第二行\n"

    monkeypatch.setattr(service, "parse_document", fake_parse_document)
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    result = await service.process_material_source_file(item["file_id"])

    assert result == {"file_id": item["file_id"], "status": "ready", "unit_status": "ready"}
    async with material_env() as db:
        source_file = await MaterialLibraryRepository(db).get_source_file(item["file_id"])
        units = (await db.execute(select(MaterialUnit).where(MaterialUnit.file_id == item["file_id"]))).scalars().all()
    assert source_file.unit_mode == "whole_file"
    assert source_file.unit_status == "ready"
    assert [(unit.title, unit.start_line, unit.end_line, unit.status) for unit in units] == [
        ("notes", 1, 2, "ready")
    ]


async def test_create_source_file_rejects_unknown_unit_mode(material_env) -> None:
    await _create_library(material_env)
    fake_minio = FakeMinio()
    async with material_env() as db:
        with pytest.raises(ValueError, match="未知的单元确认方式"):
            await service.create_material_source_file(
                db,
                library_id="library-1",
                relative_path="books/notes.md",
                filename="notes.md",
                content=b"raw source",
                created_by="admin-1",
                minio_client=fake_minio,
                unit_mode="fast",
            )


async def test_delete_source_removes_private_objects_and_database_fact(material_env) -> None:
    await _create_library(material_env)
    fake_minio = FakeMinio()
    item = await _upload_source(material_env, fake_minio, "delete.md")
    prefix = f"material-libraries/library-1/source-files/{item['file_id']}"

    async with material_env() as db:
        await service.delete_material_source_file(
            db,
            library_id="library-1",
            file_id=item["file_id"],
            minio_client=fake_minio,
        )

    assert fake_minio.deleted_prefixes == [("materials", prefix), ("kb-images", prefix)]
    async with material_env() as db:
        assert await MaterialLibraryRepository(db).get_source_file(item["file_id"]) is None


async def test_retry_resets_only_selected_source_file(material_env) -> None:
    await _create_library(material_env)
    fake_minio = FakeMinio()
    failed = await _upload_source(material_env, fake_minio, "failed.md")
    untouched = await _upload_source(material_env, fake_minio, "untouched.md")

    async with material_env() as db:
        await MaterialLibraryRepository(db).update_source_file(
            failed["file_id"],
            status="needs_attention",
            progress=10,
            error_message="解析失败",
        )
        await db.commit()

    async with material_env() as db:
        result = await service.reset_material_source_file(
            db,
            library_id="library-1",
            file_id=failed["file_id"],
        )

    assert result["status"] == "pending"
    assert result["progress"] == 0
    assert result["error_message"] is None
    async with material_env() as db:
        untouched_source = await MaterialLibraryRepository(db).get_source_file(untouched["file_id"])
    assert untouched_source.status == "pending"


async def test_enqueue_failure_returns_updated_source_file_fact(material_env) -> None:
    """投递失败后返回数据库中的需要处理事实，不影响其他来源文件。"""

    await _create_library(material_env)
    fake_minio = FakeMinio()
    failed = await _upload_source(material_env, fake_minio, "failed.md")
    untouched = await _upload_source(material_env, fake_minio, "untouched.md")

    async with material_env() as db:
        result = await service.mark_material_source_enqueue_failed(
            db,
            file_id=failed["file_id"],
            error="来源文件任务投递失败: redis unavailable",
        )

    assert result["file_id"] == failed["file_id"]
    assert result["status"] == "needs_attention"
    assert result["error_message"] == "来源文件任务投递失败: redis unavailable"
    async with material_env() as db:
        untouched_source = await MaterialLibraryRepository(db).get_source_file(untouched["file_id"])
    assert untouched_source.status == "pending"
    assert untouched_source.error_message is None


async def test_boundary_confirmation_saves_only_ready_units_and_reads_complete_text(
    material_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create_library(material_env)
    fake_minio = FakeMinio()
    item = await _upload_source(material_env, fake_minio, "book.md")
    markdown = "# 第一主题\n第一主题正文\n# 第二主题\n第二主题正文\n"

    async def fake_parse_document(*_args, **_kwargs):
        return markdown

    async def fake_get_options(_db):
        return {"default_model": "provider:model"}

    async def fake_confirm_units(**_kwargs):
        return [
            MaterialRange(title="第一主题", start_line=1, end_line=2),
            MaterialRange(title="第二主题", start_line=3, end_line=4),
        ]

    monkeypatch.setattr(service, "parse_document", fake_parse_document)
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(service, "system_options", SimpleNamespace(get=fake_get_options))
    monkeypatch.setattr(service.material_boundary_tasks, "confirm_units", fake_confirm_units)

    await service.process_material_source_file(item["file_id"])
    result = await service.process_material_source_units(item["file_id"])

    assert result == {"file_id": item["file_id"], "unit_status": "ready", "unit_count": 2}
    async with material_env() as db:
        units = await service.list_material_units(db, library_id="library-1")
        second_text = await service.get_material_unit_text(
            db,
            library_id="library-1",
            unit_id=units[1]["unit_id"],
        )
    assert [(unit["title"], unit["start_line"], unit["end_line"]) for unit in units] == [
        ("第一主题", 1, 2),
        ("第二主题", 3, 4),
    ]
    assert second_text["content"] == "# 第二主题\n第二主题正文\n"

    async with material_env() as db:
        stored_units = list((await db.execute(select(MaterialUnit).order_by(MaterialUnit.start_line))).scalars())
        stored_units[1].status = "pending"
        await db.commit()
    async with material_env() as db:
        visible_units = await service.list_material_units(db, library_id="library-1")
    assert [unit["title"] for unit in visible_units] == ["第一主题"]


async def test_boundary_attention_keeps_complete_source_and_records_reason(
    material_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create_library(material_env)
    fake_minio = FakeMinio()
    item = await _upload_source(material_env, fake_minio, "incomplete.md")

    async def fake_parse_document(*_args, **_kwargs):
        return "# 未完成主题\n只有开头\n"

    async def fake_get_options(_db):
        return {"default_model": "provider:model"}

    async def fail_boundary(**_kwargs):
        raise MaterialBoundaryNeedsAttention("正文在主题结论前中断")

    monkeypatch.setattr(service, "parse_document", fake_parse_document)
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(service, "system_options", SimpleNamespace(get=fake_get_options))
    monkeypatch.setattr(service.material_boundary_tasks, "confirm_units", fail_boundary)

    await service.process_material_source_file(item["file_id"])
    result = await service.process_material_source_units(item["file_id"])

    assert result == {
        "file_id": item["file_id"],
        "unit_status": "needs_attention",
        "error": "正文在主题结论前中断",
    }
    async with material_env() as db:
        source_file = await MaterialLibraryRepository(db).get_source_file(item["file_id"])
        source_text = await service.get_material_source_text(
            db,
            library_id="library-1",
            file_id=item["file_id"],
        )
    assert source_file.status == "ready"
    assert source_file.unit_status == "needs_attention"
    assert source_text["content"] == "# 未完成主题\n只有开头\n"


async def test_retry_units_resets_only_selected_source(material_env) -> None:
    await _create_library(material_env)
    fake_minio = FakeMinio()
    selected = await _upload_source(material_env, fake_minio, "selected.md")
    untouched = await _upload_source(material_env, fake_minio, "untouched.md")

    async with material_env() as db:
        repo = MaterialLibraryRepository(db)
        for item in (selected, untouched):
            await repo.update_source_file(
                item["file_id"],
                status="ready",
                unit_status="ready",
                markdown_bucket="materials",
                markdown_object=f"{item['file_id']}.md",
                line_count=1,
            )
            await repo.create_material_units(
                item["file_id"],
                [
                    {
                        "unit_id": f"unit-{item['file_id']}",
                        "title": item["relative_path"],
                        "start_line": 1,
                        "end_line": 1,
                        "status": "ready",
                    }
                ],
            )
        await db.commit()

    async with material_env() as db:
        result = await service.reset_material_source_units(
            db,
            library_id="library-1",
            file_id=selected["file_id"],
        )

    assert result["unit_status"] == "pending"
    async with material_env() as db:
        remaining_units = list((await db.execute(select(MaterialUnit))).scalars())
        untouched_source = await MaterialLibraryRepository(db).get_source_file(untouched["file_id"])
    assert [unit.file_id for unit in remaining_units] == [untouched["file_id"]]
    assert untouched_source.unit_status == "ready"


async def test_referenced_source_cannot_be_deleted_or_rechapterized(material_env) -> None:
    await _create_library(material_env)
    fake_minio = FakeMinio()
    selected = await _upload_source(material_env, fake_minio, "selected.md")
    unit_id = f"unit-{selected['file_id']}"

    async with material_env() as db:
        library_repo = MaterialLibraryRepository(db)
        await library_repo.update_source_file(
            selected["file_id"],
            status="ready",
            unit_status="ready",
            markdown_bucket="materials",
            markdown_object="selected.md",
            line_count=1,
        )
        await library_repo.create_material_units(
            selected["file_id"],
            [{"unit_id": unit_id, "title": "已引用材料", "start_line": 1, "end_line": 1}],
        )
        expert_repo = ExpertRepository(db)
        await expert_repo.create_project(
            project_id="project-1",
            name="专家项目",
            description="",
            model_spec="provider:model",
            created_by="admin-1",
            status="ready",
        )
        await expert_repo.create_materials(
            "project-1",
            [
                {
                    "material_id": "expert-material-1",
                    "material_unit_id": unit_id,
                    "source_file_id": selected["file_id"],
                    "title": "已引用材料",
                    "source_path": "selected.md",
                    "source_start_line": 1,
                    "source_end_line": 1,
                    "raw_path": None,
                    "text_path": None,
                    "file_type": ".md",
                    "status": "ready",
                    "line_count": 1,
                }
            ],
        )
        await db.commit()

    async with material_env() as db:
        with pytest.raises(ValueError, match="已被专家项目引用"):
            await service.reset_material_source_units(
                db,
                library_id="library-1",
                file_id=selected["file_id"],
            )
        await db.rollback()
    async with material_env() as db:
        with pytest.raises(ValueError, match="已被专家项目引用"):
            await service.delete_material_source_file(
                db,
                library_id="library-1",
                file_id=selected["file_id"],
                minio_client=fake_minio,
            )
        await db.rollback()

    async with material_env() as db:
        assert await MaterialLibraryRepository(db).get_material_unit(unit_id) is not None
    assert fake_minio.deleted_prefixes == []
