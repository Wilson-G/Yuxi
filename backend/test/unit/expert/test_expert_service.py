"""专家项目第 0、1 阶段服务行为测试。"""

from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.expert import service, storage, worker
from yuxi.expert.contracts import (
    MaterialNoteResult,
    MaterialReviewResult,
    ProjectMethodResult,
    ProjectOrientationResult,
    ProjectPromptPack,
)
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.storage.postgres.models_business import Base

pytestmark = pytest.mark.unit

REAL_ASYNCIO_SLEEP = asyncio.sleep


@pytest_asyncio.fixture
async def expert_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """创建可供多个服务会话共享的 SQLite 项目环境。"""

    database_path = tmp_path / "expert-service.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
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
    monkeypatch.setattr(service.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(
        service.model_cache,
        "get_model_info",
        lambda model_spec: (
            SimpleNamespace(model_type="chat", context_length=128_000) if model_spec == "provider:model" else None
        ),
    )

    try:
        yield SimpleNamespace(factory=factory, save_dir=tmp_path)
    finally:
        await engine.dispose()


def _prompt_pack() -> ProjectPromptPack:
    return ProjectPromptPack(
        note_author="note author prompt",
        note_reviewer="note reviewer prompt",
        note_reviser="note reviser prompt",
        topic_researcher="topic researcher prompt",
        topic_editor="topic editor prompt",
        handout_editor="handout editor prompt",
        handout_reviewer="handout reviewer prompt",
        handout_reviser="handout reviser prompt",
        book_editor="book editor prompt",
        navigation_author="navigation author prompt",
        skill_author="skill author prompt",
    )


async def _create_project(
    expert_env,
    *,
    count: int,
    stage: str = service.ORIENTATION_STAGE,
    status: str = "preparing",
):
    uploads = []
    for index in range(1, count + 1):
        path = expert_env.save_dir / f"upload-{index:02d}.md"
        path.write_text(f"RAW-{index:02d}", encoding="utf-8")
        uploads.append((f"{index:02d}-material.md", path))

    async with expert_env.factory() as db:
        project = await service.create_expert_project(
            db,
            name="判断方法项目",
            description="学习材料中的判断条件与边界",
            model_spec="provider:model",
            created_by="user-1",
            uploads=uploads,
        )
        project.stage = stage
        project.status = status
        await db.commit()
        materials = await ExpertRepository(db).list_project_materials(project.project_id)
        return project.project_id, [material.material_id for material in materials]


async def _make_materials_ready(expert_env, project_id: str) -> list[str]:
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        materials = await repo.list_project_materials(project_id)
        project_root = storage.get_expert_projects_root() / project_id
        for index, material in enumerate(materials, start=1):
            text_path = f"text/{material.material_id}.md"
            (project_root / text_path).write_text(
                f"FIRST-{index}\nMIDDLE-{index}\nLAST-{index}",
                encoding="utf-8",
            )
            await repo.update_material(
                material.material_id,
                status="ready",
                text_path=text_path,
                line_count=3,
                token_count=20,
                error_message=None,
            )
        await repo.update_project(project_id, stage=service.ORIENTATION_STAGE, status="ready")
        await db.commit()
        return [material.material_id for material in materials]


class OrientationModelTasks:
    """记录第 0 阶段调用，并让指定小样的首次 Review 可重试失败。"""

    def __init__(self, *, fail_review_title: str | None = None):
        self.fail_review_title = fail_review_title
        self.failed_review = False
        self.call_counts: Counter[str] = Counter()

    async def orient_project(self, **_kwargs) -> ProjectOrientationResult:
        self.call_counts["orientation"] += 1
        return ProjectOrientationResult(
            template_name="mentor-oral-teaching.md",
            material_type="老师口述案例",
            expert_identity="能说明判断条件和停止线的老师",
            report_markdown="# 项目定向报告",
            source_risks=["转写不确定"],
        )

    async def design_project_method(self, **_kwargs) -> ProjectMethodResult:
        self.call_counts["method"] += 1
        return ProjectMethodResult(
            learning_plan_markdown="# 项目学习方案",
            compilation_brief_markdown="# 项目专属编译底稿",
            prompt_pack=_prompt_pack(),
            method_sample_aliases=["M001", "M002"],
        )

    async def write_material_note(self, *, material_alias: str, title: str, **_kwargs) -> MaterialNoteResult:
        self.call_counts[f"note:{title}"] += 1
        return MaterialNoteResult(
            decision="learned",
            note_markdown="# 学艺笔记",
            routing_labels=["判断条件"],
            candidate_questions=["什么条件会让判断反转？"],
            evidence=[
                {
                    "material_alias": material_alias,
                    "start_line": 1,
                    "end_line": 3,
                    "supports": "材料展示了判断条件和停止线",
                }
            ],
        )

    async def review_material_note(self, *, title: str, **_kwargs) -> MaterialReviewResult:
        self.call_counts[f"review:{title}"] += 1
        if title == self.fail_review_title and not self.failed_review:
            self.failed_review = True
            raise RuntimeError("sample review failed")
        return MaterialReviewResult(decision="pass", summary="笔记忠实保留了条件", issues=[])

    async def revise_material_note(self, **_kwargs) -> MaterialNoteResult:
        raise AssertionError("通过的小样不应进入修订")


class CalibrationModelTasks(OrientationModelTasks):
    """模拟一条具体 Review 问题触发的一次方法收敛。"""

    def __init__(self):
        super().__init__()
        self.calibration_inputs: list[dict] = []
        self.assessment_inputs: list[dict] = []
        self.review_rounds: Counter[str] = Counter()

    async def review_material_note(self, *, title: str, **_kwargs) -> MaterialReviewResult:
        self.review_rounds[title] += 1
        if title == "01-material" and self.review_rounds[title] == 1:
            return MaterialReviewResult(
                decision="revise",
                summary="缺少判断条件的保留职责",
                issues=[
                    {
                        "problem": "遗漏改变处理的条件",
                        "affected_part": "判断链",
                        "source_lines": ["L3"],
                        "revision_direction": "保留原文明确条件",
                    }
                ],
            )
        return MaterialReviewResult(decision="pass", summary="通过", issues=[])

    async def revise_material_note(self, **kwargs) -> MaterialNoteResult:
        return await self.write_material_note(**kwargs)

    async def calibrate_project_method(self, **kwargs):
        self.calibration_inputs.append(kwargs)
        return SimpleNamespace(
            learning_plan_markdown="# 校准后的项目学习方案",
            compilation_brief_markdown="# 校准后的项目专属编译底稿",
            prompt_pack=_prompt_pack(),
        )

    async def assess_method_calibration(self, **kwargs):
        self.assessment_inputs.append(kwargs)
        return SimpleNamespace(decision="acceptable_residual", reason="局部问题可由逐篇 Repair 处理")


class AttentionCalibrationTasks(CalibrationModelTasks):
    """模拟 V2 后仍存在方法级问题的校准评估。"""

    async def assess_method_calibration(self, **kwargs):
        self.assessment_inputs.append(kwargs)
        return SimpleNamespace(decision="needs_attention", reason="V2 仍会系统性遗漏判断条件")


class RepairableResidualTasks(CalibrationModelTasks):
    """模拟 V2 留下可由后台继续修订关闭的局部 Review 问题。"""

    def __init__(self):
        super().__init__()
        self.v2_repair_requested = False

    async def review_material_note(self, *, title: str, **_kwargs) -> MaterialReviewResult:
        if self.calibration_inputs and title == "01-material" and not self.v2_repair_requested:
            self.v2_repair_requested = True
            return MaterialReviewResult(
                decision="revise",
                summary="当前单篇仍需补回一个条件",
                issues=[
                    {
                        "problem": "条件位置需调整",
                        "affected_part": "判断链",
                        "source_lines": ["L3"],
                        "revision_direction": "按当前原文调整",
                    }
                ],
            )
        return await super().review_material_note(title=title)


class ParallelOrientationSampleTasks(OrientationModelTasks):
    """让两个小样在笔记调用处相互等待，验证它们可以并发启动。"""

    def __init__(self):
        super().__init__()
        self.started_titles: set[str] = set()
        self.both_started = asyncio.Event()
        self.release = asyncio.Event()

    async def write_material_note(self, *, title: str, **kwargs) -> MaterialNoteResult:
        self.started_titles.add(title)
        if len(self.started_titles) == 2:
            self.both_started.set()
        await self.release.wait()
        return await super().write_material_note(title=title, **kwargs)


class ConcurrentLearningTasks:
    """记录材料任务的占槽、启动顺序和模型调用次数。"""

    def __init__(self, titles: list[str], *, failing_title: str):
        self.gates = {title: asyncio.Event() for title in titles}
        self.failing_title = failing_title
        self.start_order: list[str] = []
        self.call_counts: Counter[str] = Counter()
        self.active_titles: set[str] = set()
        self.max_active = 0

    async def write_material_note(
        self,
        *,
        material_alias: str,
        title: str,
        **_kwargs,
    ) -> MaterialNoteResult:
        self.start_order.append(title)
        self.call_counts[title] += 1
        self.active_titles.add(title)
        self.max_active = max(self.max_active, len(self.active_titles))
        await self.gates[title].wait()
        if title == self.failing_title:
            self.active_titles.remove(title)
            raise RuntimeError(f"{title} model failed")
        return MaterialNoteResult(
            decision="learned",
            note_markdown=f"# {title} 学艺笔记",
            routing_labels=["判断条件"],
            evidence=[
                {
                    "material_alias": material_alias,
                    "start_line": 1,
                    "end_line": 3,
                    "supports": "材料完整支持当前判断",
                }
            ],
        )

    async def review_material_note(self, *, title: str, **_kwargs) -> MaterialReviewResult:
        self.active_titles.remove(title)
        return MaterialReviewResult(decision="pass", summary="通过", issues=[])

    async def revise_material_note(self, **_kwargs) -> MaterialNoteResult:
        raise AssertionError("通过的笔记不应进入修订")


def test_learned_note_requires_runtime_fields_and_valid_evidence_lines() -> None:
    """已学习笔记必须能被后续分组和证据流程直接消费。"""

    valid = MaterialNoteResult(
        decision="learned",
        note_markdown="# 判断链",
        routing_labels=["条件判断"],
        evidence=[
            {
                "material_alias": "M001",
                "start_line": 1,
                "end_line": 3,
                "supports": "原文给出了判断条件",
            }
        ],
    )
    service._validate_note(valid, alias="M001", line_count=3)

    invalid_notes = [
        (valid.model_copy(update={"note_markdown": "  "}), "缺少笔记正文"),
        (valid.model_copy(update={"routing_labels": []}), "缺少首轮分组 Labels"),
        (valid.model_copy(update={"evidence": []}), "缺少可定位的原文依据"),
        (
            MaterialNoteResult.model_validate(
                {
                    **valid.model_dump(),
                    "evidence": [
                        {
                            "material_alias": "M001",
                            "start_line": 2,
                            "end_line": 4,
                            "supports": "超出正文范围",
                        }
                    ],
                }
            ),
            "证据行号超出当前材料范围",
        ),
    ]
    for note, message in invalid_notes:
        with pytest.raises(ValueError, match=message):
            service._validate_note(note, alias="M001", line_count=3)


def test_method_samples_allow_repairable_review_issues_but_block_structural_attention() -> None:
    """可修 Review 交给后续 Repair，结构性需要处理仍阻塞交接。"""

    service._validate_method_samples_ready(
        [
            {
                "status": "review_revise",
                "final_review": {
                    "decision": "revise",
                    "issues": [{"problem": "补回条件", "revision_direction": "按原文修订"}],
                },
            }
        ]
    )
    with pytest.raises(ValueError, match="无法自动修复"):
        service._validate_method_samples_ready(
            [{"status": "needs_attention", "final_review": {"decision": "needs_attention"}}]
        )


async def _wait_until(predicate, *, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("等待并发材料任务状态超时")
        await REAL_ASYNCIO_SLEEP(0.01)


@pytest.mark.asyncio
async def test_create_project_commits_independent_materials_before_queue_submission(
    expert_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = expert_env.save_dir / "first.md"
    second = expert_env.save_dir / "second.md"
    first.write_text("FIRST MATERIAL", encoding="utf-8")
    second.write_text("SECOND MATERIAL", encoding="utf-8")

    async with expert_env.factory() as db:
        project = await service.create_expert_project(
            db,
            name="提交边界项目",
            description="",
            model_spec="provider:model",
            created_by="user-1",
            uploads=[("first.md", first), ("nested/second.md", second)],
        )
        project_id = project.project_id
        await db.rollback()

    observed_at_enqueue: list[tuple[str, list[str]]] = []

    class FakePool:
        async def enqueue_job(self, _function: str, project_id: str, **_kwargs) -> None:
            async with expert_env.factory() as observer:
                stored_project = await ExpertRepository(observer).get_project(project_id)
                materials = await ExpertRepository(observer).list_project_materials(project_id)
                assert stored_project is not None
                observed_at_enqueue.append((stored_project.status, [item.source_path for item in materials]))

    async def fake_get_arq_pool():
        return FakePool()

    monkeypatch.setattr(worker, "get_arq_pool", fake_get_arq_pool)
    await worker.enqueue_project_preparation(project_id)

    assert observed_at_enqueue == [("preparing", ["first.md", "nested/second.md"])]
    project_root = storage.get_expert_projects_root() / project_id
    async with expert_env.factory() as db:
        materials = await ExpertRepository(db).list_project_materials(project_id)
    assert len(materials) == 2
    assert len({material.material_id for material in materials}) == 2
    assert [(project_root / material.raw_path).read_text(encoding="utf-8") for material in materials] == [
        "FIRST MATERIAL",
        "SECOND MATERIAL",
    ]


@pytest.mark.asyncio
async def test_create_project_requires_configured_model_context_length(
    expert_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = expert_env.save_dir / "material.md"
    source.write_text("完整材料", encoding="utf-8")
    monkeypatch.setattr(
        service.model_cache,
        "get_model_info",
        lambda _model_spec: SimpleNamespace(model_type="chat", context_length=None),
    )

    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="缺少上下文长度配置"):
            await service.create_expert_project(
                db,
                name="缺少上下文配置",
                description="",
                model_spec="provider:model",
                created_by="user-1",
                uploads=[("material.md", source)],
            )


@pytest.mark.asyncio
async def test_preparation_failure_marks_only_its_material_needs_attention(
    expert_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _ = await _create_project(expert_env, count=3)

    async def fake_parse_material(current_project_id, source):
        assert current_project_id == project_id
        if source.title == "02-material":
            raise ValueError("document conversion failed")
        return storage.ParsedMaterial(
            text_path=f"text/{source.material_id}.md",
            line_count=3,
            token_count=20,
        )

    monkeypatch.setattr(service, "parse_material", fake_parse_material)

    result = await service.prepare_expert_project(project_id)

    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        materials = await repo.list_project_materials(project_id)
    assert result["status"] == "needs_attention"
    assert project is not None and project.status == "needs_attention"
    assert {material.title: material.status for material in materials} == {
        "01-material": "ready",
        "02-material": "needs_attention",
        "03-material": "ready",
    }
    failed = next(material for material in materials if material.title == "02-material")
    assert failed.error_message == "document conversion failed"


@pytest.mark.asyncio
async def test_orientation_retry_reuses_completed_work_and_preserves_samples(expert_env) -> None:
    project_id, _ = await _create_project(expert_env, count=3)
    material_ids = await _make_materials_ready(expert_env, project_id)

    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="请选择 1～20 份代表性材料"):
            await service.start_project_orientation(
                db,
                project_id=project_id,
                created_by="user-1",
                representative_material_ids=[],
            )
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids[:2],
        )
        with pytest.raises(ValueError, match="没有等待确认"):
            await service.confirm_project_method(db, project_id=project_id, created_by="user-1")
        await db.rollback()

    model_tasks = OrientationModelTasks(fail_review_title="02-material")
    with pytest.raises(RuntimeError, match="sample review failed"):
        await service.run_project_orientation(
            project_id,
            method_version,
            model_tasks=model_tasks,
        )

    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        assert project.status == "running"
        assert project.orientation_report == "# 项目定向报告"
        assert project.learning_plan == "# 项目学习方案"
        assert project.compilation_brief == "# 项目专属编译底稿"
        assert project.prompt_pack == _prompt_pack().model_dump()
        assert [sample["material_id"] for sample in project.method_samples] == material_ids[:1]
    assert model_tasks.call_counts == {
        "orientation": 1,
        "method": 1,
        "note:01-material": 1,
        "review:01-material": 1,
        "note:02-material": 1,
        "review:02-material": 1,
    }

    result = await service.run_project_orientation(
        project_id,
        method_version,
        model_tasks=model_tasks,
    )
    assert model_tasks.call_counts == {
        "orientation": 1,
        "method": 1,
        "note:01-material": 1,
        "review:01-material": 1,
        "note:02-material": 2,
        "review:02-material": 2,
    }

    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        materials = await repo.list_project_materials(project_id)
        assert project is not None
        assert project.status == "awaiting_confirmation"
        assert project.orientation_report == "# 项目定向报告"
        assert project.learning_plan == "# 项目学习方案"
        assert project.compilation_brief == "# 项目专属编译底稿"
        assert project.prompt_pack == _prompt_pack().model_dump()
        assert len(project.method_samples) == 2
        assert all(sample["status"] == "passed" for sample in project.method_samples)
        assert all(sample["final_review"]["decision"] == "pass" for sample in project.method_samples)
        assert [material.material_id for material in materials if material.is_representative] == material_ids[:2]

    assert result == {
        "project_id": project_id,
        "method_version": method_version,
        "status": "awaiting_confirmation",
    }


@pytest.mark.asyncio
async def test_orientation_accepts_twenty_representative_materials(expert_env) -> None:
    project_id, _ = await _create_project(expert_env, count=21)
    material_ids = await _make_materials_ready(expert_env, project_id)

    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="请选择 1～20 份代表性材料"):
            await service.start_project_orientation(
                db,
                project_id=project_id,
                created_by="user-1",
                representative_material_ids=material_ids,
            )

        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids[:20],
        )
        project = await ExpertRepository(db).get_project(project_id)

    assert method_version == 1
    assert project is not None
    assert project.representative_material_ids == material_ids[:20]


