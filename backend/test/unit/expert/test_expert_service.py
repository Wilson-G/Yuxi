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
    MaterialAutoResolutionResult,
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
    compiler_profile_id: str = "apprenticeship",
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
            compiler_profile_id=compiler_profile_id,
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

    async def design_project_method(self, *, samples: list[dict[str, str]], **_kwargs) -> ProjectMethodResult:
        self.call_counts["method"] += 1
        return ProjectMethodResult(
            learning_plan_markdown="# 项目学习方案",
            compilation_brief_markdown="# 项目专属编译底稿",
            prompt_pack=_prompt_pack(),
            method_sample_aliases=[sample["alias"] for sample in samples[:5]],
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


class RetryableAssessmentTasks(CalibrationModelTasks):
    """让第一次方法评估发生瞬时失败，验证重试只恢复评估。"""

    async def assess_method_calibration(self, **kwargs):
        self.assessment_inputs.append(kwargs)
        if len(self.assessment_inputs) == 1:
            raise ConnectionError("方法评估连接中断")
        return SimpleNamespace(decision="acceptable_residual", reason="局部问题可由逐篇 Repair 处理")


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
    """让多个小样在笔记调用处相互等待，验证它们可以并发启动。"""

    def __init__(self, expected_sample_count: int):
        super().__init__()
        self.expected_sample_count = expected_sample_count
        self.started_titles: set[str] = set()
        self.all_started = asyncio.Event()
        self.release = asyncio.Event()

    async def write_material_note(self, *, title: str, **kwargs) -> MaterialNoteResult:
        self.started_titles.add(title)
        if len(self.started_titles) == self.expected_sample_count:
            self.all_started.set()
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
            description="学习材料中的判断条件与边界",
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
async def test_same_material_units_create_independent_projects_with_fixed_profiles(expert_env) -> None:
    unit_ids = await _create_ready_library_units(expert_env)

    async with expert_env.factory() as db:
        default_project = await service.create_expert_project(
            db,
            name="教材笔记项目",
            description="使用教材笔记法学习同一批材料",
            model_spec="provider:model",
            created_by="user-1",
            material_unit_ids=unit_ids,
        )
        reasoning_project = await service.create_expert_project(
            db,
            name="领域推导项目",
            description="使用领域推导资产法学习同一批材料",
            model_spec="provider:model",
            created_by="user-1",
            material_unit_ids=unit_ids,
            compiler_profile_id="reasoning_assets",
        )

    assert default_project.project_id != reasoning_project.project_id
    assert default_project.compiler_profile_id == "apprenticeship"
    assert reasoning_project.compiler_profile_id == "reasoning_assets"
    async with expert_env.factory() as db:
        default_materials = await ExpertRepository(db).list_project_materials(default_project.project_id)
        reasoning_materials = await ExpertRepository(db).list_project_materials(reasoning_project.project_id)
    assert [item.material_unit_id for item in default_materials] == unit_ids
    assert [item.material_unit_id for item in reasoning_materials] == unit_ids
    assert {item.material_id for item in default_materials}.isdisjoint(item.material_id for item in reasoning_materials)


@pytest.mark.asyncio
async def test_create_project_rejects_unknown_compiler_profile_without_partial_project(expert_env) -> None:
    unit_ids = await _create_ready_library_units(expert_env)

    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="未知专家编译方法"):
            await service.create_expert_project(
                db,
                name="未知方法项目",
                description="不能创建无法恢复的专家项目",
                model_spec="provider:model",
                created_by="user-1",
                material_unit_ids=unit_ids,
                compiler_profile_id="unknown",
            )

    async with expert_env.factory() as db:
        assert await ExpertRepository(db).list_projects() == []


@pytest.mark.asyncio
async def test_create_project_requires_expert_context(expert_env) -> None:
    """创建项目时必须提供模型定向所需的专家背景。"""

    unit_ids = await _create_ready_library_units(expert_env)

    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="请补充专家背景与学习目标"):
            await service.create_expert_project(
                db,
                name="缺少上下文项目",
                description="  ",
                model_spec="provider:model",
                created_by="user-1",
                material_unit_ids=unit_ids,
            )


