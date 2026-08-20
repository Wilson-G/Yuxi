"""专家项目讲义编译、全书收口和候选发布测试。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.skills.repository import SkillRepository
from yuxi.expert import compilation_service, storage
from yuxi.expert.contracts import (
    BookReviewResult,
    HandoutDraftResult,
    HandoutReviewResult,
    HumanizedTextResult,
    NavigationResult,
    SkillBodyResult,
    TopicEvidencePack,
)
from yuxi.expert.model_tasks import ExpertContextLimitError
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.storage.postgres.models_business import Base

pytestmark = pytest.mark.unit

PROJECT_ID = "4" * 32
REAL_ASYNCIO_SLEEP = asyncio.sleep


@pytest_asyncio.fixture
async def compilation_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """创建讲义编译服务使用的 SQLite 与项目目录。"""

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'expert-compilation.db'}",
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
    monkeypatch.setattr(compilation_service.pg_manager, "get_async_session_context", session_context)
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


async def _seed_compilation_project(compilation_env) -> tuple[list[str], list[str]]:
    storage.create_project_workspace(PROJECT_ID)
    async with compilation_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.create_project(
            project_id=PROJECT_ID,
            name="正式讲义项目",
            description="依据正式讲义和精选证据回答",
            model_spec="provider:model",
            created_by="user-1",
            stage="handout_compilation",
            status="running",
        )
        project.method_version = 1
        project.learning_plan = "# 已确认的项目学习方案"
        project.prompt_pack = _prompt_pack()
        project.organization_version = 1
        project.organization_confirmed_at = project.updated_at
        project.editorial_plan = "# 已确认的编辑方案"

        materials = []
        for index in range(1, 4):
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

        topic_specs = [
            ("topic-a", "主题 A", "基础判断", 1, "material-1"),
            ("topic-b", "主题 B", "基础判断", 2, "material-2"),
            ("topic-c", "主题 C", "行动边界", 3, "material-3"),
        ]
        await repo.replace_topics(
            PROJECT_ID,
            1,
            [
                {
                    "topic_id": f"id-{topic_key}",
                    "topic_key": topic_key,
                    "title": title,
                    "section": section,
                    "reader_question": f"{title} 应该怎样判断？",
                    "purpose": f"帮助读者完成 {title} 的判断",
                    "boundaries": "资料不足时停止",
                    "position": position,
                    "material_ids": [material_id],
                    "evidence_requirements": [],
                    "is_sample": False,
                    "status": "pending",
                }
                for topic_key, title, section, position, material_id in topic_specs
            ],
        )
        await db.commit()
    return ["material-1", "material-2", "material-3"], ["topic-a", "topic-b", "topic-c"]


class CompilationModelTasks:
    """记录证据助手并发和总编讲义调用串行性。"""

    def __init__(self, *, failing_topic: str | None = None):
        self.failing_topic = failing_topic
        self.evidence_active = 0
        self.evidence_max_active = 0
        self.evidence_started: list[str] = []
        self.editor_active = 0
        self.editor_max_active = 0
        self.editor_started: list[str] = []
        self.writer_notes: dict[str, str] = {}
        self.reviewer_notes: dict[str, list[str]] = {}
        self.reviser_notes: dict[str, str] = {}
        self.book_calls = 0
        self.humanized_artifacts: list[str] = []

    @staticmethod
    def _topic_key(topic: str) -> str:
        return str(json.loads(topic)["topic_key"])

    @staticmethod
    def _topic_alias(topic_key: str) -> str:
        return {"topic-a": "N001", "topic-b": "N002", "topic-c": "N003"}[topic_key]

    async def prepare_topic_evidence(self, *, topic: str, **_kwargs) -> TopicEvidencePack:
        topic_key = self._topic_key(topic)
        self.evidence_started.append(topic_key)
        self.evidence_active += 1
        self.evidence_max_active = max(self.evidence_max_active, self.evidence_active)
        await REAL_ASYNCIO_SLEEP(0.1)
        self.evidence_active -= 1
        return TopicEvidencePack(
            topic_key=topic_key,
            evidence=[
                {
                    "note_alias": self._topic_alias(topic_key),
                    "start_line": 1,
                    "end_line": 2,
                    "supports": f"{topic_key} 的关键判断",
                }
            ],
        )

    async def write_handout(self, *, topic: str, notes: str, **_kwargs) -> HandoutDraftResult:
        topic_key = self._topic_key(topic)
        self.editor_started.append(topic_key)
        self.writer_notes[topic_key] = notes
        self.editor_active += 1
        self.editor_max_active = max(self.editor_max_active, self.editor_active)
        try:
            await REAL_ASYNCIO_SLEEP(0.02)
            if topic_key == self.failing_topic:
                raise RuntimeError(f"{topic_key} editor failed")
            return self._draft(topic_key, suffix="正式候选")
        finally:
            self.editor_active -= 1

    async def review_handout(
        self,
        *,
        handout: HandoutDraftResult,
        notes: str,
        **_kwargs,
    ) -> HandoutReviewResult:
        self.reviewer_notes.setdefault(handout.topic_key, []).append(notes)
        return HandoutReviewResult(decision="pass", summary="讲义通过", issues=[])

    async def revise_handout(
        self,
        *,
        handout: HandoutDraftResult,
        notes: str,
        **_kwargs,
    ) -> HandoutDraftResult:
        self.reviser_notes[handout.topic_key] = notes
        return self._draft(handout.topic_key, suffix="全书定点修订")

    async def review_book(self, **_kwargs) -> BookReviewResult:
        self.book_calls += 1
        return BookReviewResult(decision="pass", summary="全书通过", issues=[])

    async def write_navigation(self, **_kwargs) -> NavigationResult:
        return NavigationResult(navigation_markdown="# 读者导航")

    async def write_skill_body(self, **_kwargs) -> SkillBodyResult:
        return SkillBodyResult(skill_markdown="这是一位说明条件、边界和停止线的专家。")

    async def humanize_visible_artifact(
        self,
        *,
        artifact_name: str,
        content_markdown: str,
        **_kwargs,
    ) -> HumanizedTextResult:
        self.humanized_artifacts.append(artifact_name)
        return HumanizedTextResult(content_markdown=f"{content_markdown}\n\n这是一版更好读的表达。")

    def _draft(self, topic_key: str, *, suffix: str) -> HandoutDraftResult:
        return HandoutDraftResult(
            topic_key=topic_key,
            handout_markdown=f"# {topic_key} {suffix}\n判断方法与边界。",
            citations=[
                {
                    "note_alias": self._topic_alias(topic_key),
                    "start_line": 1,
                    "end_line": 2,
                    "supports": f"{topic_key} 的关键判断",
                }
            ],
            editorial_plan_markdown="# 已确认的编辑方案",
        )


class GroupedBookModelTasks(CompilationModelTasks):
    """模拟全书与导航超限，记录按既有 section/topic 形成的输入范围。"""

    def __init__(self):
        super().__init__()
        self.book_inputs: list[str] = []
        self.navigation_inputs: list[str] = []
        self.book_revision_calls = 0

    async def review_book(self, *, handouts: str, **_kwargs) -> BookReviewResult:
        self.book_inputs.append(handouts)
        if len(self.book_inputs) == 1:
            raise ExpertContextLimitError("whole book too large")
        if len(self.book_inputs) in {2, 3}:
            return BookReviewResult(decision="pass", summary="分区检查通过", issues=[])
        return BookReviewResult(decision="pass", summary="修订后全书通过", issues=[])

    async def merge_book_reviews(self, **_kwargs) -> BookReviewResult:
        return BookReviewResult(
            decision="revise",
            summary="主题 A 术语需要与全书统一",
            issues=[
                {
                    "topic_keys": ["topic-a"],
                    "problem": "术语口径不一致",
                    "revision_direction": "只修订主题 A 的术语口径",
                }
            ],
        )

    async def revise_handout(
        self,
        *,
        handout: HandoutDraftResult,
        notes: str,
        **_kwargs,
    ) -> HandoutDraftResult:
        self.book_revision_calls += 1
        self.reviser_notes[handout.topic_key] = notes
        return self._draft(handout.topic_key, suffix="全书定点修订")

    async def write_navigation(self, *, handouts: str, **_kwargs) -> NavigationResult:
        self.navigation_inputs.append(handouts)
        call_number = len(self.navigation_inputs)
        if call_number in {1, 2}:
            raise ExpertContextLimitError("navigation input too large")
        return NavigationResult(navigation_markdown=f"# 范围导航 {call_number}")

    async def merge_navigation(self, **_kwargs) -> NavigationResult:
        return NavigationResult(navigation_markdown="# 项目级统一导航")


@pytest.mark.asyncio
async def test_evidence_preparation_is_concurrent_but_handout_editor_is_serial_and_failure_blocks(
    compilation_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, topic_keys = await _seed_compilation_project(compilation_env)
    monkeypatch.setattr(compilation_service, "EVIDENCE_PREPARATION_CONCURRENCY", 3)
    tasks = CompilationModelTasks(failing_topic="topic-b")

    result = await compilation_service.run_handout_compilation(PROJECT_ID, 1, model_tasks=tasks)

    async with compilation_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(PROJECT_ID)
        topics = await repo.list_topics(PROJECT_ID, 1)
    assert result == {"project_id": PROJECT_ID, "status": "needs_attention"}
    assert tasks.evidence_max_active == 3
    assert set(tasks.evidence_started) == set(topic_keys)
    assert tasks.editor_max_active == 1
    assert tasks.editor_started == topic_keys
    assert {topic.topic_key: topic.status for topic in topics} == {
        "topic-a": "passed",
        "topic-b": "needs_attention",
        "topic-c": "passed",
    }
    assert project is not None
    assert project.stage == "handout_compilation"
    assert project.status == "needs_attention"
    assert project.skill_dir is None
    assert tasks.book_calls == 0


@pytest.mark.asyncio
async def test_book_revision_and_context_grouping_finish_candidate_without_activation(
    compilation_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, topic_keys = await _seed_compilation_project(compilation_env)
    monkeypatch.setattr(compilation_service, "EVIDENCE_PREPARATION_CONCURRENCY", 3)
    tasks = GroupedBookModelTasks()

    result = await compilation_service.run_handout_compilation(PROJECT_ID, 1, model_tasks=tasks)

    async with compilation_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(PROJECT_ID)
        topics = await repo.list_topics(PROJECT_ID, 1)
        installed = await SkillRepository(db).get_by_slug(project.skill_slug if project else "missing")
    assert project is not None
    assert result == {"project_id": PROJECT_ID, "status": "ready", "skill_slug": project.skill_slug}
    assert project.stage == "release"
    assert project.status == "ready"
    assert project.skill_dir == "release/v1"
    assert (storage.get_project_root(PROJECT_ID) / project.skill_dir / "SKILL.md").is_file()
    assert installed is None
    assert tasks.editor_max_active == 1
    assert tasks.editor_started == topic_keys
    assert tasks.book_revision_calls == 1
    assert tasks.reviser_notes["topic-a"] == tasks.writer_notes["topic-a"]
    assert tasks.reviewer_notes["topic-a"] == [
        tasks.writer_notes["topic-a"],
        tasks.writer_notes["topic-a"],
    ]
    assert "L1: SOURCE-1-FIRST\nL2: SOURCE-1-MIDDLE" in tasks.writer_notes["topic-a"]
    assert "SOURCE-1-LAST" not in tasks.writer_notes["topic-a"]
    assert {topic.topic_key: topic.revision_count for topic in topics} == {
        "topic-a": 1,
        "topic-b": 0,
        "topic-c": 0,
    }
    assert all(topic.status == "passed" for topic in topics)
    assert "全书定点修订" in next(topic.handout for topic in topics if topic.topic_key == "topic-a")
    assert tasks.humanized_artifacts == [
        "正式讲义《主题 A》",
        "正式讲义《主题 B》",
        "正式讲义《主题 C》",
        "读者导航",
        "专家 Skill 使用入口",
    ]
    assert "更好读的表达" in project.navigation
    assert "更好读的表达" in project.skill_body

    assert len(tasks.book_inputs) == 4
    assert all(topic_key in tasks.book_inputs[0] for topic_key in topic_keys)
    assert "topic-a" in tasks.book_inputs[1] and "topic-b" in tasks.book_inputs[1]
    assert "topic-c" not in tasks.book_inputs[1]
    assert "topic-c" in tasks.book_inputs[2]
    assert all(topic_key in tasks.book_inputs[3] for topic_key in topic_keys)

    assert len(tasks.navigation_inputs) == 5
    assert all(topic_key in tasks.navigation_inputs[0] for topic_key in topic_keys)
    assert "topic-a" in tasks.navigation_inputs[1] and "topic-b" in tasks.navigation_inputs[1]
    assert "topic-c" not in tasks.navigation_inputs[1]
    assert "topic-a" in tasks.navigation_inputs[2] and "topic-b" not in tasks.navigation_inputs[2]
    assert "topic-b" in tasks.navigation_inputs[3] and "topic-a" not in tasks.navigation_inputs[3]
    assert "topic-c" in tasks.navigation_inputs[4]


@pytest.mark.asyncio
async def test_confirmed_sample_handout_is_not_humanized_again(compilation_env) -> None:
    """用户确认过的可读小样直接进入发布，成书阶段只改写其他讲义。"""

    await _seed_compilation_project(compilation_env)
    confirmed_handout = "# 用户确认的小样\n\n按确认版本直接发布。"
    async with compilation_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(PROJECT_ID)
        sample = await repo.lock_topic("id-topic-a")
        assert project is not None and sample is not None
        sample.is_sample = True
        sample.status = "passed"
        sample.handout = confirmed_handout
        sample.evidence_pack = {
            "topic_key": "topic-a",
            "evidence": [
                {
                    "note_alias": "N001",
                    "start_line": 1,
                    "end_line": 2,
                    "supports": "topic-a 的关键判断",
                }
            ],
            "conflicts": [],
            "omissions": [],
        }
        sample.handout_contract = {
            "topic_key": "topic-a",
            "handout_markdown": confirmed_handout,
            "citations": sample.evidence_pack["evidence"],
            "editorial_plan_markdown": "# 已确认的编辑方案",
        }
        sample.review = {"decision": "pass", "summary": "小样已通过", "issues": []}
        project.organization_samples = [
            {
                "topic_id": sample.topic_id,
                "topic_key": sample.topic_key,
                "title": sample.title,
                "status": "passed",
                "handout": confirmed_handout,
                "review": sample.review,
                "reason": "",
            }
        ]
        await db.commit()

    tasks = CompilationModelTasks()
    result = await compilation_service.run_handout_compilation(PROJECT_ID, 1, model_tasks=tasks)

    async with compilation_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(PROJECT_ID)
        topics = await repo.list_topics(PROJECT_ID, 1)
    assert result["status"] == "ready"
    assert project is not None
    assert next(topic.handout for topic in topics if topic.topic_key == "topic-a") == confirmed_handout
    assert tasks.humanized_artifacts == [
        "正式讲义《主题 B》",
        "正式讲义《主题 C》",
        "读者导航",
        "专家 Skill 使用入口",
    ]