@pytest.mark.asyncio
async def test_orientation_runs_independent_samples_concurrently(expert_env) -> None:
    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)

    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )

    tasks = ParallelOrientationSampleTasks()
    runner = asyncio.create_task(
        service.run_project_orientation(project_id, method_version, model_tasks=tasks)
    )
    await asyncio.wait_for(tasks.both_started.wait(), timeout=1)
    assert tasks.started_titles == {"01-material", "02-material"}

    tasks.release.set()
    result = await runner

    assert result["status"] == "awaiting_confirmation"
    assert tasks.call_counts == {
        "orientation": 1,
        "method": 1,
        "note:01-material": 1,
        "review:01-material": 1,
        "note:02-material": 1,
        "review:02-material": 1,
    }


@pytest.mark.asyncio
async def test_material_learning_refills_slots_and_attention_blocks_until_explicit_exclusion(
    expert_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _ = await _create_project(expert_env, count=5)
    material_ids = await _make_materials_ready(expert_env, project_id)
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        await repo.update_project(
            project_id,
            stage=service.MATERIAL_LEARNING_STAGE,
            status="running",
            method_version=1,
            learning_plan="# 已确认的学习方案",
            prompt_pack=_prompt_pack().model_dump(),
        )
        for material_id in material_ids:
            await repo.update_material(material_id, status="pending")
        await db.commit()
        materials = await repo.list_project_materials(project_id)
        titles = [material.title for material in materials]

    tasks = ConcurrentLearningTasks(titles, failing_title=titles[-1])
    monkeypatch.setattr(service, "MATERIAL_LEARNING_CONCURRENCY", 2)

    async def fast_retry_sleep(delay: float) -> None:
        await REAL_ASYNCIO_SLEEP(0 if delay == 2 else delay)

    monkeypatch.setattr(service.asyncio, "sleep", fast_retry_sleep)

    stage_task = asyncio.create_task(service.run_material_learning_stage(project_id, 1, model_tasks=tasks))
    await _wait_until(lambda: len(tasks.start_order) == 2)
    assert set(tasks.start_order) == set(titles[:2])
    assert tasks.active_titles == set(titles[:2])
    assert tasks.max_active == 2

    tasks.gates[titles[0]].set()
    await _wait_until(lambda: len(tasks.start_order) == 3)
    assert set(tasks.start_order[:3]) == set(titles[:3])
    assert tasks.active_titles == {titles[1], titles[2]}

    for gate in tasks.gates.values():
        gate.set()
    result = await stage_task

    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        materials = await repo.list_project_materials(project_id)
    statuses = {material.title: material.status for material in materials}
    assert result["status"] == "needs_attention"
    assert project is not None
    assert project.stage == service.MATERIAL_LEARNING_STAGE
    assert project.status == "needs_attention"
    assert statuses == {**{title: "passed" for title in titles[:-1]}, titles[-1]: "needs_attention"}
    assert tasks.max_active == 2
    assert set(tasks.start_order) == set(titles)
    assert tasks.call_counts == Counter({**{title: 1 for title in titles[:-1]}, titles[-1]: 2})

    calls_before_recovery = tasks.call_counts.copy()
    second_result = await service.run_material_learning_stage(project_id, 1, model_tasks=tasks)
    assert second_result["status"] == "needs_attention"
    assert tasks.call_counts == calls_before_recovery

    failed_material = next(material for material in materials if material.title == titles[-1])
    async with expert_env.factory() as db:
        should_start_organization = await service.exclude_material(
            db,
            project_id=project_id,
            material_id=failed_material.material_id,
            created_by="user-1",
            reason="模型两次失败，用户明确排除后继续",
        )
    assert should_start_organization is True

    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        materials = await repo.list_project_materials(project_id)
    assert project is not None
    assert project.stage == "organization"
    assert project.status == "preparing"
    assert {material.title: material.status for material in materials} == {
        **{title: "passed" for title in titles[:-1]},
        titles[-1]: "excluded",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("note_decision", ["no_value", "needs_attention"])
async def test_nontransferable_note_is_not_promoted_to_passed(expert_env, note_decision: str) -> None:
    """Reviewer 认可材料不具备迁移价值时，项目自动排除该材料。"""

    class NontransferableTasks:
        async def write_material_note(self, **_kwargs) -> MaterialNoteResult:
            return MaterialNoteResult(
                decision=note_decision,
                reason="材料没有可迁移的判断方法",
            )

        async def review_material_note(self, **_kwargs) -> MaterialReviewResult:
            return MaterialReviewResult(decision="pass", summary="不入选结论忠实于原文")

        async def revise_material_note(self, **_kwargs) -> MaterialNoteResult:
            raise AssertionError("不入选材料不应进入修订")

    project_id, material_ids = await _create_project(expert_env, count=1)
    await _make_materials_ready(expert_env, project_id)
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        await repo.update_project(
            project_id,
            stage=service.MATERIAL_LEARNING_STAGE,
            status="running",
            method_version=1,
            learning_plan="# 学习方案",
            prompt_pack=_prompt_pack().model_dump(),
        )
        await repo.update_material(material_ids[0], status="pending")
        await db.commit()

    result = await service.run_material_learning_stage(project_id, 1, model_tasks=NontransferableTasks())

    async with expert_env.factory() as db:
        material = await ExpertRepository(db).get_material(material_ids[0])

    assert result["status"] == "preparing"
    assert material is not None
    assert material.status == "no_value"
    assert material.error_message == "材料没有可迁移的判断方法"


@pytest.mark.asyncio
async def test_material_learning_revises_until_reviewer_passes(expert_env) -> None:
    """同一材料的具体 Review 意见由后台连续修订，不交给用户逐项处理。"""

    class RepeatedRevisionTasks:
        def __init__(self):
            self.review_count = 0
            self.revised_notes: list[str] = []

        async def write_material_note(self, *, material_alias: str, **_kwargs) -> MaterialNoteResult:
            return MaterialNoteResult(
                decision="learned",
                note_markdown="# 学艺笔记",
                routing_labels=["判断条件"],
                evidence=[
                    {
                        "material_alias": material_alias,
                        "start_line": 1,
                        "end_line": 3,
                        "supports": "材料展示了当前判断条件",
                    }
                ],
            )

        async def review_material_note(self, **_kwargs) -> MaterialReviewResult:
            self.review_count += 1
            if self.review_count <= 2:
                return MaterialReviewResult(
                    decision="revise",
                    summary="还需补足一个判断条件",
                    issues=[
                        {
                            "problem": "遗漏关键条件",
                            "affected_part": "判断链",
                            "source_lines": ["L2"],
                            "revision_direction": "按原文补回条件",
                        }
                    ],
                )
            return MaterialReviewResult(decision="pass", summary="通过", issues=[])

        async def revise_material_note(self, *, note: MaterialNoteResult, **kwargs) -> MaterialNoteResult:
            self.revised_notes.append(note.note_markdown)
            revised = await self.write_material_note(**kwargs)
            return revised.model_copy(update={"note_markdown": f"{note.note_markdown} 已修订"})

    project_id, material_ids = await _create_project(expert_env, count=1)
    await _make_materials_ready(expert_env, project_id)
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        await repo.update_project(
            project_id,
            stage=service.MATERIAL_LEARNING_STAGE,
            status="running",
            method_version=1,
            learning_plan="# 学习方案",
            prompt_pack=_prompt_pack().model_dump(),
        )
        await repo.update_material(material_ids[0], status="pending")
        await db.commit()

    tasks = RepeatedRevisionTasks()
    result = await service.run_material_learning_stage(
        project_id,
        1,
        model_tasks=tasks,
    )

    async with expert_env.factory() as db:
        material = await ExpertRepository(db).get_material(material_ids[0])

    assert result["status"] == "preparing"
    assert material is not None
    assert material.status == "passed"
    assert material.revision_count == 2
    assert material.review["decision"] == "pass"
    assert tasks.revised_notes == ["# 学艺笔记", "# 学艺笔记 已修订"]


@pytest.mark.asyncio
async def test_material_learning_requires_attention_after_two_unsuccessful_revisions(expert_env) -> None:
    """第二次复核仍要求修订时，保留结果并交给人工处理。"""

    class NeverPassingReviewTasks:
        async def write_material_note(self, *, material_alias: str, **_kwargs) -> MaterialNoteResult:
            return MaterialNoteResult(
                decision="learned",
                note_markdown="# 学艺笔记",
                routing_labels=["判断条件"],
                evidence=[
                    {
                        "material_alias": material_alias,
                        "start_line": 1,
                        "end_line": 3,
                        "supports": "材料展示了当前判断条件",
                    }
                ],
            )

        async def review_material_note(self, **_kwargs) -> MaterialReviewResult:
            return MaterialReviewResult(
                decision="revise",
                summary="仍需收窄一处判断",
                issues=[
                    {
                        "problem": "仍有未经支持的判断",
                        "affected_part": "判断链",
                        "source_lines": ["L2"],
                        "revision_direction": "按原文收窄",
                    }
                ],
            )

        async def revise_material_note(self, **kwargs) -> MaterialNoteResult:
            return await self.write_material_note(**kwargs)

    project_id, material_ids = await _create_project(expert_env, count=1)
    await _make_materials_ready(expert_env, project_id)
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        await repo.update_project(
            project_id,
            stage=service.MATERIAL_LEARNING_STAGE,
            status="running",
            method_version=1,
            learning_plan="# 学习方案",
            prompt_pack=_prompt_pack().model_dump(),
        )
        await repo.update_material(material_ids[0], status="pending")
        await db.commit()

    result = await service.run_material_learning_stage(
        project_id,
        1,
        model_tasks=NeverPassingReviewTasks(),
    )

    async with expert_env.factory() as db:
        material = await ExpertRepository(db).get_material(material_ids[0])

    assert result["status"] == "needs_attention"
    assert material is not None
    assert material.status == "needs_attention"
    assert material.revision_count == 2
    assert material.review["decision"] == "revise"
    assert material.error_message == "已完成 2 次自动修订，Review 仍要求修订：仍需收窄一处判断"


@pytest.mark.asyncio
async def test_recovered_material_uses_only_its_remaining_revision(expert_env) -> None:
    """恢复中的材料沿用已保存的修订次数，不从初稿重新计算。"""

    class RemainingRevisionTasks:
        def __init__(self):
            self.revision_calls = 0

        async def write_material_note(self, **_kwargs) -> MaterialNoteResult:
            raise AssertionError("恢复中的材料不应重新生成初稿")

        async def review_material_note(self, **_kwargs) -> MaterialReviewResult:
            return MaterialReviewResult(
                decision="revise",
                summary="仍需收窄一处判断",
                issues=[
                    {
                        "problem": "仍有未经支持的判断",
                        "affected_part": "判断链",
                        "source_lines": ["L2"],
                        "revision_direction": "按原文收窄",
                    }
                ],
            )

        async def revise_material_note(self, *, note: MaterialNoteResult, **_kwargs) -> MaterialNoteResult:
            self.revision_calls += 1
            return note.model_copy(update={"note_markdown": "# 第二次修订"})

    project_id, material_ids = await _create_project(expert_env, count=1)
    await _make_materials_ready(expert_env, project_id)
    existing_note = MaterialNoteResult(
        decision="learned",
        note_markdown="# 第一次修订",
        routing_labels=["判断条件"],
        evidence=[
            {
                "material_alias": "CURRENT_MATERIAL",
                "start_line": 1,
                "end_line": 3,
                "supports": "材料展示了当前判断条件",
            }
        ],
    )
    existing_review = MaterialReviewResult(
        decision="revise",
        summary="仍需收窄一处判断",
        issues=[
            {
                "problem": "仍有未经支持的判断",
                "affected_part": "判断链",
                "source_lines": ["L2"],
                "revision_direction": "按原文收窄",
            }
        ],
    )
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        await repo.update_project(
            project_id,
            stage=service.MATERIAL_LEARNING_STAGE,
            status="running",
            method_version=1,
            learning_plan="# 学习方案",
            prompt_pack=_prompt_pack().model_dump(),
        )
        await repo.update_material(
            material_ids[0],
            status="learning",
            note=existing_note.note_markdown,
            note_contract=existing_note.model_dump(),
            review=existing_review.model_dump(),
            revision_count=1,
        )
        await db.commit()

    tasks = RemainingRevisionTasks()
    result = await service.run_material_learning_stage(project_id, 1, model_tasks=tasks)

    async with expert_env.factory() as db:
        material = await ExpertRepository(db).get_material(material_ids[0])

    assert result["status"] == "needs_attention"
    assert tasks.revision_calls == 1
    assert material is not None
    assert material.status == "needs_attention"
    assert material.revision_count == 2
    assert material.note == "# 第二次修订"


@pytest.mark.asyncio
async def test_cannot_exclude_the_only_remaining_material(expert_env) -> None:
    project_id, material_ids = await _create_project(
        expert_env,
        count=1,
        stage=service.MATERIAL_LEARNING_STAGE,
        status="needs_attention",
    )
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        await repo.update_material(material_ids[0], status="needs_attention", error_message="模型输出需要处理")
        await db.commit()

        with pytest.raises(ValueError, match="至少需要保留一份"):
            await service.exclude_material(
                db,
                project_id=project_id,
                material_id=material_ids[0],
                created_by="user-1",
                reason="排除后项目将没有材料",
            )


@pytest.mark.asyncio
async def test_method_calibration_compares_samples_and_starts_learning(expert_env) -> None:
    """校准将 V1 基线与 V2 小样交给残余评估，再直接进入批量学习。"""

    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)
    model_tasks = CalibrationModelTasks()

    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )
    await service.run_project_orientation(project_id, method_version, model_tasks=model_tasks)

    async with expert_env.factory() as db:
        calibrated_version = await service.confirm_project_method(
            db,
            project_id=project_id,
            created_by="user-1",
        )
    assert calibrated_version == 2

    result = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)
    assert result == {
        "project_id": project_id,
        "method_version": 2,
        "status": "running",
    }
    assert len(model_tasks.calibration_inputs) == 1
    calibration_input = model_tasks.calibration_inputs[0]
    assert calibration_input["learning_plan"] == "# 项目学习方案"
    assert calibration_input["compilation_brief"] == "# 项目专属编译底稿"
    assert [item["material_alias"] for item in calibration_input["samples"]] == ["M001", "M002"]
    assert all("FIRST-" not in str(item) for item in calibration_input["samples"])
    assert len(model_tasks.assessment_inputs) == 1
    assessment_input = model_tasks.assessment_inputs[0]
    assert assessment_input["v1_samples"] == calibration_input["samples"]
    assert [item["material_alias"] for item in assessment_input["v2_samples"]] == ["M001", "M002"]

    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        assert project.stage == service.MATERIAL_LEARNING_STAGE
        assert project.status == "running"
        assert project.learning_plan == "# 校准后的项目学习方案"
        assert project.method_sample_material_ids == material_ids
        assert [sample["material_id"] for sample in project.method_samples] == material_ids
        assert project.method_confirmed_at is not None
        assert project.method_calibration_baseline == []
        with pytest.raises(ValueError, match="没有等待确认"):
            await service.confirm_project_method(db, project_id=project_id, created_by="user-1")
        with pytest.raises(ValueError, match="当前项目状态不能开始项目定向"):
            await service.start_project_orientation(
                db,
                project_id=project_id,
                created_by="user-1",
                representative_material_ids=material_ids,
            )