@pytest.mark.asyncio
async def test_create_project_rejects_duplicate_or_unready_units_without_partial_project(expert_env) -> None:
    [ready_unit, _] = await _create_ready_library_units(expert_env)
    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="不能重复选择"):
            await service.create_expert_project(
                db,
                name="重复材料项目",
                description="学习材料中的判断条件与边界",
                model_spec="provider:model",
                created_by="user-1",
                material_unit_ids=[ready_unit, ready_unit],
            )
        with pytest.raises(ValueError, match="不存在或尚未处理完成"):
            await service.create_expert_project(
                db,
                name="未就绪材料项目",
                description="学习材料中的判断条件与边界",
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
                    description="学习材料中的判断条件与边界",
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
        with pytest.raises(ValueError) as exc_info:
            await service.create_expert_project(
                db,
                name="缺少上下文配置",
                description="学习材料中的判断条件与边界",
                model_spec="provider:model",
                created_by="user-1",
                material_unit_ids=["unit-1"],
            )
    assert str(exc_info.value) == (
        "专家项目模型缺少上下文长度配置，请前往「智能体 > 模型供应商 > 管理模型 > 配置」补充"
    )


@pytest.mark.asyncio
async def test_orientation_loads_compiler_profile_from_project_fact(
    expert_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _ = await _create_project(
        expert_env,
        count=2,
        compiler_profile_id="reasoning_assets",
    )
    material_ids = await _make_materials_ready(expert_env, project_id)
    tasks = OrientationModelTasks()
    loaded_profiles: list[str] = []

    def build_tasks(*_args, compiler_profile, **_kwargs):
        loaded_profiles.append(compiler_profile.profile_id)
        return tasks

    monkeypatch.setattr(service, "ExpertModelTasks", build_tasks)
    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )

    result = await service.run_project_orientation(project_id, method_version)

    assert result["status"] == "awaiting_confirmation"
    assert loaded_profiles == ["reasoning_assets"]


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
        with pytest.raises(ValueError, match="可用材料少于 4 份时必须全部选择"):
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
            representative_material_ids=material_ids,
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
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        materials = await repo.list_project_materials(project_id)
        assert project is not None
        assert project.status == "running"
        assert project.orientation_report == "# 项目定向报告"
        assert project.learning_plan == "# 项目学习方案"
        assert project.compilation_brief == "# 项目专属编译底稿"
        assert project.prompt_pack == _prompt_pack().model_dump()
        persisted_ids = {sample["material_id"] for sample in project.method_samples}
        persisted_titles = {material.title for material in materials if material.material_id in persisted_ids}
        assert "02-material" not in persisted_titles

    result = await service.run_project_orientation(
        project_id,
        method_version,
        model_tasks=model_tasks,
    )
    assert model_tasks.call_counts["orientation"] == 1
    assert model_tasks.call_counts["method"] == 1
    assert model_tasks.call_counts["note:02-material"] == 2
    assert model_tasks.call_counts["review:02-material"] == 2
    for title in persisted_titles:
        assert model_tasks.call_counts[f"note:{title}"] == 1
        assert model_tasks.call_counts[f"review:{title}"] == 1

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
        assert len(project.method_samples) == 3
        assert all(sample["status"] == "passed" for sample in project.method_samples)
        assert all(sample["final_review"]["decision"] == "pass" for sample in project.method_samples)
        assert [material.material_id for material in materials if material.is_representative] == material_ids

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
        with pytest.raises(ValueError, match="请选择 4～20 份代表性材料"):
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
async def test_orientation_requires_four_representative_materials_when_available(expert_env) -> None:
    """可用材料达到四份时，不能只用少量材料完成项目定向。"""

    project_id, _ = await _create_project(expert_env, count=5)
    material_ids = await _make_materials_ready(expert_env, project_id)

    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="请选择 4～20 份代表性材料"):
            await service.start_project_orientation(
                db,
                project_id=project_id,
                created_by="user-1",
                representative_material_ids=material_ids[:3],
            )
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids[:4],
        )

    assert method_version == 1


