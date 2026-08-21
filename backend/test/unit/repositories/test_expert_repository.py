"""专家项目仓储单元测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.models_material import MaterialUnit  # noqa: F401

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _create_project(
    db: AsyncSession,
    *,
    project_id: str = "project-1",
    created_by: str = "user-1",
):
    return await ExpertRepository(db).create_project(
        project_id=project_id,
        name=f"专家项目 {project_id}",
        description="学习真实材料中的判断方法",
        model_spec="provider:model",
        created_by=created_by,
    )


def _material_payload(
    material_id: str,
    source_path: str,
    *,
    status: str = "ready",
    material_unit_id: str | None = None,
    source_file_id: str | None = None,
) -> dict:
    payload = {
        "material_id": material_id,
        "title": source_path,
        "source_path": source_path,
        "raw_path": f"raw/{source_path}",
        "text_path": f"materials/{material_id}.md",
        "file_type": ".md",
        "status": status,
        "token_count": 120,
        "line_count": 12,
    }
    if material_unit_id is not None:
        payload.update(
            material_unit_id=material_unit_id,
            source_file_id=source_file_id,
            source_start_line=1,
            source_end_line=12,
            raw_path=None,
            text_path=None,
        )
    return payload


async def _create_material_units(db: AsyncSession) -> None:
    """创建仓储唯一性测试使用的两个同源材料单元。"""

    repository = MaterialLibraryRepository(db)
    await repository.create_library(library_id="library-1", name="资料库", created_by="user-1")
    await repository.create_source_file(
        file_id="source-1",
        library_id="library-1",
        directory_id=None,
        original_filename="same.md",
        relative_path="same.md",
        file_type=".md",
        file_size=20,
        source_bucket="materials",
        source_object="sources/same.md",
        markdown_bucket="materials",
        markdown_object="normalized/same.md",
        status="ready",
        unit_status="ready",
        progress=100,
        line_count=24,
        error_message=None,
        created_by="user-1",
    )
    await repository.create_material_units(
        "source-1",
        [
            {"unit_id": "unit-1", "title": "第一单元", "start_line": 1, "end_line": 12},
            {"unit_id": "unit-2", "title": "第二单元", "start_line": 13, "end_line": 24},
        ],
    )


def _research_batch_payload(batch_id: str, title: str, material_ids: list[str]) -> dict:
    return {
        "batch_id": batch_id,
        "group_key": batch_id,
        "title": title,
        "rationale": "这些材料属于同一来源系列，应完整阅读后再提出候选主题。",
        "material_ids": material_ids,
        "status": "pending",
    }


def _topic_payload(
    topic_id: str,
    topic_key: str,
    *,
    position: int = 0,
    is_sample: bool = False,
) -> dict:
    return {
        "topic_id": topic_id,
        "topic_key": topic_key,
        "title": f"主题 {topic_key}",
        "section": "判断方法",
        "reader_question": "面对这个局面时应该先判断什么？",
        "purpose": "帮助读者识别局面并按材料中的顺序判断。",
        "boundaries": "资料不足时停止，不把案例断语写成通则。",
        "position": position,
        "material_ids": ["material-1"],
        "evidence_requirements": ["关键判断需要材料行号依据"],
        "is_sample": is_sample,
        "status": "pending",
    }


@pytest.mark.asyncio
async def test_project_reads_and_lists_are_scoped_by_creator(session: AsyncSession):
    repository = ExpertRepository(session)
    own_project = await _create_project(session, project_id="project-own", created_by="user-1")
    await _create_project(session, project_id="project-other", created_by="user-2")

    fetched = await repository.get_project("project-own", created_by="user-1")
    hidden = await repository.get_project("project-own", created_by="user-2")
    listed = await repository.list_projects(created_by="user-1")

    assert fetched is own_project
    assert hidden is None
    assert [project.project_id for project in listed] == ["project-own"]
    assert own_project.auto_advance is False
    assert own_project.compiler_profile_id == "apprenticeship"
    assert own_project.to_dict()["compiler_profile_id"] == "apprenticeship"
    assert own_project.to_dict()["auto_advance"] is False
    assert own_project.to_dict()["representative_material_ids"] == []
    assert own_project.to_dict()["prompt_pack"] == {}
    assert own_project.to_dict()["organization_version"] == 0
    assert own_project.to_dict()["planning_groups"] == []
    assert own_project.to_dict()["topic_map"] == {}


@pytest.mark.asyncio
async def test_project_update_saves_method_confirmation_fields(session: AsyncSession):
    repository = ExpertRepository(session)
    await _create_project(session)

    updated = await repository.update_project(
        "project-1",
        stage="learning",
        status="running",
        method_version=1,
        representative_material_ids=["material-1"],
        learning_plan="# 项目学习方案",
        prompt_pack={"note": "项目专属学艺 Prompt"},
        organization_version=1,
        planning_groups=[{"title": "同一老师的系列材料", "material_ids": ["material-1"]}],
        topic_map={"topics": [{"topic_key": "topic-1"}]},
        editorial_plan="# 当前编辑方案",
        organization_samples=[{"topic_id": "topic-1", "review": "pass"}],
    )

    assert updated is not None
    assert updated.stage == "learning"
    assert updated.method_version == 1
    assert updated.representative_material_ids == ["material-1"]
    assert updated.prompt_pack == {"note": "项目专属学艺 Prompt"}
    assert updated.organization_version == 1
    assert updated.topic_map == {"topics": [{"topic_key": "topic-1"}]}
    assert updated.editorial_plan == "# 当前编辑方案"
    with pytest.raises(ValueError, match="created_by"):
        await repository.update_project("project-1", created_by="user-2")
    with pytest.raises(ValueError, match="compiler_profile_id"):
        await repository.update_project("project-1", compiler_profile_id="reasoning_assets")


@pytest.mark.asyncio
async def test_repository_does_not_commit_service_transaction(session: AsyncSession):
    repository = ExpertRepository(session)
    await _create_project(session)

    await session.rollback()

    assert await repository.get_project("project-1") is None


@pytest.mark.asyncio
async def test_material_queries_lock_and_update_single_material(session: AsyncSession):
    repository = ExpertRepository(session)
    await _create_project(session)
    await repository.create_materials(
        "project-1",
        [
            _material_payload("material-2", "02-second.md", status="pending"),
            _material_payload("material-1", "01-first.md"),
        ],
    )

    materials = await repository.list_project_materials("project-1")
    ready = await repository.list_materials_by_status("project-1", "ready")
    locked = await repository.lock_material("material-1")
    updated = await repository.update_material(
        "material-1",
        status="passed",
        method_version=1,
        note="# 学艺笔记",
        note_contract={"disposition": "usable"},
        review={"verdict": "pass", "issues": []},
    )

    assert [material.material_id for material in materials] == ["material-1", "material-2"]
    assert [material.material_id for material in ready] == ["material-1"]
    assert locked is materials[0]
    assert updated is materials[0]
    assert updated.status == "passed"
    assert updated.note_contract == {"disposition": "usable"}
    assert updated.review == {"verdict": "pass", "issues": []}


@pytest.mark.asyncio
async def test_material_unit_is_unique_inside_project(session: AsyncSession):
    repository = ExpertRepository(session)
    await _create_project(session, project_id="project-1")
    await _create_project(session, project_id="project-2")
    await _create_material_units(session)
    await repository.create_materials(
        "project-1",
        [
            _material_payload(
                "material-1",
                "same.md",
                material_unit_id="unit-1",
                source_file_id="source-1",
            ),
            _material_payload(
                "material-2",
                "same.md",
                material_unit_id="unit-2",
                source_file_id="source-1",
            ),
        ],
    )
    await session.commit()

    with pytest.raises(IntegrityError):
        await repository.create_materials(
            "project-1",
            [
                _material_payload(
                    "material-duplicate",
                    "same.md",
                    material_unit_id="unit-1",
                    source_file_id="source-1",
                )
            ],
        )
    await session.rollback()

    created = await repository.create_materials(
        "project-2",
        [
            _material_payload(
                "material-other-project",
                "same.md",
                material_unit_id="unit-1",
                source_file_id="source-1",
            )
        ],
    )
    assert created[0].project_id == "project-2"


@pytest.mark.asyncio
async def test_replacing_research_batches_only_replaces_requested_version(session: AsyncSession):
    repository = ExpertRepository(session)
    await _create_project(session)
    await repository.replace_research_batches(
        "project-1",
        1,
        [_research_batch_payload("batch-v1-old", "旧版分组", ["material-1"])],
    )
    await repository.replace_research_batches(
        "project-1",
        2,
        [_research_batch_payload("batch-v2", "第二版分组", ["material-2"])],
    )

    replacement = await repository.replace_research_batches(
        "project-1",
        1,
        [_research_batch_payload("batch-v1-new", "新版分组", ["material-1", "material-3"])],
    )
    version_one = await repository.list_research_batches("project-1", 1)
    version_two = await repository.list_research_batches("project-1", 2)
    locked = await repository.lock_research_batch("batch-v1-new")
    updated = await repository.update_research_batch(
        "batch-v1-new",
        status="completed",
        result={"candidate_topics": ["真实问题一"]},
    )

    assert replacement == version_one
    assert [batch.batch_id for batch in version_one] == ["batch-v1-new"]
    assert [batch.batch_id for batch in version_two] == ["batch-v2"]
    assert locked is version_one[0]
    assert updated is version_one[0]
    assert updated.result == {"candidate_topics": ["真实问题一"]}
    assert updated.to_dict()["organization_version"] == 1


@pytest.mark.asyncio
async def test_replacing_topics_preserves_other_organization_versions(session: AsyncSession):
    repository = ExpertRepository(session)
    await _create_project(session)
    await repository.replace_topics(
        "project-1",
        1,
        [_topic_payload("topic-v1-old", "diagnosis", position=2)],
    )
    await repository.replace_topics(
        "project-1",
        2,
        [_topic_payload("topic-v2", "diagnosis", is_sample=True)],
    )

    await repository.replace_topics(
        "project-1",
        1,
        [
            _topic_payload("topic-v1-second", "action", position=2),
            _topic_payload("topic-v1-first", "diagnosis", position=1),
        ],
    )
    version_one = await repository.list_topics("project-1", 1)
    version_two = await repository.list_topics("project-1", 2)
    locked = await repository.lock_topic("topic-v1-first")
    updated = await repository.update_topic(
        "topic-v1-first",
        status="passed",
        evidence_pack={"anchors": ["material-1:L1-L3"]},
        handout="# 正式讲义",
        handout_contract={"decision": "ready"},
        review={"decision": "pass", "issues": []},
    )

    assert [topic.topic_id for topic in version_one] == ["topic-v1-first", "topic-v1-second"]
    assert [topic.topic_id for topic in version_two] == ["topic-v2"]
    assert locked is version_one[0]
    assert updated is version_one[0]
    assert updated.handout == "# 正式讲义"
    assert updated.review == {"decision": "pass", "issues": []}
    assert updated.to_dict()["evidence_pack"] == {"anchors": ["material-1:L1-L3"]}


@pytest.mark.asyncio
async def test_topic_key_is_unique_per_project_organization_version(session: AsyncSession):
    repository = ExpertRepository(session)
    await _create_project(session)
    await session.commit()

    with pytest.raises(IntegrityError):
        await repository.replace_topics(
            "project-1",
            1,
            [
                _topic_payload("topic-1", "same-key"),
                _topic_payload("topic-2", "same-key"),
            ],
        )
    await session.rollback()

    version_one = await repository.replace_topics(
        "project-1",
        1,
        [_topic_payload("topic-v1", "same-key")],
    )
    version_two = await repository.replace_topics(
        "project-1",
        2,
        [_topic_payload("topic-v2", "same-key")],
    )
    assert version_one[0].topic_key == version_two[0].topic_key


@pytest.mark.asyncio
async def test_replacing_organization_records_stays_in_service_transaction(session: AsyncSession):
    repository = ExpertRepository(session)
    await _create_project(session)
    await repository.replace_research_batches(
        "project-1",
        1,
        [_research_batch_payload("batch-old", "原研究批次", ["material-1"])],
    )
    await repository.replace_topics(
        "project-1",
        1,
        [_topic_payload("topic-old", "old-topic")],
    )
    await session.commit()

    await repository.replace_research_batches(
        "project-1",
        1,
        [_research_batch_payload("batch-new", "新研究批次", ["material-2"])],
    )
    await repository.replace_topics(
        "project-1",
        1,
        [_topic_payload("topic-new", "new-topic")],
    )
    await session.rollback()

    batches = await repository.list_research_batches("project-1", 1)
    topics = await repository.list_topics("project-1", 1)
    assert [batch.batch_id for batch in batches] == ["batch-old"]
    assert [topic.topic_id for topic in topics] == ["topic-old"]


@pytest.mark.asyncio
async def test_deleting_project_cascades_to_materials_research_batches_and_topics(session: AsyncSession):
    repository = ExpertRepository(session)
    project = await _create_project(session)
    await repository.create_materials(
        "project-1",
        [_material_payload("material-1", "source.md")],
    )
    await repository.replace_research_batches(
        "project-1",
        1,
        [_research_batch_payload("batch-1", "候选研究", ["material-1"])],
    )
    await repository.replace_topics(
        "project-1",
        1,
        [_topic_payload("topic-1", "topic-key")],
    )
    await session.commit()

    await session.delete(project)
    await session.commit()

    assert await repository.get_material("material-1") is None
    assert await repository.list_research_batches("project-1", 1) == []
    assert await repository.list_topics("project-1", 1) == []