@pytest.mark.asyncio
async def test_method_confirmation_without_review_issue_starts_learning_directly(expert_env) -> None:
    """没有可泛化 Review 问题时，确认方向后直接进入批量学习。"""

    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)
    model_tasks = OrientationModelTasks()
    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )
    await service.run_project_orientation(project_id, method_version, model_tasks=model_tasks)

    async with expert_env.factory() as db:
        method_version = await service.confirm_project_method(
            db,
            project_id=project_id,
            created_by="user-1",
        )
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None
    assert project.method_version == 1
    assert method_version == 1
    assert project.stage == service.MATERIAL_LEARNING_STAGE
    assert project.status == "running"


@pytest.mark.asyncio
async def test_method_calibration_stops_when_residual_problem_is_method_level(expert_env) -> None:
    """评估明确认为残余会影响批量学习时，项目进入需要处理。"""

    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)
    model_tasks = AttentionCalibrationTasks()
    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )
    await service.run_project_orientation(project_id, method_version, model_tasks=model_tasks)
    async with expert_env.factory() as db:
        calibrated_version = await service.confirm_project_method(
            db,
            project_id=project_id,
            created_by="user-1",
        )

    result = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)
    assert result["status"] == "needs_attention"
    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None
    assert project.stage == service.ORIENTATION_STAGE
    assert project.status == "needs_attention"
    assert project.error_message == "V2 仍会系统性遗漏判断条件"
    assert project.method_calibration_baseline == []


@pytest.mark.asyncio
async def test_method_calibration_automatically_repairs_v2_review_issue(expert_env) -> None:
    """V2 局部 Review 问题由后台自动修订并复核。"""

    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)
    model_tasks = RepairableResidualTasks()
    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )
    await service.run_project_orientation(project_id, method_version, model_tasks=model_tasks)
    async with expert_env.factory() as db:
        calibrated_version = await service.confirm_project_method(
            db,
            project_id=project_id,
            created_by="user-1",
        )

    result = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)
    assert result["status"] == "running"
    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None
    assert project.stage == service.MATERIAL_LEARNING_STAGE
    assert project.method_samples[0]["status"] == "passed"
    assert project.method_samples[0]["final_review"]["decision"] == "pass"