@pytest.mark.asyncio
async def test_orientation_runs_independent_samples_concurrently(expert_env) -> None:
    project_id, _ = await _create_project(expert_env, count=4)
    material_ids = await _make_materials_ready(expert_env, project_id)

    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )

    tasks = ParallelOrientationSampleTasks(expected_sample_count=4)
    runner = asyncio.create_task(service.run_project_orientation(project_id, method_version, model_tasks=tasks))
    await asyncio.wait_for(tasks.all_started.wait(), timeout=1)
    assert tasks.started_titles == {"01-material", "02-material", "03-material", "04-material"}

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
        "note:03-material": 1,
        "review:03-material": 1,
        "note:04-material": 1,
        "review:04-material": 1,
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
@pytest.mark.parametrize("auto_advance", [False, True])
async def test_nontransferable_note_stops_when_no_material_passes(
    expert_env,
    auto_advance: bool,
) -> None:
    """两种推进模式零份材料通过时都明确停止。"""

    class NontransferableTasks:
        async def write_material_note(self, **_kwargs) -> MaterialNoteResult:
            return MaterialNoteResult(
                decision="no_value",
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
        project = await repo.get_project(project_id)
        assert project is not None
        project.auto_advance = auto_advance
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
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        material = await repo.get_material(material_ids[0])

    assert result["status"] == "needs_attention"
    assert project is not None
    assert project.stage == service.MATERIAL_LEARNING_STAGE
    assert project.status == "needs_attention"
    assert project.error_message == "全部材料均被模型判定不采用，无法形成可信专家 Skill"
    assert material is not None
    assert material.status == "no_value"
    assert material.error_message == "材料没有可迁移的判断方法"


@pytest.mark.asyncio
async def test_manual_mode_keeps_author_attention_for_user_action(expert_env) -> None:
    """人工模式保留内容问题，不调用自动终局模型。"""

    class AuthorAttentionTasks:
        async def write_material_note(self, **_kwargs) -> MaterialNoteResult:
            return MaterialNoteResult(decision="needs_attention", reason="材料观点需要人工判断")

        async def review_material_note(self, **_kwargs) -> MaterialReviewResult:
            raise AssertionError("没有合法 learned 笔记时不应进入普通 Review")

        async def revise_material_note(self, **_kwargs) -> MaterialNoteResult:
            raise AssertionError("没有合法 learned 笔记时不应进入修订")

        async def resolve_material_automatically(self, **_kwargs) -> MaterialAutoResolutionResult:
            raise AssertionError("人工模式不能调用自动终局模型")

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
        model_tasks=AuthorAttentionTasks(),
    )

    async with expert_env.factory() as db:
        material = await ExpertRepository(db).get_material(material_ids[0])
    assert result["status"] == "needs_attention"
    assert material is not None
    assert material.status == "needs_attention"
    assert material.error_message == "材料观点需要人工判断"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_issue",
    ["revision_exhausted", "revision_attention", "review_attention", "author_attention"],
)
async def test_auto_mode_resolves_content_issues_to_complete_learned_note(
    expert_env,
    content_issue: str,
) -> None:
    """自动模式把三类内容卡点收敛为一份可供主题组织的完整笔记。"""

    class AutoResolutionTasks:
        def __init__(self):
            self.revision_calls = 0
            self.resolution_inputs: list[dict] = []

        async def write_material_note(self, *, material_alias: str, **_kwargs) -> MaterialNoteResult:
            if content_issue == "author_attention":
                return MaterialNoteResult(decision="needs_attention", reason="Author 无法决定内容取舍")
            return MaterialNoteResult(
                decision="learned",
                note_markdown="# 当前笔记",
                routing_labels=["判断条件"],
                evidence=[
                    {
                        "material_alias": material_alias,
                        "start_line": 1,
                        "end_line": 2,
                        "supports": "原文支持当前判断",
                    }
                ],
            )

        async def review_material_note(self, **_kwargs) -> MaterialReviewResult:
            if content_issue == "review_attention":
                return MaterialReviewResult(
                    decision="needs_attention",
                    summary="Reviewer 无法完成普通内容取舍",
                    issues=[],
                )
            return MaterialReviewResult(
                decision="revise",
                summary="仍需修订一处内容",
                issues=[
                    {
                        "problem": "判断边界仍不清楚",
                        "affected_part": "判断链",
                        "source_lines": ["L2"],
                        "revision_direction": "按原文收窄",
                    }
                ],
            )

        async def revise_material_note(self, *, note: MaterialNoteResult, **_kwargs) -> MaterialNoteResult:
            self.revision_calls += 1
            if content_issue == "revision_attention":
                return MaterialNoteResult(decision="needs_attention", reason="Repair 无法完成内容取舍")
            return note.model_copy(update={"note_markdown": f"# 第 {self.revision_calls} 次修订"})

        async def resolve_material_automatically(self, **kwargs) -> MaterialAutoResolutionResult:
            self.resolution_inputs.append(kwargs)
            return MaterialAutoResolutionResult(
                decision="learned",
                note_markdown="# 自动终局笔记\n保留完整判断条件。",
                reason="完整材料能够形成可采用的判断方法",
                routing_labels=["条件变化时调整"],
                candidate_questions=["什么条件会改变处理？"],
                evidence=[
                    {
                        "material_alias": kwargs["material_alias"],
                        "start_line": 1,
                        "end_line": 3,
                        "supports": "完整材料支持终局笔记",
                    }
                ],
            )

    project_id, material_ids = await _create_project(expert_env, count=1)
    await _make_materials_ready(expert_env, project_id)
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        assert project is not None
        project.auto_advance = True
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

    tasks = AutoResolutionTasks()
    result = await service.run_material_learning_stage(
        project_id,
        material_ids[0],
        1,
        model_tasks=tasks,
    )

    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        material = await repo.get_material(material_ids[0])
    assert result["status"] == "preparing"
    assert project is not None and project.stage == "organization"
    assert material is not None
    assert material.status == "passed"
    assert material.note == "# 自动终局笔记\n保留完整判断条件。"
    assert material.note_contract["decision"] == "learned"
    assert material.review == {
        "decision": "pass",
        "summary": "完整材料能够形成可采用的判断方法",
        "issues": [],
    }
    assert material.error_message is None
    assert len(tasks.resolution_inputs) == 1
    resolution_input = tasks.resolution_inputs[0]
    assert resolution_input["latest_result"].decision == (
        "needs_attention" if content_issue in {"author_attention", "revision_attention"} else "learned"
    )
    assert (resolution_input["candidate_note"] is None) is (content_issue == "author_attention")
    expected_revisions = {"revision_exhausted": 2, "revision_attention": 1}.get(content_issue, 0)
    assert tasks.revision_calls == expected_revisions


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
async def test_method_calibration_compares_samples_and_waits_for_second_confirmation(expert_env) -> None:
    """校准将 V1 与 V2 小样交给残余评估，并等待用户确认 V2 方法。"""

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
        "status": "awaiting_confirmation",
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
        assert project.stage == service.ORIENTATION_STAGE
        assert project.status == "awaiting_confirmation"
        assert project.learning_plan == "# 校准后的项目学习方案"
        assert project.method_sample_material_ids == material_ids
        assert [sample["material_id"] for sample in project.method_samples] == material_ids
        assert project.method_confirmed_at is None
        assert project.method_calibration_baseline == []
        confirmed_version = await service.confirm_project_method(
            db,
            project_id=project_id,
            created_by="user-1",
            auto_advance=True,
        )
        assert confirmed_version == calibrated_version
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        assert project.stage == service.MATERIAL_LEARNING_STAGE
        assert project.status == "running"
        assert project.method_confirmed_at is not None
        assert project.auto_advance is False
        with pytest.raises(ValueError, match="当前项目状态不能开始项目定向"):
            await service.start_project_orientation(
                db,
                project_id=project_id,
                created_by="user-1",
                representative_material_ids=material_ids,
            )


