"""专家项目主题组织与第二次确认测试。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.expert import organization_service, storage
from yuxi.expert.contracts import (
    HandoutDraftResult,
    HandoutReviewResult,
    PlanningGroupsResult,
    TopicEvidencePack,
    TopicMapResult,
    TopicResearchResult,
)
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.storage.postgres.models_business import Base

pytestmark = pytest.mark.unit

PROJECT_ID = "3" * 32
REAL_ASYNCIO_SLEEP = asyncio.sleep


@pytest_asyncio.fixture
async def organization_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """创建主题组织服务使用的 SQLite 与项目目录。"""

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'expert-organization.db'}",
        connect_args={"timeout": 30},
    )
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
    monkeypatch.setattr(organization_service.pg_manager, "get_async_session_context", session_context)
    try:
        yield SimpleNamespace(factory=factory, save_dir=tmp_path)
    finally:
        await engine.dispose()


def _prompt_pack() -> dict[str, str]:
    return {
        "note_author": "note author prompt",
        "note_reviewer": "note reviewer prompt",
        "note_reviser": "note reviser prompt",
        "topic_researcher": "topic researcher prompt",
        "topic_editor": "topic editor prompt",
        "handout_editor": "handout editor prompt",
        "handout_reviewer": "handout reviewer prompt",
        "handout_reviser": "handout reviser prompt",
        "book_editor": "book editor prompt",
        "navigation_author": "navigation author prompt",
        "skill_author": "skill author prompt",
    }


async def _seed_organization_project(
    organization_env,
    *,
    material_count: int,
    auto_advance: bool = False,
) -> list[str]:
    storage.create_project_workspace(PROJECT_ID)
    async with organization_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.create_project(
            project_id=PROJECT_ID,
            name="组织方法项目",
            description="把通过 Review 的笔记组织为主题讲义",
            model_spec="provider:model",
            created_by="user-1",
            stage="organization",
            status="preparing",
        )
        project.method_version = 1
        project.auto_advance = auto_advance
        project.learning_plan = "# 已确认的项目学习方案"
        project.prompt_pack = _prompt_pack()
        materials = []
        for index in range(1, material_count + 1):
            material_id = f"material-{index}"
            text_path = f"text/{material_id}.md"
            (storage.get_project_root(PROJECT_ID) / text_path).write_text(
                f"SOURCE-{index}-FIRST\nSOURCE-{index}-MIDDLE\nSOURCE-{index}-LAST",
                encoding="utf-8",
            )
            materials.append(
                {
                    "material_id": material_id,
                    "title": f"材料 {index}",
                    "source_path": f"series/{index:02d}.md",
                    "raw_path": f"raw/{material_id}.md",
                    "text_path": text_path,
                    "file_type": ".md",
                    "status": "passed",
                    "line_count": 3,
                    "token_count": 20,
                    "method_version": 1,
                    "note": f"# 笔记 {index}\n判断条件 {index}",
                    "note_contract": {
                        "decision": "learned",
                        "note_markdown": f"# 笔记 {index}",
                        "routing_labels": [f"标签 {index}"],
                        "candidate_questions": [f"问题 {index}"],
                        "evidence": [
                            {
                                "material_alias": "CURRENT_MATERIAL",
                                "start_line": 1,
                                "end_line": 3,
                                "supports": f"材料 {index} 的判断条件",
                            }
                        ],
                    },
                    "review": {"decision": "pass", "summary": "通过", "issues": []},
                }
            )
        await repo.create_materials(PROJECT_ID, materials)
        await db.commit()
    return [f"material-{index}" for index in range(1, material_count + 1)]


class InvalidGroupingTasks:
    """只返回指定规划分组，用于验证模型范围错误立即失败。"""

    def __init__(self, grouping: PlanningGroupsResult):
        self.grouping = grouping

    async def plan_note_groups(self, **_kwargs) -> PlanningGroupsResult:
        return self.grouping


def test_note_catalog_uses_only_source_and_routing_labels() -> None:
    material = SimpleNamespace(
        material_id="material-1",
        title="不应进入首轮目录的标题",
        source_path="series/source-1.md",
        note_contract={
            "routing_labels": ["判断条件", "停止线"],
            "candidate_questions": ["不应作为首轮分组依据的问题"],
        },
    )

    catalog = organization_service._build_note_catalog([material], {material.material_id: "N001"})

    assert catalog == "N001 | 来源：series/source-1.md | Labels：判断条件；停止线"
    assert "候选问题" not in catalog
    assert "不应作为首轮分组依据的问题" not in catalog
    assert material.title not in catalog


class OrganizationModelTasks:
    """记录并发候选研究，并完成一篇经过一次修订的小样。"""

    def __init__(self):
        self.group_aliases = {
            "signal-a": ["N001", "N002"],
            "signal-b": ["N003", "N004"],
        }
        self.research_active = 0
        self.research_max_active = 0
        self.research_started: list[str] = []
        self.review_calls = 0
        self.revision_calls = 0
        self.evidence_topic = ""
        self.handout_topic = ""
        self.writer_notes = ""
        self.writer_editorial_plan = ""
        self.reviewer_notes: list[str] = []
        self.reviewer_editorial_plans: list[str] = []
        self.reviser_notes = ""
        self.reviser_editorial_plan = ""

    async def plan_note_groups(self, **_kwargs) -> PlanningGroupsResult:
        return PlanningGroupsResult(
            groups=[
                {
                    "group_key": "signal-a",
                    "title": "信号 A 的判断范围",
                    "rationale": "相近判断路标召回的完整范围",
                    "note_aliases": self.group_aliases["signal-a"],
                },
                {
                    "group_key": "signal-b",
                    "title": "信号 B 的判断范围",
                    "rationale": "相近判断路标召回的完整范围",
                    "note_aliases": self.group_aliases["signal-b"],
                },
            ]
        )

    async def research_topic_candidates(self, *, group_key: str, **_kwargs) -> TopicResearchResult:
        self.research_started.append(group_key)
        self.research_active += 1
        self.research_max_active = max(self.research_max_active, self.research_active)
        await REAL_ASYNCIO_SLEEP(0.03)
        self.research_active -= 1
        return TopicResearchResult(
            group_key=group_key,
            candidates=[
                {
                    "candidate_key": f"candidate-{group_key}",
                    "reader_question": f"{group_key} 应该怎样判断？",
                    "rationale": "笔记回答同一类真实问题",
                    "note_aliases": self.group_aliases[group_key],
                }
            ],
            group_findings=f"{group_key} 的候选研究",
        )

    async def edit_topic_map(self, **_kwargs) -> TopicMapResult:
        return TopicMapResult(
            plan_markdown="# 主题与讲义规划",
            editorial_plan_markdown="# 当前编辑方案",
            topics=[
                {
                    "topic_key": "diagnosis",
                    "title": "识别局面与判断顺序",
                    "section": "判断方法",
                    "reader_question": "面对局面时先看什么？",
                    "purpose": "帮助读者识别局面并按顺序判断",
                    "boundaries": "材料不足时停止",
                    "note_aliases": ["N001", "N002"],
                    "unresolved_questions": ["停止条件应由哪一段原文支撑？"],
                    "order": 1,
                },
                {
                    "topic_key": "action",
                    "title": "条件成立后的行动",
                    "section": "行动边界",
                    "reader_question": "条件成立后怎样行动？",
                    "purpose": "说明行动选择与代价",
                    "boundaries": "不把个案改成通则",
                    "note_aliases": ["N003"],
                    "order": 2,
                },
            ],
            unassigned_notes=[{"note_alias": "N004", "reason": "只有个案信息，不足以形成正式主题"}],
            sample_topic_keys=["diagnosis"],
            sample_selection_reason="同时覆盖判断顺序和材料边界",
        )

    async def prepare_topic_evidence(self, *, topic: str, **_kwargs) -> TopicEvidencePack:
        self.evidence_topic = topic
        return TopicEvidencePack(
            topic_key="diagnosis",
            evidence=[
                {
                    "note_alias": "N001",
                    "start_line": 1,
                    "end_line": 2,
                    "supports": "先识别局面再判断",
                }
            ],
        )

    async def write_handout(
        self,
        *,
        topic: str,
        notes: str,
        editorial_plan: str,
        **_kwargs,
    ) -> HandoutDraftResult:
        self.handout_topic = topic
        self.writer_notes = notes
        self.writer_editorial_plan = editorial_plan
        return HandoutDraftResult(
            topic_key="diagnosis",
            handout_markdown="# 初版讲义\n先识别局面。",
            citations=[
                {
                    "note_alias": "N001",
                    "start_line": 1,
                    "end_line": 2,
                    "supports": "先识别局面再判断",
                }
            ],
            editorial_plan_markdown="# 初版写后编辑方案",
        )

    async def review_handout(self, *, notes: str, editorial_plan: str, **_kwargs) -> HandoutReviewResult:
        self.reviewer_notes.append(notes)
        self.reviewer_editorial_plans.append(editorial_plan)
        self.review_calls += 1
        if self.review_calls == 1:
            return HandoutReviewResult(
                decision="revise",
                summary="需要补回停止条件",
                issues=[
                    {
                        "problem": "停止条件缺失",
                        "affected_part": "判断步骤",
                        "source_lines": ["L2"],
                        "revision_direction": "补回材料中的停止条件",
                    }
                ],
            )
        return HandoutReviewResult(decision="pass", summary="修订后通过", issues=[])

    async def revise_handout(self, *, notes: str, editorial_plan: str, **_kwargs) -> HandoutDraftResult:
        self.reviser_notes = notes
        self.reviser_editorial_plan = editorial_plan
        self.revision_calls += 1
        return HandoutDraftResult(
            topic_key="diagnosis",
            handout_markdown="# 修订讲义\n先识别局面，材料不足时停止。",
            citations=[
                {
                    "note_alias": "N001",
                    "start_line": 1,
                    "end_line": 2,
                    "supports": "先识别局面再判断",
                }
            ],
            editorial_plan_markdown="# 修订通过后的编辑方案",
        )


class MiddleUnassignedOrganizationTasks(OrganizationModelTasks):
    """让中间别名不入选，验证后续别名和主题材料映射保持稳定。"""

    async def edit_topic_map(self, **_kwargs) -> TopicMapResult:
        return TopicMapResult(
            plan_markdown="# 主题与讲义规划",
            editorial_plan_markdown="# 当前编辑方案",
            topics=[
                {
                    "topic_key": "diagnosis",
                    "title": "识别局面与判断顺序",
                    "section": "判断方法",
                    "reader_question": "面对局面时先看什么？",
                    "purpose": "帮助读者识别局面并按顺序判断",
                    "boundaries": "材料不足时停止",
                    "note_aliases": ["N001"],
                    "order": 1,
                },
                {
                    "topic_key": "action",
                    "title": "条件成立后的行动",
                    "section": "行动边界",
                    "reader_question": "条件成立后怎样行动？",
                    "purpose": "说明行动选择与代价",
                    "boundaries": "不把个案改成通则",
                    "note_aliases": ["N003", "N004"],
                    "order": 2,
                },
            ],
            unassigned_notes=[{"note_alias": "N002", "reason": "只有个案信息，不足以形成正式主题"}],
            sample_topic_keys=["diagnosis"],
            sample_selection_reason="覆盖核心判断顺序",
        )


class AllUnassignedOrganizationTasks(OrganizationModelTasks):
    """让总编明确拒绝全部已审笔记，不强行生成主题或小样。"""

    async def edit_topic_map(self, **_kwargs) -> TopicMapResult:
        return TopicMapResult(
            plan_markdown="# 无法形成可信主题",
            editorial_plan_markdown="# 当前编辑判断",
            topics=[],
            unassigned_notes=[
                {"note_alias": alias, "reason": f"{alias} 只有孤立个案，无法形成可信方法"}
                for alias in ("N001", "N002", "N003", "N004")
            ],
            sample_topic_keys=[],
            sample_selection_reason="没有正式主题可供试写",
        )


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        (
            [
                {
                    "group_key": "bad",
                    "title": "错误范围",
                    "rationale": "引用未知笔记",
                    "note_aliases": ["N001", "N999"],
                }
            ],
            "未知或空",
        ),
        (
            [
                {
                    "group_key": "partial",
                    "title": "遗漏范围",
                    "rationale": "只覆盖第一份笔记",
                    "note_aliases": ["N001"],
                }
            ],
            "没有覆盖全部",
        ),
    ],
)
@pytest.mark.asyncio
async def test_planning_groups_fail_fast_on_unknown_or_omitted_passed_notes(
    organization_env,
    groups,
    message,
) -> None:
    await _seed_organization_project(organization_env, material_count=2)
    version = await organization_service.initialize_organization_stage(PROJECT_ID)

    with pytest.raises(ValueError, match=message):
        await organization_service.run_organization_stage(
            PROJECT_ID,
            version,
            model_tasks=InvalidGroupingTasks(PlanningGroupsResult(groups=groups)),
        )

    async with organization_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(PROJECT_ID)
        batches = await repo.list_research_batches(PROJECT_ID, version)
    assert project is not None and project.planning_groups == []
    assert batches == []


def test_topic_map_requires_assigned_and_unassigned_notes_to_be_disjoint() -> None:
    overlapping = TopicMapResult(
        plan_markdown="# 规划",
        editorial_plan_markdown="# 编辑方案",
        topics=[
            {
                "topic_key": "topic-a",
                "title": "主题 A",
                "section": "方法",
                "reader_question": "如何判断？",
                "purpose": "形成判断",
                "boundaries": "材料不足时停止",
                "note_aliases": ["N001"],
                "order": 1,
            }
        ],
        unassigned_notes=[{"note_alias": "N001", "reason": "不应同时未分配"}],
        sample_topic_keys=["topic-a"],
        sample_selection_reason="代表核心方法",
    )

    with pytest.raises(ValueError, match="不能同时"):
        organization_service._validate_topic_map(overlapping, {"N001"})


def test_topic_map_allows_all_notes_to_be_explicitly_unassigned_without_samples() -> None:
    result = TopicMapResult(
        plan_markdown="# 无法形成主题",
        editorial_plan_markdown="# 编辑判断",
        topics=[],
        unassigned_notes=[
            {"note_alias": "N001", "reason": "只有孤立事实"},
            {"note_alias": "N002", "reason": "没有可迁移判断"},
        ],
        sample_topic_keys=[],
        sample_selection_reason="没有主题可供试写",
    )

    organization_service._validate_topic_map(result, {"N001", "N002"})


def test_evidence_pack_may_span_adjacent_approved_note_anchors() -> None:
    material = SimpleNamespace(
        line_count=6,
        note_contract={
            "evidence": [
                {"start_line": 2, "end_line": 3},
                {"start_line": 4, "end_line": 5},
            ]
        },
    )
    pack = TopicEvidencePack(
        topic_key="topic-a",
        evidence=[
            {
                "note_alias": "N001",
                "start_line": 2,
                "end_line": 5,
                "supports": "相邻的两条已审依据共同支撑当前判断",
            }
        ],
    )

    organization_service._validate_evidence_pack(pack, "topic-a", {"N001": material})


def test_handout_citations_may_select_subranges_of_evidence_pack() -> None:
    evidence_pack = TopicEvidencePack(
        topic_key="topic-a",
        evidence=[
            {
                "note_alias": "N001",
                "start_line": 2,
                "end_line": 6,
                "supports": "获准原文范围",
            }
        ],
    )
    handout = HandoutDraftResult(
        topic_key="topic-a",
        handout_markdown="# 讲义",
        citations=[
            {
                "note_alias": "N001",
                "start_line": 3,
                "end_line": 4,
                "supports": "讲义只引用其中必要的精确行",
            }
        ],
        editorial_plan_markdown="# 编辑方案",
    )

    organization_service.validate_handout_contract(handout, "topic-a", evidence_pack)


def test_topic_definition_passes_retry_feedback_to_editor() -> None:
    topic = SimpleNamespace(
        topic_key="topic-a",
        title="主题 A",
        section="方法",
        reader_question="怎样判断？",
        purpose="形成可靠判断",
        boundaries="材料不足时停止",
        evidence_requirements=[],
        error_message="结语必须区分受引导回应和直接回答",
        position=1,
    )

    formatted = organization_service.format_topic_definition(topic)

    assert "结语必须区分受引导回应和直接回答" in formatted


@pytest.mark.asyncio
async def test_full_notes_rewrite_material_task_alias_to_current_note_alias(organization_env) -> None:
    await _seed_organization_project(organization_env, material_count=1)
    async with organization_env.factory() as db:
        material = (await ExpertRepository(db).list_project_materials(PROJECT_ID))[0]

    material.note_contract["evidence"][0]["material_alias"] = "CURRENT_MATERIAL"
    formatted = organization_service.format_full_notes([material], {material.material_id: "N001"})

    assert '"material_alias": "N001"' in formatted
    assert "CURRENT_MATERIAL" not in formatted


@pytest.mark.asyncio
async def test_handout_input_uses_complete_notes_and_only_selected_reliable_text_lines(
    organization_env,
) -> None:
    await _seed_organization_project(organization_env, material_count=1)
    async with organization_env.factory() as db:
        materials = await ExpertRepository(db).list_project_materials(PROJECT_ID)

    evidence_pack = TopicEvidencePack(
        topic_key="diagnosis",
        evidence=[
            {
                "note_alias": "N001",
                "start_line": 1,
                "end_line": 2,
                "supports": "先识别局面再判断",
            }
        ],
    )

    result = organization_service.format_handout_notes_with_evidence(
        PROJECT_ID,
        materials,
        {materials[0].material_id: "N001"},
        evidence_pack,
    )

    assert "# 笔记 1\n判断条件 1" in result
    assert "L1: SOURCE-1-FIRST\nL2: SOURCE-1-MIDDLE" in result
    assert "SOURCE-1-LAST" not in result


@pytest.mark.asyncio
async def test_handout_input_fails_fast_when_selected_source_lines_are_out_of_range(
    organization_env,
) -> None:
    await _seed_organization_project(organization_env, material_count=1)
    async with organization_env.factory() as db:
        materials = await ExpertRepository(db).list_project_materials(PROJECT_ID)
    evidence_pack = TopicEvidencePack(
        topic_key="diagnosis",
        evidence=[
            {
                "note_alias": "N001",
                "start_line": 2,
                "end_line": 4,
                "supports": "越界证据",
            }
        ],
    )

    with pytest.raises(ValueError, match="超出可靠正文范围"):
        organization_service.format_handout_notes_with_evidence(
            PROJECT_ID,
            materials,
            {materials[0].material_id: "N001"},
            evidence_pack,
        )


@pytest.mark.asyncio
async def test_organization_researches_groups_concurrently_and_confirms_only_after_review(
    organization_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_organization_project(organization_env, material_count=4)
    version = await organization_service.initialize_organization_stage(PROJECT_ID)

    async with organization_env.factory() as db:
        with pytest.raises(ValueError, match="没有等待确认"):
            await organization_service.confirm_organization_stage(
                db,
                project_id=PROJECT_ID,
                created_by="user-1",
            )
        await db.rollback()

    monkeypatch.setattr(organization_service, "ORGANIZATION_RESEARCH_CONCURRENCY", 2)
    tasks = OrganizationModelTasks()
    result = await organization_service.run_organization_stage(
        PROJECT_ID,
        version,
        model_tasks=tasks,
    )

    async with organization_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(PROJECT_ID)
        batches = await repo.list_research_batches(PROJECT_ID, version)
        topics = await repo.list_topics(PROJECT_ID, version)
    assert result["status"] == "awaiting_confirmation"
    assert tasks.research_max_active == 2
    assert set(tasks.research_started) == {"signal-a", "signal-b"}
    assert {batch.group_key: batch.status for batch in batches} == {
        "signal-a": "completed",
        "signal-b": "completed",
    }
    assert all(batch.result["group_key"] == batch.group_key for batch in batches)
    assert project is not None
    assert project.status == "awaiting_confirmation"
    assert project.topic_map["unassigned_notes"] == [
        {"note_alias": "N004", "reason": "只有个案信息，不足以形成正式主题"}
    ]
    assert len(project.organization_samples) == 1
    assert project.organization_samples[0]["status"] == "passed"
    assert tasks.revision_calls == 1
    assert tasks.review_calls == 2
    assert "停止条件应由哪一段原文支撑？" in tasks.evidence_topic
    assert "停止条件应由哪一段原文支撑？" in tasks.handout_topic
    assert tasks.writer_notes == tasks.reviser_notes
    assert tasks.reviewer_notes == [tasks.writer_notes, tasks.writer_notes]
    assert tasks.writer_editorial_plan == "# 当前编辑方案"
    assert tasks.reviser_editorial_plan == "# 初版写后编辑方案"
    assert tasks.reviewer_editorial_plans == ["# 初版写后编辑方案", "# 修订通过后的编辑方案"]
    assert "L1: SOURCE-1-FIRST\nL2: SOURCE-1-MIDDLE" in tasks.writer_notes
    assert "SOURCE-1-LAST" not in tasks.writer_notes
    assert {topic.topic_key: (topic.is_sample, topic.status, topic.revision_count) for topic in topics} == {
        "diagnosis": (True, "passed", 1),
        "action": (False, "pending", 0),
    }

    async with organization_env.factory() as db:
        confirmed_version = await organization_service.confirm_organization_stage(
            db,
            project_id=PROJECT_ID,
            created_by="user-1",
        )

    async with organization_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(PROJECT_ID)
        topics = await repo.list_topics(PROJECT_ID, version)
    assert confirmed_version == version
    assert project is not None
    assert project.editorial_plan == "# 修订通过后的编辑方案"
    assert project.stage == "handout_compilation"
    assert project.status == "running"
    assert {topic.topic_key: topic.status for topic in topics} == {
        "diagnosis": "passed",
        "action": "pending",
    }
    assert json.loads(json.dumps(project.topic_map, ensure_ascii=False))["sample_topic_keys"] == ["diagnosis"]


@pytest.mark.asyncio
async def test_auto_advance_freezes_reviewed_organization_without_renumbering_unassigned_note(
    organization_env,
) -> None:
    material_ids = await _seed_organization_project(
        organization_env,
        material_count=4,
        auto_advance=True,
    )
    version = await organization_service.initialize_organization_stage(PROJECT_ID)

    result = await organization_service.run_organization_stage(
        PROJECT_ID,
        version,
        model_tasks=MiddleUnassignedOrganizationTasks(),
    )

    async with organization_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(PROJECT_ID)
        materials = await repo.list_project_materials(PROJECT_ID)
        topics = await repo.list_topics(PROJECT_ID, version)

    assert result["stage"] == "handout_compilation"
    assert result["status"] == "running"
    assert project is not None
    assert project.stage == "handout_compilation"
    assert project.status == "running"
    assert project.organization_confirmed_at is not None
    assert project.topic_map["unassigned_notes"] == [
        {"note_alias": "N002", "reason": "只有个案信息，不足以形成正式主题"}
    ]
    assert [material.status for material in materials] == ["passed"] * 4
    assert next(topic for topic in topics if topic.topic_key == "action").material_ids == [
        material_ids[2],
        material_ids[3],
    ]


@pytest.mark.asyncio
async def test_all_unassigned_notes_are_persisted_and_stop_organization(organization_env) -> None:
    await _seed_organization_project(organization_env, material_count=4, auto_advance=True)
    version = await organization_service.initialize_organization_stage(PROJECT_ID)

    result = await organization_service.run_organization_stage(
        PROJECT_ID,
        version,
        model_tasks=AllUnassignedOrganizationTasks(),
    )

    async with organization_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(PROJECT_ID)
        materials = await repo.list_project_materials(PROJECT_ID)
        topics = await repo.list_topics(PROJECT_ID, version)

    assert result["status"] == "needs_attention"
    assert project is not None
    assert project.stage == "organization"
    assert project.status == "needs_attention"
    assert len(project.topic_map["unassigned_notes"]) == 4
    assert "无法形成可信专家 Skill" in project.error_message
    assert [material.status for material in materials] == ["passed"] * 4
    assert topics == []


@pytest.mark.asyncio
async def test_organization_retry_preserves_sample_review_failure(organization_env) -> None:
    """组织任务重投时保留已经保存的讲义小样 Review 原因。"""

    await _seed_organization_project(organization_env, material_count=4)
    version = await organization_service.initialize_organization_stage(PROJECT_ID)
    tasks = OrganizationModelTasks()
    await organization_service.run_organization_stage(PROJECT_ID, version, model_tasks=tasks)

    async with organization_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.lock_project(PROJECT_ID)
        topics = await repo.list_topics(PROJECT_ID, version)
        sample = next(topic for topic in topics if topic.is_sample)
        assert project is not None
        project.status = "running"
        sample.status = "needs_attention"
        sample.error_message = "Review 仍要求补回判断条件"
        await db.commit()

    result = await organization_service.run_organization_stage(
        PROJECT_ID,
        version,
        model_tasks=tasks,
    )

    async with organization_env.factory() as db:
        project = await ExpertRepository(db).get_project(PROJECT_ID)

    assert result["status"] == "needs_attention"
    assert project is not None
    assert project.organization_samples[0]["reason"] == "Review 仍要求补回判断条件"
    assert project.error_message == "Review 仍要求补回判断条件"
