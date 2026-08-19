"""专家项目读取资料库单元和旧项目正文的兼容合同。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.expert import materials, publisher, storage
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.storage.postgres.models_business import Base

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

PROJECT_ID = "7" * 32


class FakeMinio:
    """返回测试中固定的规范化 Markdown。"""

    async def adownload_file(self, bucket_name: str, object_name: str) -> bytes:
        assert (bucket_name, object_name) == ("materials", "normalized/source.md")
        return "来源第一行\n单元第一行\n单元第二行\n单元第三行\n来源最后一行\n".encode()


@pytest_asyncio.fixture
async def material_unit_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """创建资料库和专家项目共享的 SQLite 事实库。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'expert-material-units.db'}")
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

    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(materials.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(materials, "get_minio_client", lambda: FakeMinio())
    try:
        yield factory
    finally:
        await engine.dispose()


async def test_material_unit_reads_complete_range_and_maps_evidence_to_source_lines(material_unit_env) -> None:
    async with material_unit_env() as db:
        library_repo = MaterialLibraryRepository(db)
        await library_repo.create_library(library_id="library-1", name="资料库", created_by="admin-1")
        await library_repo.create_source_file(
            file_id="source-1",
            library_id="library-1",
            directory_id=None,
            original_filename="source.md",
            relative_path="books/source.md",
            file_type=".md",
            file_size=100,
            source_bucket="materials",
            source_object="original/source.md",
            markdown_bucket="materials",
            markdown_object="normalized/source.md",
            status="ready",
            unit_status="ready",
            progress=100,
            line_count=5,
            error_message=None,
            created_by="admin-1",
        )
        await library_repo.create_material_units(
            "source-1",
            [{"unit_id": "unit-1", "title": "自然主题", "start_line": 2, "end_line": 4}],
        )
        expert_repo = ExpertRepository(db)
        project = await expert_repo.create_project(
            project_id=PROJECT_ID,
            name="资料库项目",
            description="",
            model_spec="provider:model",
            created_by="admin-1",
            status="ready",
        )
        project.organization_version = 1
        [material] = await expert_repo.create_materials(
            PROJECT_ID,
            [
                {
                    "material_id": "material-1",
                    "material_unit_id": "unit-1",
                    "source_file_id": "source-1",
                    "title": "自然主题",
                    "source_path": "books/source.md",
                    "source_start_line": 2,
                    "source_end_line": 4,
                    "raw_path": None,
                    "text_path": None,
                    "file_type": ".md",
                    "status": "passed",
                    "line_count": 3,
                }
            ],
        )
        await db.commit()

    source_text = await materials.load_expert_material_source_text(material)
    assert await materials.read_expert_material_text(material) == "单元第一行\n单元第二行\n单元第三行\n"
    assert (
        materials.format_expert_material_excerpt(
            material,
            source_text,
            start_line=2,
            end_line=3,
            source_line_numbers=True,
        )
        == "L3: 单元第二行\nL4: 单元第三行"
    )
    assert materials.map_expert_material_source_lines(material, start_line=2, end_line=3) == (3, 4)

    storage.create_project_output_workspace(PROJECT_ID)
    topic = SimpleNamespace(
        topic_key="natural-topic",
        title="自然主题讲义",
        reader_question="怎样判断？",
        position=1,
        handout="# 自然主题讲义",
        handout_contract={
            "citations": [
                {
                    "note_alias": "N001",
                    "start_line": 2,
                    "end_line": 3,
                    "supports": "单元中的关键判断",
                }
            ]
        },
    )
    relative = publisher.build_candidate_skill(
        project=project,
        materials=[material],
        material_source_texts={"material-1": source_text},
        topics=[topic],
        navigation="进入自然主题讲义。",
        skill_body="按材料中的判断条件回答。",
    )
    candidate = storage.get_project_root(PROJECT_ID) / relative
    [source_row] = [json.loads(line) for line in (candidate / "sources.jsonl").read_text().splitlines()]
    assert (source_row["start_line"], source_row["end_line"]) == (3, 4)
    reference = (candidate / source_row["reference_path"]).read_text()
    assert "位置：L3-L4" in reference
    assert "L3: 单元第二行" in reference


async def test_legacy_project_material_keeps_reading_existing_text_record(
    material_unit_env,
) -> None:
    project_root = storage.create_project_workspace(PROJECT_ID)
    text_path = "text/legacy.md"
    (project_root / text_path).write_text("旧项目可靠正文", encoding="utf-8")
    legacy = SimpleNamespace(
        project_id=PROJECT_ID,
        material_id="legacy-material",
        material_unit_id=None,
        source_file_id=None,
        source_start_line=None,
        source_end_line=None,
        text_path=text_path,
        line_count=1,
    )

    assert await materials.read_expert_material_text(legacy) == "旧项目可靠正文"