@pytest.mark.asyncio
async def test_auto_mode_starts_material_learning_after_acceptable_calibration(expert_env) -> None:
    """自动推进已获授权时，校准通过后直接进入逐份学习。"""

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
            auto_advance=True,
        )

    result = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)

    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        materials = await repo.list_project_materials(project_id)
    assert result == {
        "project_id": project_id,
        "method_version": calibrated_version,
        "status": "running",
    }
    assert project is not None
    assert project.stage == service.MATERIAL_LEARNING_STAGE
    assert project.status == "running"
    assert project.auto_advance is True
    assert project.method_confirmed_at is not None
    assert project.method_calibration_baseline == []
    assert all(material.status == "pending" for material in materials)

    repeated = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)
    assert repeated["status"] == "running"
    assert len(model_tasks.assessment_inputs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_advance", [False, True])
async def test_method_calibration_retry_only_repeats_assessment(
    expert_env,
    auto_advance: bool,
) -> None:
    """V2 已持久化后只重试评估，不重复校准方法或生成小样。"""

    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)
    model_tasks = RetryableAssessmentTasks()

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
            auto_advance=auto_advance,
        )

    with pytest.raises(ConnectionError, match="方法评估连接中断"):
        await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)

    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        assert project.stage == service.ORIENTATION_STAGE
        assert project.status == "awaiting_confirmation"
        assert project.method_calibration_completed_at is not None
        assert project.method_calibration_baseline
        assert [sample["material_id"] for sample in project.method_samples] == material_ids
        v2_samples = list(project.method_samples)
    note_calls_after_failure = model_tasks.call_counts.copy()

    result = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)

    assert len(model_tasks.calibration_inputs) == 1
    assert len(model_tasks.assessment_inputs) == 2
    assert model_tasks.call_counts == note_calls_after_failure
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        assert project is not None
        assert project.method_samples == v2_samples
        assert project.method_calibration_baseline == []
        if auto_advance:
            materials = await repo.list_project_materials(project_id)
            assert result["status"] == "running"
            assert project.stage == service.MATERIAL_LEARNING_STAGE
            assert project.status == "running"
            assert all(material.status == "pending" for material in materials)
        else:
            assert result["status"] == "awaiting_confirmation"
            assert project.stage == service.ORIENTATION_STAGE
            assert project.status == "awaiting_confirmation"
            assert project.method_confirmed_at is None


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
            auto_advance=True,
        )
        project = await ExpertRepository(db).get_project(project_id)
        detail = await service.get_expert_project_detail(db, project_id=project_id, created_by="user-1")
    assert project is not None
    assert project.method_version == 1
    assert method_version == 1
    assert project.stage == service.MATERIAL_LEARNING_STAGE
    assert project.status == "running"
    assert project.auto_advance is True
    assert detail["auto_advance"] is True


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
async def test_reorientation_can_replace_auto_advance_selection(expert_env) -> None:
    """重新定向生成的新 V1 可以重新选择自动推进模式。"""

    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)
    calibration_tasks = AttentionCalibrationTasks()

    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )
    await service.run_project_orientation(project_id, method_version, model_tasks=calibration_tasks)
    async with expert_env.factory() as db:
        calibrated_version = await service.confirm_project_method(
            db,
            project_id=project_id,
            created_by="user-1",
            auto_advance=True,
        )
    await service.run_method_calibration(project_id, calibrated_version, model_tasks=calibration_tasks)

    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        assert project.auto_advance is True
        reoriented_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
            feedback="重新调整学习方法",
        )

    await service.run_project_orientation(
        project_id,
        reoriented_version,
        model_tasks=OrientationModelTasks(),
    )
    async with expert_env.factory() as db:
        confirmed_version = await service.confirm_project_method(
            db,
            project_id=project_id,
            created_by="user-1",
            auto_advance=False,
        )
        project = await ExpertRepository(db).get_project(project_id)

    assert project is not None
    assert reoriented_version > calibrated_version
    assert confirmed_version == reoriented_version
    assert project.auto_advance is False


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
    assert result["status"] == "awaiting_confirmation"
    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None
    assert project.stage == service.ORIENTATION_STAGE
    assert project.status == "awaiting_confirmation"
    assert project.method_samples[0]["status"] == "passed"
    assert project.method_samples[0]["final_review"]["decision"] == "pass"
