"""专家项目第 0、1 阶段服务行为测试。"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.expert import service, storage
from yuxi.expert.contracts import (
    MaterialNoteResult,
    MaterialReviewResult,
    ProjectMethodResult,
    ProjectOrientationResult,
    ProjectPromptPack,
)
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
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
    project_id = uuid.uuid4().hex
    project_root = storage.create_project_workspace(project_id)
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        await repo.create_project(
            project_id=project_id,
            name="判断方法项目",
            description="学习材料中的判断条件与边界",
            model_spec="provider:model",
            created_by="user-1",
            stage=stage,
            status=status,
        )
        materials = []
        for index in range(1, count + 1):
            material_id = uuid.uuid4().hex
            raw_path = f"raw/{material_id}.md"
            (project_root / raw_path).write_text(f"RAW-{index:02d}", encoding="utf-8")
            materials.append(
                {
                    "material_id": material_id,
                    "title": f"{index:02d}-material",
                    "source_path": f"{index:02d}-material.md",
                    "raw_path": raw_path,
                    "file_type": ".md",
                    "status": "pending",
                }
            )
        await repo.create_materials(project_id, materials)
        await db.commit()
        stored = await repo.list_project_materials(project_id)
        return project_id, [material.material_id for material in stored]


async def _create_ready_library_units(expert_env) -> list[str]:
    """创建两个可供新项目选择的资料库材料单元。"""

    async with expert_env.factory() as db:
        repo = MaterialLibraryRepository(db)
        await repo.create_library(library_id="library-1", name="测试资料库", created_by="user-1")
        unit_ids = []
        for index, relative_path in enumerate(("first.md", "nested/second.md"), start=1):
            file_id = f"file-{index}"
            await repo.create_source_file(
                file_id=file_id,
                library_id="library-1",
                directory_id=None,
                original_filename=Path(relative_path).name,
                relative_path=relative_path,
                file_type=".md",
                file_size=10,
                source_bucket="materials",
                source_object=f"sources/{file_id}.md",
                markdown_bucket="materials",
                markdown_object=f"normalized/{file_id}.md",
                status="ready",
                unit_status="ready",
                progress=100,
                line_count=3,
                error_message=None,
                created_by="user-1",
            )
            unit_id = f"unit-{index}"
            await repo.create_material_units(
                file_id,
                [{"unit_id": unit_id, "title": f"材料 {index}", "start_line": 1, "end_line": 3}],
            )
            unit_ids.append(unit_id)
        await db.commit()
        return unit_ids


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
async def test_create_project_uses_ready_units_without_raw_or_text_copies(
    expert_env,
) -> None:
    unit_ids = await _create_ready_library_units(expert_env)

    async with expert_env.factory() as db:
        project = await service.create_expert_project(
            db,
            name="提交边界项目",
            description="",
            model_spec="provider:model",
            created_by="user-1",
            material_unit_ids=unit_ids,
        )
        project_id = project.project_id
        assert project.status == "ready"

    project_root = storage.get_expert_projects_root() / project_id
    async with expert_env.factory() as db:
        materials = await ExpertRepository(db).list_project_materials(project_id)
    assert [material.material_unit_id for material in materials] == unit_ids
    assert [material.source_path for material in materials] == ["first.md", "nested/second.md"]
    assert all(material.status == "ready" for material in materials)
    assert all(material.raw_path is None and material.text_path is None for material in materials)
    assert project_root.is_dir()
    assert not (project_root / "raw").exists()
    assert not (project_root / "text").exists()


@pytest.mark.asyncio
async def test_create_project_rejects_duplicate_or_unready_units_without_partial_project(expert_env) -> None:
    [ready_unit, _] = await _create_ready_library_units(expert_env)
    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="不能重复选择"):
            await service.create_expert_project(
                db,
                name="重复材料项目",
                description="",
                model_spec="provider:model",
                created_by="user-1",
                material_unit_ids=[ready_unit, ready_unit],
            )
        with pytest.raises(ValueError, match="不存在或尚未处理完成"):
            await service.create_expert_project(
                db,
                name="未就绪材料项目",
                description="",
                model_spec="provider:model",
                created_by="user-1",
                material_unit_ids=["not-ready"],
            )

    async with expert_env.factory() as db:
        assert await ExpertRepository(db).list_projects() == []


@pytest.mark.asyncio
async def test_create_project_commit_failure_removes_project_and_output_workspace(expert_env) -> None:
    unit_ids = await _create_ready_library_units(expert_env)
    projects_root = storage.get_expert_projects_root()
    before = {path.name for path in projects_root.iterdir()}

    async with expert_env.factory() as db:

        def fail_commit(_session) -> None:
            raise RuntimeError("项目提交失败")

        event.listen(db.sync_session, "before_commit", fail_commit)
        try:
            with pytest.raises(RuntimeError, match="项目提交失败"):
                await service.create_expert_project(
                    db,
                    name="事务失败项目",
                    description="",
                    model_spec="provider:model",
                    created_by="user-1",
                    material_unit_ids=unit_ids,
                )
        finally:
            event.remove(db.sync_session, "before_commit", fail_commit)

    async with expert_env.factory() as db:
        assert await ExpertRepository(db).list_projects() == []
    assert {path.name for path in projects_root.iterdir()} == before


@pytest.mark.asyncio
async def test_create_project_requires_configured_model_context_length(
    expert_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                material_unit_ids=["unit-1"],
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
    runner = asyncio.create_task(service.run_project_orientation(project_id, method_version, model_tasks=tasks))
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
async def test_material_learning_runs_one_independent_job_per_material(expert_env) -> None:
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

    tasks = ConcurrentLearningTasks(titles, failing_title="")
    jobs = [
        asyncio.create_task(service.run_material_learning_stage(project_id, material_id, 1, model_tasks=tasks))
        for material_id in material_ids
    ]
    await _wait_until(lambda: len(tasks.start_order) == len(material_ids))
    assert set(tasks.start_order) == set(titles)
    assert tasks.max_active == len(material_ids)

    for gate in tasks.gates.values():
        gate.set()
    await asyncio.gather(*jobs)

    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        materials = await repo.list_project_materials(project_id)
    assert project is not None
    assert project.stage == "organization"
    assert project.status == "preparing"
    assert all(material.status == "passed" for material in materials)
    assert tasks.call_counts == Counter({title: 1 for title in titles})

    calls_before_recovery = tasks.call_counts.copy()
    second_result = await service.run_material_learning_stage(
        project_id,
        material_ids[0],
        1,
        model_tasks=tasks,
    )
    assert second_result["status"] == "ignored"
    assert tasks.call_counts == calls_before_recovery


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

    result = await service.run_material_learning_stage(
        project_id,
        material_ids[0],
        1,
        model_tasks=NontransferableTasks(),
    )

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
        material_ids[0],
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
        material_ids[0],
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
    result = await service.run_material_learning_stage(
        project_id,
        material_ids[0],
        1,
        model_tasks=tasks,
    )

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
async def test_pause_and_resume_derive_material_learning_from_existing_facts(expert_env) -> None:
    """暂停只写项目状态，恢复根据阶段和材料事实继续。"""

    project_id, _ = await _create_project(
        expert_env,
        count=1,
        stage=service.MATERIAL_LEARNING_STAGE,
        status="running",
    )
    async with expert_env.factory() as db:
        paused = await service.pause_expert_project(db, project_id=project_id, created_by="user-1")
        assert paused.status == "paused"

    async with expert_env.factory() as db:
        resumed = await service.resume_expert_project(db, project_id=project_id, created_by="user-1")
        assert resumed.stage == service.MATERIAL_LEARNING_STAGE
        assert resumed.status == "running"
        assert resumed.message == "正在继续学习材料"

    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        await repo.update_project(
            project_id,
            status="needs_attention",
            message="方法校准存在问题",
            error_message="校准后仍遗漏关键条件",
        )
        await db.commit()
        await service.pause_expert_project(db, project_id=project_id, created_by="user-1")
        resumed = await service.resume_expert_project(db, project_id=project_id, created_by="user-1")

    assert resumed.status == "needs_attention"
    assert resumed.error_message == "校准后仍遗漏关键条件"


@pytest.mark.asyncio
async def test_resume_keeps_running_when_only_part_of_method_samples_finished(expert_env) -> None:
    """部分小样完成不能被误判为已经到达第一次确认点。"""

    project_id, _ = await _create_project(expert_env, count=2, status="running")
    material_ids = await _make_materials_ready(expert_env, project_id)
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        await repo.update_project(
            project_id,
            method_version=1,
            method_sample_material_ids=material_ids,
            method_samples=[{"material_id": material_ids[0], "status": "passed"}],
        )
        await db.commit()
        await service.pause_expert_project(db, project_id=project_id, created_by="user-1")
        resumed = await service.resume_expert_project(db, project_id=project_id, created_by="user-1")

    assert resumed.status == "running"
    assert resumed.message == "正在继续生成项目学习方法"


@pytest.mark.asyncio
async def test_delete_project_rejects_running_and_enabled_then_removes_paused_workspace(expert_env) -> None:
    running_id, [material_id] = await _create_project(expert_env, count=1, status="running")
    running_root = storage.get_project_root(running_id)
    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="必须先暂停"):
            await service.delete_expert_project(db, project_id=running_id, created_by="user-1")
        await service.pause_expert_project(db, project_id=running_id, created_by="user-1")
        await service.delete_expert_project(db, project_id=running_id, created_by="user-1")
        assert await ExpertRepository(db).get_project(running_id) is None
        assert await ExpertRepository(db).get_material(material_id) is None
    assert not running_root.exists()

    enabled_id, _ = await _create_project(expert_env, count=1, stage="release", status="enabled")
    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="必须先卸载"):
            await service.delete_expert_project(db, project_id=enabled_id, created_by="user-1")


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
