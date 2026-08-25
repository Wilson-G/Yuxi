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
    ProjectPlanResult,
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
            SimpleNamespace(model_type="chat", context_length=128_000)
            if model_spec in {"provider:model", "provider:other"}
            else None
        ),
    )

    try:
        yield SimpleNamespace(factory=factory, save_dir=tmp_path)
    finally:
        await engine.dispose()


def _note_prompt(role: str) -> str:
    """构造符合方法合同的三份 note Prompt：共用块逐字一致，仅角色职责句不同。"""

    domain_block = (
        "- 领域基础（可作为补链依据）：领域事实。\n"
        "- 样本观察（只属于样本资产，不代表整个材料包）：样本事实。\n"
        "- 项目假设（待全部材料验证，不得写成已确认方法）：假设句。\n"
        "- 材料缺口：缺口句。"
    )
    label_block = (
        "- 原文明说：归属规则。\n"
        "- 领域补链：补链规则。\n"
        "- 候选解释：降调规则。\n"
        "- 专家质疑：质疑规则。\n"
        "- 材料未知：未知规则。"
    )
    return f"{role} prompt\n## 项目领域推导模型\n{domain_block}\n【五类来源标签】\n{label_block}"


def _prompt_pack() -> ProjectPromptPack:
    return ProjectPromptPack(
        note_author=_note_prompt("note author prompt"),
        note_reviewer=_note_prompt("note reviewer prompt"),
        topic_researcher="topic researcher prompt",
        topic_editor="topic editor prompt",
        handout_editor="handout editor prompt",
        handout_reviewer="handout reviewer prompt",
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


class ActivityRecorderMixin:
    """给测试桩补上真实 ExpertModelTasks 的流水能力（阶段上下文 + 事件缓冲）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.activity_events: list = []
        self._activity_project: dict = {"stage": None, "material_id": None}

    def bind_activity_context(self, *, stage: str, material_id: str | None = None) -> None:
        self._activity_project["stage"] = stage
        self._activity_project["material_id"] = material_id

    def _emit_activity(self, current_step: str, message: str, *, status: str = "info") -> None:
        if not self._activity_project["stage"]:
            return
        self.activity_events.append(
            SimpleNamespace(
                stage=self._activity_project["stage"],
                current_step=current_step,
                status=status,
                material_id=self._activity_project["material_id"],
                message=message,
            )
        )

    def _next_revision_round(self) -> int:
        rounds = [
            int(str(event.current_step).split(":", 1)[1])
            for event in self.activity_events
            if str(event.current_step).startswith("revise:")
        ]
        return (max(rounds) + 1) if rounds else 1


class OrientationModelTasks(ActivityRecorderMixin):
    """记录第 0 阶段调用，并让指定小样的首次 Author 可重试失败。"""

    def __init__(self, *, fail_note_title: str | None = None):
        super().__init__()
        self.fail_note_title = fail_note_title
        self.failed_note = False
        self.call_counts: Counter[str] = Counter()

    async def plan_project_method(self, *, samples: list[dict[str, str]], **_kwargs) -> ProjectPlanResult:
        self.call_counts["plan"] += 1
        return ProjectPlanResult(
            template_name="mentor-oral-teaching.md",
            material_type="老师口述案例",
            expert_identity="能说明判断条件和停止线的老师",
            report_markdown="# 项目定向报告",
            source_risks=["转写不确定"],
            learning_plan_markdown="# 项目学习方案",
            compilation_brief_markdown="# 项目专属编译底稿",
            prompt_pack=_prompt_pack(),
            method_sample_aliases=[sample["alias"] for sample in samples[:5]],
        )

    async def write_material_note(self, *, material_alias: str, title: str, **_kwargs) -> MaterialNoteResult:
        self.call_counts[f"note:{title}"] += 1
        if title == self.fail_note_title and not self.failed_note:
            self.failed_note = True
            raise RuntimeError("sample note failed")
        return MaterialNoteResult(
            decision="learned",
            note_markdown=(
                "# 当前材料的领域推导资产\n\n## 资产定位\n定位。\n\n"
                "## 资产节点目录\n- N01 [输入事实][原文明说] 事实\n\n"
                "## 资产正文\n\n### N01 [输入事实][原文明说]\n内容。\n\n"
                "## CoT 推导示范\nInput: ...\n"
            ),
            routing_labels=["判断条件"],
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

    async def write_material_note(self, **kwargs) -> MaterialNoteResult:
        # 与真实调用层一致：Author 判定结论落一条流水。
        result = await super().write_material_note(**kwargs)
        self._emit_activity("note_draft", f"笔记初稿：{result.decision}")
        return result

    async def review_material_note(self, *, title: str, **_kwargs) -> MaterialReviewResult:
        self.review_rounds[title] += 1
        if title == "01-material" and self.review_rounds[title] == 1:
            review = MaterialReviewResult(
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
        else:
            review = MaterialReviewResult(decision="pass", summary="通过", issues=[])
        self._emit_activity("review", f"Review：{review.decision}——{review.summary}")
        return review

    async def revise_material_note(self, **kwargs) -> MaterialNoteResult:
        result = await self.write_material_note(**kwargs)
        self._emit_activity(f"revise:{self._next_revision_round()}", f"笔记修订：{result.decision}")
        return result

    async def calibrate_project_method(self, **kwargs):
        self.calibration_inputs.append(kwargs)
        self.review_rounds_at_calibration = dict(self.review_rounds)
        return SimpleNamespace(
            learning_plan_markdown="# 校准后的项目学习方案",
            compilation_brief_markdown="# 校准后的项目专属编译底稿",
            prompt_pack=_prompt_pack(),
        )

    async def assess_method_calibration(self, **kwargs):
        self.assessment_inputs.append(kwargs)
        return SimpleNamespace(decision="acceptable_residual", reason="局部问题可由逐篇 Repair 处理")


class InitialAttentionCalibrationTasks(CalibrationModelTasks):
    """让 V1 小样初稿带 needs_attention 问题，V2 使用校准方法后通过。"""

    async def write_material_note(self, **kwargs) -> MaterialNoteResult:
        if not self.calibration_inputs and kwargs["title"] == "01-material":
            result = await super().write_material_note(**kwargs)
            return result.model_copy(
                update={"decision": "needs_attention", "reason": "当前方法仍会遗漏改变处理的条件"}
            )
        return await super().write_material_note(**kwargs)


class AttentionCalibrationTasks(CalibrationModelTasks):
    """模拟 V2 后仍存在方法级问题的校准评估。"""

    async def assess_method_calibration(self, **kwargs):
        self.assessment_inputs.append(kwargs)
        return SimpleNamespace(decision="needs_attention", reason="V2 仍会系统性遗漏判断条件")


class BoundedAutoAssessmentTasks(AttentionCalibrationTasks):
    """前三次评估都判方法级残余，之后放行；用于验证预算耗尽后人工修复能重新解锁。"""

    async def assess_method_calibration(self, **kwargs):
        self.assessment_inputs.append(kwargs)
        if len(self.assessment_inputs) <= 3:
            return SimpleNamespace(decision="needs_attention", reason="本轮小样仍有系统性遗漏")
        return SimpleNamespace(decision="acceptable_residual", reason="局部问题可由逐篇 Repair 处理")


class PersistentSampleFailureTasks(CalibrationModelTasks):
    """初始定向小样带可修问题但通过；进入校准后每轮复跑的 Review 都不通过（手动模式无自动终裁）。"""

    async def review_material_note(self, *, title: str, **_kwargs) -> MaterialReviewResult:
        self.review_rounds[title] += 1
        if self.calibration_inputs:
            return MaterialReviewResult(
                decision="revise",
                summary="校准后仍会遗漏改变处理的条件",
                issues=[
                    {
                        "problem": "仍遗漏改变处理的条件",
                        "affected_part": "判断链",
                        "source_lines": ["L3"],
                        "revision_direction": "保留原文明确条件",
                    }
                ],
            )
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


class AutoResolvedCalibrationTasks(CalibrationModelTasks):
    """Review 始终要求修订；自动终裁把一份收编、另一份判不入选，验证 no_value 小样可通过交接。"""

    async def review_material_note(self, *, title: str, **kwargs) -> MaterialReviewResult:
        if self.calibration_inputs:
            self.review_rounds[title] += 1
            return MaterialReviewResult(
                decision="revise",
                summary="始终要求修订",
                issues=[
                    {
                        "problem": "仍需补回条件",
                        "affected_part": "判断链",
                        "source_lines": ["L3"],
                        "revision_direction": "按原文调整",
                    }
                ],
            )
        # 初始定向沿用默认行为：01 材料首轮给出具体问题、修订后通过，保证确认时能触发校准。
        return await super().review_material_note(title=title, **kwargs)

    async def revise_material_note(self, **kwargs) -> MaterialNoteResult:
        return await self.write_material_note(**kwargs)

    async def resolve_material_automatically(self, *, title: str, material_alias: str, **_kwargs) -> MaterialNoteResult:
        if title == "01-material":
            return MaterialNoteResult(
                decision="learned",
                note_markdown="# 终裁笔记",
                routing_labels=["判断条件"],
                evidence=[
                    {
                        "material_alias": material_alias,
                        "start_line": 1,
                        "end_line": 3,
                        "supports": "终裁保留判断条件",
                    }
                ],
            )
        return MaterialNoteResult(decision="no_value", reason="材料与专家判断无关")


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

    model_tasks = OrientationModelTasks(fail_note_title="02-material")
    with pytest.raises(RuntimeError, match="sample note failed"):
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
    assert model_tasks.call_counts["plan"] == 1
    assert model_tasks.call_counts["note:02-material"] == 2
    for title in persisted_titles:
        assert model_tasks.call_counts[f"note:{title}"] == 1

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
    # 预览模式只跑 Author 初稿，Review 留给确认后的方法校准。
    assert tasks.call_counts == {
        "plan": 1,
        "note:01-material": 1,
        "note:02-material": 1,
        "note:03-material": 1,
        "note:04-material": 1,
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
async def test_initial_attention_repair_calibrates_current_method_without_reorientation(
    expert_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """初始小样失败后保留 V1 证据，校准完整方法并复跑固定小样。"""

    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)
    model_tasks = InitialAttentionCalibrationTasks()

    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )
    result = await service.run_project_orientation(project_id, method_version, model_tasks=model_tasks)
    # 预览模式：小样初稿带明确问题进入 needs_attention，repair 依据该初稿起校准版本。
    assert result["status"] == "needs_attention"

    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        v1_samples = list(project.method_samples)
        v1_baseline = service._build_method_calibration_samples(v1_samples, material_ids)
        assert project.method_sample_material_ids == material_ids

        repaired_version = await service.repair_project_method(
            db,
            project_id=project_id,
            created_by="user-1",
        )
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        assert repaired_version == method_version + 1
        assert project.status == "running"
        assert project.method_confirmed_at is None
        assert project.auto_advance is False
        assert project.method_samples == v1_samples
        assert project.method_calibration_baseline == v1_baseline
        assert project.method_sample_material_ids == material_ids

    def forbidden_profile_read(*_args, **_kwargs):
        raise AssertionError("Review 修复不能重新读取中央 Profile")

    monkeypatch.setattr(service, "get_expert_compiler_profile", forbidden_profile_read)
    result = await service.run_method_calibration(
        project_id,
        repaired_version,
        model_tasks=model_tasks,
    )

    assert result == {
        "project_id": project_id,
        "method_version": repaired_version,
        "status": "running",
    }
    assert model_tasks.call_counts["plan"] == 1
    assert len(model_tasks.calibration_inputs) == 1
    calibration_input = model_tasks.calibration_inputs[0]
    assert calibration_input["learning_plan"] == "# 项目学习方案"
    assert calibration_input["compilation_brief"] == "# 项目专属编译底稿"
    # 放行后先补 Review：校准输入的 V1 小样带独立 Review 意见（01 材料初稿 needs_attention，跳过补跑）。
    assert len(calibration_input["samples"]) == len(v1_baseline)
    for backfilled_item, baseline_item in zip(calibration_input["samples"], v1_baseline, strict=True):
        assert backfilled_item["note"] == baseline_item["note"]
    assert calibration_input["samples"][0]["review"] is None
    assert calibration_input["samples"][1]["review"]["decision"] == "pass"

    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None
    assert project.learning_plan == "# 校准后的项目学习方案"
    assert project.compilation_brief == "# 校准后的项目专属编译底稿"
    assert project.prompt_pack == _prompt_pack().model_dump()
    assert project.method_sample_material_ids == material_ids
    assert [sample["material_id"] for sample in project.method_samples] == material_ids
    assert all(sample["status"] == "passed" for sample in project.method_samples)
    assert project.method_calibration_baseline == []


@pytest.mark.asyncio
async def test_initial_attention_repair_rejects_missing_or_spent_calibration_evidence(expert_env) -> None:
    """修复只接受完整、固定且带具体 Review 问题的初始 attention 方法。"""

    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)
    model_tasks = InitialAttentionCalibrationTasks()
    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )
    await service.run_project_orientation(project_id, method_version, model_tasks=model_tasks)

    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        original_samples = list(project.method_samples)
        original_report = project.orientation_report
        original_plan = project.learning_plan
        original_sample_ids = list(project.method_sample_material_ids)

        project.status = "awaiting_confirmation"
        with pytest.raises(ValueError, match="没有等待修复"):
            await service.repair_project_method(db, project_id=project_id, created_by="user-1")
        project.status = "needs_attention"

        project.orientation_report = ""
        with pytest.raises(ValueError, match="缺少定向报告"):
            await service.repair_project_method(db, project_id=project_id, created_by="user-1")
        project.orientation_report = original_report

        project.learning_plan = ""
        with pytest.raises(ValueError, match="缺少学习方案"):
            await service.repair_project_method(db, project_id=project_id, created_by="user-1")
        project.learning_plan = original_plan

        project.method_sample_material_ids = []
        with pytest.raises(ValueError, match="缺少固定的小样材料清单"):
            await service.repair_project_method(db, project_id=project_id, created_by="user-1")
        project.method_sample_material_ids = original_sample_ids

        # 新流程里 needs_attention 初稿样本没有独立 Review：没有可执行 Review 问题时
        # repair 仍应放行（needs_attention 初稿本身就是校准输入），不能卡死人工出口。
        project.method_samples = [
            {**sample, "review": None, "final_review": None} for sample in original_samples
        ]
        reviewless_repaired_version = await service.repair_project_method(
            db,
            project_id=project_id,
            created_by="user-1",
        )
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        assert reviewless_repaired_version == method_version + 1
        assert project.status == "running"
        # 回到原始事实，继续校验死锁态出口。
        project.stage = service.ORIENTATION_STAGE
        project.status = "needs_attention"
        project.method_samples = original_samples
        project.method_version = method_version
        project.method_calibration_baseline = []
        await db.flush()

        project.method_calibration_completed_at = service.utc_now_naive()
        # 校准已完成且基线为空是死锁态：repair 是预算制人工出口，应当放行并重置校准预算。
        repaired_version = await service.repair_project_method(db, project_id=project_id, created_by="user-1")
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        assert repaired_version == method_version + 1
        assert project.status == "running"
        assert project.method_calibration_completed_at is None
        assert project.method_calibration_rounds == 0
        assert project.auto_advance is False


@pytest.mark.asyncio
async def test_method_calibration_compares_samples_and_starts_learning(expert_env) -> None:
    """校准将 V1 与 V2 小样交给残余评估；方向已在初稿确认，评估通过直接进入批量学习。"""

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
            # 方向已在初稿确认：人工模式校准通过同样直接进入批量学习。
            materials = await repo.list_project_materials(project_id)
            assert result["status"] == "running"
            assert project.stage == service.MATERIAL_LEARNING_STAGE
            assert project.status == "running"
            assert all(material.status == "pending" for material in materials)


@pytest.mark.asyncio
async def test_method_confirmation_without_review_issue_starts_learning_directly(expert_env) -> None:
    """没有可泛化 Review 问题时，确认方向后直接进入批量学习（校准版小样复跑无意见）。"""

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
    # 预览小样无 Review：确认后先跑一轮校准（复跑小样带完整 Review）再评估。
    result = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)
    assert result["status"] == "running"
    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
        detail = await service.get_expert_project_detail(db, project_id=project_id, created_by="user-1")
    assert project is not None
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


async def _confirm_with_calibration(
    expert_env,
    model_tasks,
    *,
    auto_advance: bool,
) -> tuple[str, list[str], int]:
    """驱动一个项目走到首次方法确认并触发校准，返回项目 ID、材料清单和校准版本。"""

    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)
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
    return project_id, material_ids, calibrated_version


@pytest.mark.asyncio
async def test_auto_mode_calibration_budget_exhaustion_then_repair_reopens(expert_env) -> None:
    """自动校准按预算有界续跑：耗尽后带已用轮数停机；人工修复保留自动推进并重置预算解锁。"""

    model_tasks = BoundedAutoAssessmentTasks()
    project_id, material_ids, calibrated_version = await _confirm_with_calibration(
        expert_env,
        model_tasks,
        auto_advance=True,
    )

    first = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)
    assert first["status"] == "running"
    assert first["next_method_version"] == calibrated_version + 1

    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        assert project.method_version == calibrated_version + 1
        assert project.status == "running"
        # 代际确认事实与自动推进授权在续跑期间保持不变。
        assert project.method_confirmed_at is not None
        assert project.auto_advance is True
        assert project.method_calibration_completed_at is None
        assert project.method_calibration_rounds == 1
        # 本轮失败的小样连同 Review 成为下一轮的基线证据。
        assert [sample["material_alias"] for sample in project.method_calibration_baseline] == ["M001", "M002"]

    second = await service.run_method_calibration(project_id, calibrated_version + 1, model_tasks=model_tasks)
    assert second["next_method_version"] == calibrated_version + 2
    third = await service.run_method_calibration(project_id, calibrated_version + 2, model_tasks=model_tasks)
    assert third["status"] == "needs_attention"
    assert "next_method_version" not in third
    assert len(model_tasks.calibration_inputs) == 3
    assert len(model_tasks.assessment_inputs) == 3

    # 并发窗口：旧版本的重复校准任务晚到，只能被忽略，不得再推进版本或重复结算。
    stale = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)
    assert stale == {"project_id": project_id, "ignored": True}
    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        assert project.method_version == calibrated_version + 2
        assert project.status == "needs_attention"
        assert project.error_message == "本轮小样仍有系统性遗漏（自动校准已用 3/3 轮）"
        assert project.method_calibration_rounds == 3

    # 人工修复是死锁态出口：保留 auto_advance、重置轮数预算，并从最新小样重建基线。
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        assert project is not None
        samples = list(project.method_samples)
        samples[0] = {
            **samples[0],
            "review": {
                "decision": "revise",
                "summary": "仍需补回条件",
                "issues": [
                    {
                        "problem": "仍需补回条件",
                        "affected_part": "判断链",
                        "source_lines": ["L3"],
                        "revision_direction": "按原文调整",
                    }
                ],
            },
        }
        project.method_samples = samples
        await db.commit()
        repaired_version = await service.repair_project_method(db, project_id=project_id, created_by="user-1")

    assert repaired_version == calibrated_version + 3
    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
        assert project is not None
        assert project.status == "running"
        assert project.auto_advance is True
        assert project.method_calibration_rounds == 0
        assert project.method_calibration_completed_at is None
        assert [sample["material_alias"] for sample in project.method_calibration_baseline] == ["M001", "M002"]

    # 预算重置后下一轮评估放行：项目自动交接进入逐份学习。
    result = await service.run_method_calibration(project_id, repaired_version, model_tasks=model_tasks)
    assert result["status"] == "running"
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        materials = await repo.list_project_materials(project_id)
    assert project is not None
    assert project.stage == service.MATERIAL_LEARNING_STAGE
    assert project.status == "running"
    assert all(material.status == "pending" for material in materials)


@pytest.mark.asyncio
async def test_manual_mode_sample_failure_stops_without_next_round(expert_env) -> None:
    """手动模式小样复跑仍失败时保持单次停机：不推进版本、不消耗轮数预算。"""

    model_tasks = PersistentSampleFailureTasks()
    project_id, _, calibrated_version = await _confirm_with_calibration(
        expert_env,
        model_tasks,
        auto_advance=False,
    )

    result = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)

    assert result["status"] == "needs_attention"
    assert len(model_tasks.calibration_inputs) == 1
    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None
    assert project.method_version == calibrated_version
    assert project.status == "needs_attention"
    assert project.method_calibration_rounds == 0
    assert project.auto_advance is False
    assert project.error_message and "已用" not in project.error_message
    # 小样复跑失败的停机点早于残余评估：基线保留原始 V1 证据。
    assert project.method_calibration_baseline


@pytest.mark.asyncio
async def test_auto_mode_resolved_samples_including_no_value_hand_off_to_learning(expert_env) -> None:
    """自动模式复跑小样启用终裁：no_value 结论通过就绪校验并随整批材料自动交接。"""

    model_tasks = AutoResolvedCalibrationTasks()
    project_id, _, calibrated_version = await _confirm_with_calibration(
        expert_env,
        model_tasks,
        auto_advance=True,
    )

    result = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)

    assert result["status"] == "running"
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        materials = await repo.list_project_materials(project_id)
    assert project is not None
    assert project.stage == service.MATERIAL_LEARNING_STAGE
    assert project.status == "running"
    statuses = {sample["status"] for sample in project.method_samples}
    assert statuses == {"passed", "no_value"}
    no_value_sample = next(sample for sample in project.method_samples if sample["status"] == "no_value")
    assert no_value_sample["final_review"]["decision"] == "no_value"
    assert all(material.status == "pending" for material in materials)


def test_validate_method_samples_ready_accepts_auto_resolved_no_value_only() -> None:
    """no_value 只有携带终裁 Review 结论时才算可交接；需要处理的小样仍然被拒。"""

    service._validate_method_samples_ready(
        [
            {"status": "passed", "final_review": {"decision": "pass"}},
            {"status": "review_revise", "final_review": {"decision": "revise"}},
            {"status": "no_value", "final_review": {"decision": "no_value"}},
        ]
    )
    with pytest.raises(ValueError, match="无法自动修复"):
        service._validate_method_samples_ready([{"status": "needs_attention", "final_review": {"decision": "no_value"}}])


@pytest.mark.asyncio
async def test_repair_only_blocks_in_flight_calibration(expert_env) -> None:
    """repair 只拦「基线已建且本轮未完成」的在途校准；其余 needs_attention 死锁态都放行。"""

    project_id, material_ids = await _create_project(expert_env, count=2)
    await _make_materials_ready(expert_env, project_id)
    model_tasks = InitialAttentionCalibrationTasks()
    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )
    await service.run_project_orientation(project_id, method_version, model_tasks=model_tasks)

    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        assert project is not None
        baseline = service._build_method_calibration_samples(list(project.method_samples), material_ids)
        project.status = "needs_attention"
        project.method_calibration_baseline = baseline
        project.method_calibration_completed_at = None
        await db.commit()
        with pytest.raises(ValueError, match="请恢复当前校准任务"):
            await service.repair_project_method(db, project_id=project_id, created_by="user-1")


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
    # 与 worker 相同的驱动方式：评估不通过就带着新版本号续跑，直到预算耗尽停机。
    running_version = calibrated_version
    while True:
        calibration_result = await service.run_method_calibration(
            project_id, running_version, model_tasks=calibration_tasks
        )
        next_version = calibration_result.get("next_method_version")
        if isinstance(next_version, int) and next_version > running_version:
            running_version = next_version
            continue
        break
    assert calibration_result["status"] == "needs_attention"

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
    assert confirmed_version == reoriented_version + 1
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
async def test_contract_failure_keeps_generated_method_candidate(expert_env) -> None:
    """方法候选先落库、后校验：合同校验失败时总编产物与样本选择保留在项目事实里，可诊断、可恢复。"""

    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)

    class WhitespacePackTasks(OrientationModelTasks):
        async def plan_project_method(self, *, samples, **_kwargs):
            self.call_counts["plan"] += 1
            return ProjectPlanResult(
                template_name="mentor-oral-teaching.md",
                material_type="老师口述案例",
                expert_identity="能说明判断条件和停止线的老师",
                report_markdown="# 项目定向报告",
                source_risks=["转写不确定"],
                learning_plan_markdown="# 项目学习方案",
                compilation_brief_markdown="# 项目专属编译底稿",
                prompt_pack=ProjectPromptPack(
                    note_author="作者 Prompt。",
                    note_reviewer="审阅 Prompt。",
                    topic_researcher="研究 Prompt。",
                    topic_editor="总编 Prompt。",
                    handout_editor="编讲义 Prompt。",
                    handout_reviewer="审讲义 Prompt。",
                    book_editor="收口 Prompt。",
                    navigation_author="导航 Prompt。",
                    skill_author="   ",
                ),
                method_sample_aliases=[sample["alias"] for sample in samples[:5]],
            )

    async with expert_env.factory() as db:
        method_version = await service.start_project_orientation(
            db,
            project_id=project_id,
            created_by="user-1",
            representative_material_ids=material_ids,
        )

    tasks = WhitespacePackTasks()
    with pytest.raises(ValueError, match="缺少阶段内容: skill_author"):
        await service.run_project_orientation(project_id, method_version, model_tasks=tasks)

    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None
    assert project.learning_plan == "# 项目学习方案"
    assert project.compilation_brief == "# 项目专属编译底稿"
    assert project.prompt_pack["note_author"] == "作者 Prompt。"
    assert project.method_sample_material_ids


@pytest.mark.asyncio
async def test_resume_reruns_orientation_when_candidate_complete_but_samples_missing(expert_env) -> None:
    """校验失败修复后的合法恢复入口：候选齐备且小样未启动时，暂停项目恢复为继续试跑当前版本，
    不被遗留 error_message 卡死。"""

    project_id, _ = await _create_project(expert_env, count=2)
    material_ids = await _make_materials_ready(expert_env, project_id)
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        await repo.update_project(
            project_id,
            method_version=3,
            orientation_report="# 项目定向报告",
            compilation_brief="# 项目专属编译底稿",
            learning_plan="# 项目学习方案",
            prompt_pack=_prompt_pack().model_dump(),
            status="needs_attention",
            error_message="项目 Prompt 包缺少阶段内容: skill_author",
        )
        await db.commit()
        paused = await service.pause_expert_project(db, project_id=project_id, created_by="user-1")
        assert paused.status == "paused"
        resumed = await service.resume_expert_project(db, project_id=project_id, created_by="user-1")

    assert resumed.status == "running"
    assert resumed.message == "项目方法候选已就绪，正在继续试跑小样"


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
    assert project.status == "running"
    assert project.method_samples[0]["status"] == "passed"
    assert project.method_samples[0]["final_review"]["decision"] == "pass"


@pytest.mark.asyncio
async def test_first_calibration_backfills_review_before_calibrating(expert_env) -> None:
    """放行后的第一轮校准先给 V1 预览小样补 Review，再交给总编收敛方法。"""

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
    notes_after_orientation = model_tasks.call_counts.copy()

    async with expert_env.factory() as db:
        calibrated_version = await service.confirm_project_method(
            db,
            project_id=project_id,
            created_by="user-1",
        )

    result = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)
    assert result["status"] == "running"
    # Author 没有因补 Review 被重写成新初稿：01 材料在补 Review 修订一次（note 2 次）、
    # 02 材料直接通过（note 1 次），其余都是复跑 V2 时的预期 Author 调用。
    assert model_tasks.call_counts["note:01-material"] == notes_after_orientation["note:01-material"] + 2
    assert model_tasks.call_counts["note:02-material"] == notes_after_orientation["note:02-material"] + 1
    assert model_tasks.review_rounds["01-material"] >= 1
    assert model_tasks.review_rounds["02-material"] >= 1
    # 补跑发生在校准之前：总编拿到基线时两份小样都已带独立 Review。
    assert model_tasks.review_rounds_at_calibration["01-material"] >= 1
    assert model_tasks.review_rounds_at_calibration["02-material"] >= 1
    calibration_input = model_tasks.calibration_inputs[0]
    # 校准输入的 V1 小样带独立 Review 意见，而不是无意见初稿。
    assert all(item["review"] is not None for item in calibration_input["samples"])
    assert calibration_input["samples"][0]["review"]["issues"]
    assessment_input = model_tasks.assessment_inputs[0]
    assert assessment_input["v1_samples"] == calibration_input["samples"]


@pytest.mark.asyncio
async def test_method_calibration_records_start_and_assessment_activities(expert_env) -> None:
    """校准流水至少包含任务开始与评估结论两条，事件阶段为 orientation。"""

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

    result = await service.run_method_calibration(project_id, calibrated_version, model_tasks=model_tasks)
    assert result["status"] == "running"

    activities = await _list_activities(expert_env, project_id)
    messages = [(item["current_step"], item["message"]) for item in activities]
    start = [item for item in messages if item[0] == "calibration_start"]
    assess = [item for item in messages if item[0] == "assess"]
    assert len(start) == 1
    assert len(assess) == 1
    assert assess[0][1].startswith("校准评估：")
    calibration_events = [
        item for item in activities if item["current_step"] in {"calibration_start", "assess", "calibration"}
    ]
    assert calibration_events
    assert all(item["stage"] == service.ORIENTATION_STAGE for item in calibration_events)
    # 定向计划事件也应存在：首次定向会生成方法候选。
    plan = [item for item in messages if item[0] == "plan"]
    assert plan and "定向计划已生成" in plan[0][1]


@pytest.mark.asyncio
async def test_update_project_model_applies_immediately_for_new_tasks(expert_env) -> None:
    """owner 可在运行中改模型：写库即生效，流水记录切换，非 owner 被拒绝。"""

    project_id, _ = await _create_project(expert_env, count=1)
    async with expert_env.factory() as db:
        project = await service.update_expert_project_model(
            db,
            project_id=project_id,
            created_by="user-1",
            model_spec="provider:model",
        )
    assert project.model_spec == "provider:model"
    # 创建时已是同款模型：无变化即无流水（防重复事件噪音）。
    assert await _list_activities(expert_env, project_id) == []
    async with expert_env.factory() as db:
        project = await service.update_expert_project_model(
            db,
            project_id=project_id,
            created_by="user-1",
            model_spec="provider:other",
        )
    assert project.model_spec == "provider:other"

    # 新任务读取的是最新项目事实：模型取自项目快照。
    async with expert_env.factory() as db:
        stored = await ExpertRepository(db).get_project(project_id)
    assert stored is not None and stored.model_spec == "provider:other"

    activities = await _list_activities(expert_env, project_id)
    switches = [item for item in activities if item["current_step"] == "model_switch"]
    assert len(switches) == 1
    assert switches[0]["message"].startswith("模型已切换为 provider:other")
    # 相同配置重复保存不产生流水也不报错。
    async with expert_env.factory() as db:
        unchanged = await service.update_expert_project_model(
            db,
            project_id=project_id,
            created_by="user-1",
            model_spec="provider:other",
        )
    assert unchanged.model_spec == "provider:other"
    assert len(await _list_activities(expert_env, project_id)) == 1

    # 格式校验与权限边界。
    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="不可用"):
            await service.update_expert_project_model(
                db,
                project_id=project_id,
                created_by="user-1",
                model_spec="provider:missing",
            )
        with pytest.raises(PermissionError):
            await service.update_expert_project_model(
                db,
                project_id=project_id,
                created_by="user-2",
                model_spec="provider:model",
            )

    # 并发数量校验与流水：合法值落库，越界拒绝。
    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="并发数量"):
            await service.update_expert_project_model(
                db,
                project_id=project_id,
                created_by="user-1",
                max_concurrency=service.EXPERT_MAX_MATERIAL_CONCURRENCY + 1,
            )
        with pytest.raises(ValueError, match="并发数量"):
            await service.update_expert_project_model(
                db,
                project_id=project_id,
                created_by="user-1",
                max_concurrency=-1,
            )
        updated = await service.update_expert_project_model(
            db,
            project_id=project_id,
            created_by="user-1",
            max_concurrency=3,
        )
    assert updated.max_concurrency == 3
    activities = await _list_activities(expert_env, project_id)
    concurrency_switches = [
        item for item in activities if item["current_step"] == "model_switch" and "并发数量调整为 3" in item["message"]
    ]
    assert len(concurrency_switches) == 1


def _trace_commit_and_activity(db, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """记录同一会话上 commit 与进展流水写入的先后顺序，供持锁路径回归断言。"""

    order: list[str] = []
    real_commit = db.commit

    async def traced_commit():
        order.append("commit")
        await real_commit()

    monkeypatch.setattr(db, "commit", traced_commit)
    real_append = service._append_activity

    async def traced_append(project_id, *events):
        order.append("activity")
        await real_append(project_id, *events)

    monkeypatch.setattr(service, "_append_activity", traced_append)
    return order


@pytest.mark.asyncio
async def test_model_switch_commits_before_activity_write(expert_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：换模型持锁路径必须先提交释放行锁，再写进展流水。

    流水经独立连接写入，外键检查要对项目行加 FOR KEY SHARE；
    若调用方事务仍持有 FOR UPDATE 行锁，两条连接互相等待即自死锁
    （表现为换模型保存永久转圈、后续暂停/换模型全部排队无响应）。
    SQLite 无行锁语义，故以调用顺序作为回归断言。
    """

    project_id, _ = await _create_project(expert_env, count=1)
    async with expert_env.factory() as db:
        order = _trace_commit_and_activity(db, monkeypatch)
        await service.update_expert_project_model(
            db,
            project_id=project_id,
            created_by="user-1",
            model_spec="provider:other",
        )

    assert order == ["commit", "activity"]


@pytest.mark.asyncio
async def test_confirm_method_commits_before_activity_write(expert_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：确认方法进入材料学习（_start_material_learning）同样先提交再写 stage_start 流水。"""

    project_id, material_ids = await _create_project(expert_env, count=2)
    async with expert_env.factory() as db:
        await ExpertRepository(db).update_project(
            project_id,
            status="awaiting_confirmation",
            learning_plan="# 学习方案",
            compilation_brief="# 编译底稿",
            prompt_pack=_prompt_pack().model_dump(),
            method_sample_material_ids=material_ids,
            method_samples=[{"material_id": material_ids[0], "status": "passed", "note": {"decision": "learned"}}],
            method_calibration_completed_at=service.utc_now_naive(),
        )
        await db.commit()

    async with expert_env.factory() as db:
        order = _trace_commit_and_activity(db, monkeypatch)
        await service.confirm_project_method(db, project_id=project_id, created_by="user-1")

    assert order == ["commit", "activity"]


async def _enter_material_learning(expert_env, *, count: int = 2) -> tuple[str, list[str]]:
    """直接把测试项目推进到材料学习 running 态，返回项目与材料 ID。"""

    project_id, material_ids = await _create_project(expert_env, count=count)
    await _make_materials_ready(expert_env, project_id)
    async with expert_env.factory() as db:
        await ExpertRepository(db).update_project(
            project_id,
            stage=service.MATERIAL_LEARNING_STAGE,
            status="running",
            method_version=1,
            learning_plan="# 已确认的学习方案",
            prompt_pack=_prompt_pack().model_dump(),
        )
        for material_id in material_ids:
            await ExpertRepository(db).update_material(material_id, status="pending")
        await db.commit()
    return project_id, material_ids


async def _list_activities(expert_env, project_id: str) -> list[dict]:
    async with expert_env.factory() as db:
        activities = await ExpertRepository(db).list_activities(project_id, limit=100)
    return [item.to_dict() for item in activities]


@pytest.mark.asyncio
async def test_material_learning_records_activity_for_note_review_revise_and_failure(expert_env) -> None:
    """学习阶段流水四类：note 初稿、review 结论、修订轮与失败逐条落库且带结论。"""

    project_id, material_ids = await _enter_material_learning(expert_env, count=2)
    model_tasks = CalibrationModelTasks()
    await service.run_material_learning_stage(project_id, material_ids[0], 1, model_tasks=model_tasks)

    activities = await _list_activities(expert_env, project_id)
    messages = [(item["current_step"], item["message"]) for item in activities]
    # 01 材料首次 review 给 revise：流水应先后出现笔记初稿、revise 结论、修订与复核。
    assert any(step == "note_draft" and "learned" in msg for step, msg in messages)
    assert any(step == "review" and msg.startswith("Review：revise——") for step, msg in messages)
    assert any(step.startswith("revise:") and "learned" in msg for step, msg in messages)
    assert any(step == "review" and msg.startswith("Review：pass") for step, msg in messages)
    assert any(step == "material_done" for step, _ in messages)
    # 学习阶段事件都带材料 ID 与阶段。
    learning_events = [item for item in activities if item["current_step"] in {"note_draft", "review"}]
    assert learning_events
    assert all(item["material_id"] == material_ids[0] for item in learning_events)
    assert all(item["stage"] == service.MATERIAL_LEARNING_STAGE for item in learning_events)

    # 失败路径：确定性业务失败落一条 warning 流水。
    await service.mark_material_learning_attention(
        project_id,
        material_ids[1],
        1,
        "笔记响应缺少正文",
    )
    activities = await _list_activities(expert_env, project_id)
    failure = [item for item in activities if item["current_step"] == "material_failed"]
    assert len(failure) == 1
    assert failure[0]["status"] == "warning"
    assert "笔记响应缺少正文" in failure[0]["message"]
    assert failure[0]["material_id"] == material_ids[1]


@pytest.mark.asyncio
async def test_material_learning_progress_message_counts_completed_materials(expert_env) -> None:
    """expert_projects.message 呈现“正在学习 N 份（已完成 M）”并在材料完成时刷新。"""

    project_id, material_ids = await _enter_material_learning(expert_env, count=2)
    model_tasks = CalibrationModelTasks()
    first = await service.run_material_learning_stage(project_id, material_ids[0], 1, model_tasks=model_tasks)
    assert first["status"] == "running"
    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None
    assert project.message == "正在学习 2 份材料（已完成 1）"

    second = await service.run_material_learning_stage(project_id, material_ids[1], 1, model_tasks=model_tasks)
    assert second["status"] == "preparing"
    async with expert_env.factory() as db:
        project = await ExpertRepository(db).get_project(project_id)
    assert project is not None
    assert project.stage == "organization"


@pytest.mark.asyncio
async def test_project_detail_returns_recent_activities_for_owner_only(expert_env) -> None:
    """detail 返回最近 20 条流水；非创建人仍拿不到项目。"""

    project_id, _ = await _enter_material_learning(expert_env, count=2)
    model_tasks = CalibrationModelTasks()
    await service.run_material_learning_stage(project_id, (await _project_material_ids(expert_env, project_id))[0], 1, model_tasks=model_tasks)

    async with expert_env.factory() as db:
        detail = await service.get_expert_project_detail(db, project_id=project_id, created_by="user-1")
    assert detail["activities"]
    assert all("message" in item and "current_step" in item for item in detail["activities"])
    assert len(detail["activities"]) <= 20

    async with expert_env.factory() as db:
        with pytest.raises(ValueError, match="专家项目不存在"):
            await service.get_expert_project_detail(db, project_id=project_id, created_by="someone-else")


async def _project_material_ids(expert_env, project_id: str) -> list[str]:
    async with expert_env.factory() as db:
        materials = await ExpertRepository(db).list_project_materials(project_id)
    return [material.material_id for material in materials]


@pytest.mark.asyncio
async def test_terminal_state_trims_activities_to_retention_limit(expert_env) -> None:
    """项目进入终态（暂停）时流水一次性截断到 200 条。"""

    project_id, _ = await _enter_material_learning(expert_env, count=1)
    async with expert_env.factory() as db:
        repo = ExpertRepository(db)
        await repo.append_activities(
            project_id,
            [
                {
                    "stage": service.MATERIAL_LEARNING_STAGE,
                    "current_step": "note_draft",
                    "message": f"第 {index} 条流水",
                }
                for index in range(230)
            ],
        )
        await db.commit()

    async with expert_env.factory() as db:
        await service.pause_expert_project(db, project_id=project_id, created_by="user-1")

    remaining = await _list_activities(expert_env, project_id)
    remaining_all = await _list_all_activities(expert_env, project_id)
    assert len(remaining_all) == service.EXPERT_ACTIVITY_RETAIN_LIMIT
    # 保留的是最新 200 条。
    assert remaining_all[0]["message"] == "第 30 条流水"
    assert remaining_all[-1]["message"] == "第 229 条流水"


async def _list_all_activities(expert_env, project_id: str) -> list[dict]:
    async with expert_env.factory() as db:
        activities = await ExpertRepository(db).list_activities(project_id, limit=1000)
    return [item.to_dict() for item in reversed(activities)]
