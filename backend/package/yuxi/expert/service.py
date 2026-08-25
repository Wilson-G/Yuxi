"""专家项目第 0 阶段与材料学习交接用例。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.skills.repository import SkillRepository
from yuxi.expert.compiler_profiles import (
    DEFAULT_COMPILER_PROFILE_ID,
    get_expert_compiler_profile,
    list_expert_compiler_profiles,
)
from yuxi.expert.contracts import (
    MaterialNoteResult,
    MaterialReviewResult,
    ProjectPromptPack,
    ReviewIssue,
)
from yuxi.expert.materials import read_expert_material_text
from yuxi.expert.model_tasks import ExpertModelTasks
from yuxi.expert.storage import (
    StoredMaterialSource,
    create_project_output_workspace,
    delete_project_workspace,
    parse_material,
)
from yuxi.expert.concurrency import get_expert_concurrency
from yuxi.models.providers.cache import model_cache
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

MAX_MATERIAL_REVISIONS = 2
MIN_REPRESENTATIVE_MATERIALS = 4
MAX_REPRESENTATIVE_MATERIALS = 20
MAX_METHOD_SAMPLES = 5
# 项目流水只在进入终态时一次性截断，正常运行不做写入时懒清理。
EXPERT_ACTIVITY_RETAIN_LIMIT = 200

# 编译方法是否含资产节点目录的判定缓存，避免修复/校准流程重复读取中央 Profile。
_ASSET_NODE_PROFILE_CACHE: dict[str, bool] = {}


def _project_uses_asset_nodes(compiler_profile_id: str) -> bool:
    """当前编译方法是否要求领域推导资产节点目录结构；结果按 profile 缓存。"""

    clean_id = str(compiler_profile_id or "")
    cached = _ASSET_NODE_PROFILE_CACHE.get(clean_id)
    if cached is None:
        profile = get_expert_compiler_profile(clean_id, validate_assets=False)
        cached = profile.includes_asset_node_catalog
        _ASSET_NODE_PROFILE_CACHE[clean_id] = cached
    return cached


def _apply_asset_note_structure_gate(
    note: MaterialNoteResult,
    *,
    uses_asset_nodes: bool,
    alias: str,
    auto_advance: bool = False,
    synthesized_review_out: list[MaterialReviewResult] | None = None,
) -> None:
    """对资产方法的学习结果执行规范化与结构合同检查。

    可修复的格式偏差已在规范化中自动补齐；规范化后仍不满足结构合同说明
    节点目录与正文存在真实不一致，需要 Author 补内容，一律合成 revise Review
    经 synthesized_review_out 交回修订循环。auto_advance 仅保留调用兼容。
    """

    if not uses_asset_nodes or note.decision != "learned":
        return

    from yuxi.expert.organization_service import normalize_asset_note_markdown, validate_asset_note_structure

    normalized = normalize_asset_note_markdown(note.note_markdown, material_alias=alias)
    try:
        validate_asset_note_structure(normalized, material_alias=alias)
    except ValueError as exc:
        # 确定性格式偏差已在规范化中修复；这里剩的是节点目录与正文真实不一致，
        # 一律合成 revise 意见交回修订循环（人工模式同样自动修复，不中断流程）。
        synthesized_review = MaterialReviewResult(
            decision="revise",
            summary=str(exc),
            issues=[
                ReviewIssue(
                    problem=str(exc),
                    affected_part="资产节点目录/资产正文",
                    revision_direction=(
                        "按异常信息修正节点目录与正文三级标题，使两者同序一一对应；"
                        "结构由系统校验，不要增删正文标题以外的层级"
                    ),
                )
            ],
        )
        if synthesized_review_out is not None:
            synthesized_review_out.append(synthesized_review)
        return
    note.note_markdown = normalized


# 每个方法确认代际允许的自动校准执行上限：首次校准加两次续跑；耗尽后停在需要处理，防止无人值守无限烧钱。
MAX_METHOD_CALIBRATION_ROUNDS = 3
ORIENTATION_STAGE = "orientation"
# 材料学习并发上限的可配置上界；0 表示不限制（历史默认行为）。
EXPERT_MAX_MATERIAL_CONCURRENCY = 20
MATERIAL_LEARNING_STAGE = "material_learning"


async def create_expert_project(
    db: AsyncSession,
    *,
    name: str,
    description: str,
    model_spec: str,
    created_by: str,
    material_unit_ids: list[str],
    compiler_profile_id: str = DEFAULT_COMPILER_PROFILE_ID,
) -> Any:
    """从资料库就绪单元创建正文已经可读的专家项目。"""

    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("专家项目名称不能为空")
    clean_description = str(description or "").strip()
    if not clean_description:
        raise ValueError("请补充专家背景与学习目标")
    compiler_profile = get_expert_compiler_profile(compiler_profile_id)
    selected_ids = [str(unit_id or "").strip() for unit_id in material_unit_ids]
    if not selected_ids or any(not unit_id for unit_id in selected_ids):
        raise ValueError("请至少选择一份已处理材料")
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("不能重复选择同一材料单元")
    model_info = model_cache.get_model_info(model_spec)
    if model_info is None or model_info.model_type != "chat":
        raise ValueError(f"专家项目模型不可用: {model_spec}")
    if not int(model_info.context_length or 0):
        raise ValueError("专家项目模型缺少上下文长度配置，请前往「智能体 > 模型供应商 > 管理模型 > 配置」补充")

    material_rows = await MaterialLibraryRepository(db).list_ready_material_units_by_ids(selected_ids)
    material_by_id = {unit.unit_id: (unit, source_file) for unit, source_file in material_rows}
    if set(material_by_id) != set(selected_ids):
        raise ValueError("所选材料单元不存在或尚未处理完成")

    project_id = uuid.uuid4().hex
    create_project_output_workspace(project_id)
    try:
        repo = ExpertRepository(db)
        project = await repo.create_project(
            project_id=project_id,
            name=clean_name,
            description=clean_description,
            model_spec=model_spec,
            compiler_profile_id=compiler_profile.profile_id,
            created_by=created_by,
            stage=ORIENTATION_STAGE,
            status="ready",
        )
        project.message = "材料正文已准备好，请选择代表样本"
        await repo.create_materials(
            project_id,
            [
                {
                    "material_id": uuid.uuid4().hex,
                    "material_unit_id": unit.unit_id,
                    "source_file_id": source_file.file_id,
                    "title": unit.title,
                    "source_path": source_file.relative_path,
                    "source_start_line": unit.start_line,
                    "source_end_line": unit.end_line,
                    "raw_path": None,
                    "text_path": None,
                    "file_type": source_file.file_type,
                    "status": "ready",
                    "line_count": unit.end_line - unit.start_line + 1,
                    "token_count": 0,
                }
                for unit, source_file in (material_by_id[unit_id] for unit_id in selected_ids)
            ],
        )
        await db.commit()
        await db.refresh(project)
        return project
    except Exception:
        await db.rollback()
        await asyncio.to_thread(delete_project_workspace, project_id)
        raise


async def list_expert_projects(db: AsyncSession, *, created_by: str) -> list[dict[str, Any]]:
    """列出当前用户的专家项目摘要。"""

    projects = await ExpertRepository(db).list_projects(created_by=created_by)
    return [
        {
            "project_id": project.project_id,
            "name": project.name,
            "description": project.description,
            "compiler_profile_id": project.compiler_profile_id,
            "stage": project.stage,
            "status": project.status,
            "message": project.message,
            "error_message": project.error_message,
            "method_version": project.method_version,
            "skill_slug": project.skill_slug,
            "created_at": project.to_dict()["created_at"],
            "updated_at": project.to_dict()["updated_at"],
        }
        for project in projects
    ]


def list_expert_compiler_profile_options() -> list[dict[str, str]]:
    """返回创建专家项目时可选择的内置编译方法。"""

    return list_expert_compiler_profiles()


async def get_expert_project_detail(
    db: AsyncSession,
    *,
    project_id: str,
    created_by: str,
) -> dict[str, Any]:
    """返回项目页面恢复当前阶段所需的事实和产物。"""

    repo = ExpertRepository(db)
    project = await repo.get_project(project_id, created_by=created_by)
    if project is None:
        raise ValueError("专家项目不存在")
    materials = await repo.list_project_materials(project_id)
    data = project.to_dict()
    data.pop("skill_dir", None)
    data["materials"] = [_serialize_material(material) for material in materials]
    data["material_summary"] = _material_summary(materials)
    if project.organization_version > 0:
        batches = await repo.list_research_batches(project_id, project.organization_version)
        topics = await repo.list_topics(project_id, project.organization_version)
        data["research_batches"] = [batch.to_dict() for batch in batches]
        data["topics"] = [topic.to_dict() for topic in topics]
    else:
        data["research_batches"] = []
        data["topics"] = []
    activities = await repo.list_activities(project_id, limit=20)
    data["activities"] = [activity.to_dict() for activity in reversed(activities)]
    return data


async def get_expert_material_text(
    db: AsyncSession,
    *,
    project_id: str,
    material_id: str,
    created_by: str,
) -> dict[str, str]:
    """供项目页面核对一份已经转换完成的可靠正文。"""

    repo = ExpertRepository(db)
    project = await repo.get_project(project_id, created_by=created_by)
    material = await repo.get_material(material_id)
    if project is None or material is None or material.project_id != project_id:
        raise ValueError("专家材料不存在")
    return {
        "material_id": material.material_id,
        "title": material.title,
        "source_path": material.source_path,
        "text": await read_expert_material_text(material),
    }


async def pause_expert_project(
    db: AsyncSession,
    *,
    project_id: str,
    created_by: str,
) -> Any:
    """把项目标记为暂停，恢复扫描不会重新投递它。"""

    project = await ExpertRepository(db).lock_project(project_id)
    if project is None or project.created_by != created_by:
        raise ValueError("专家项目不存在")
    if project.status == "enabled":
        raise ValueError("已启用的专家项目不能暂停")
    if project.status == "paused":
        raise ValueError("专家项目已经暂停")
    project.status = "paused"
    project.message = "项目已暂停，等待显式恢复"
    project.updated_at = utc_now_naive()
    await db.commit()
    # 暂停是停机终态：一次性截断流水，避免长期项目无限累积。
    await trim_expert_project_activities(project_id)
    return project


async def resume_expert_project(
    db: AsyncSession,
    *,
    project_id: str,
    created_by: str,
) -> Any:
    """依据项目阶段和子项事实恢复暂停项目。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.created_by != created_by:
        raise ValueError("专家项目不存在")
    if project.status != "paused":
        raise ValueError("只有暂停项目可以恢复")

    materials = await repo.list_project_materials(project_id)
    next_status, message = await _derive_resumed_project_status(repo, project, materials)
    project.status = next_status
    project.message = message
    if next_status != "needs_attention":
        project.error_message = None
    project.updated_at = utc_now_naive()
    await db.commit()
    return project


async def delete_expert_project(
    db: AsyncSession,
    *,
    project_id: str,
    created_by: str,
) -> None:
    """删除项目事实和私有 workspace，不触碰资料库材料。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.created_by != created_by:
        raise ValueError("专家项目不存在")
    if project.status in {"running", "preparing"}:
        raise ValueError("运行中的专家项目必须先暂停")
    if project.status == "enabled":
        raise ValueError("已启用的专家 Skill 必须先卸载")
    if project.skill_slug and await SkillRepository(db).get_by_slug(project.skill_slug) is not None:
        raise ValueError("已启用的专家 Skill 必须先卸载")

    await repo.delete_project(project_id)
    try:
        await asyncio.to_thread(delete_project_workspace, project_id)
    except Exception:
        await db.rollback()
        raise
    await db.commit()


async def _derive_resumed_project_status(
    repo: ExpertRepository,
    project: Any,
    materials: list[Any],
) -> tuple[str, str]:
    """从现有项目子项事实推导恢复状态和用户可见说明。"""

    # 方法候选完整且小样尚未启动时，恢复直接续跑当前版本：这是校验失败修复后的合法恢复入口，
    # 不被遗留 error_message 卡死；复用候选不消耗模型调用。
    if (
        project.stage == ORIENTATION_STAGE
        and project.method_version > 0
        and not project.method_samples
        and not any(item.status in {"pending", "converting", "needs_attention"} for item in materials)
        and _method_candidate_complete(
            orientation_report=project.orientation_report or "",
            compilation_brief=project.compilation_brief or "",
            learning_plan=project.learning_plan or "",
            prompt_pack=project.prompt_pack or {},
        )
    ):
        return "running", "项目方法候选已就绪，正在继续试跑小样"

    if project.error_message:
        return "needs_attention", "项目仍有问题需要处理"

    if project.stage == ORIENTATION_STAGE:
        if any(item.status in {"pending", "converting"} for item in materials):
            return "preparing", "正在继续转换材料"
        attention = next((item for item in materials if item.status == "needs_attention"), None)
        if attention is not None:
            project.error_message = attention.error_message
            return "needs_attention", "有材料需要处理后才能继续"
        if project.method_calibration_baseline and project.method_calibration_completed_at is None:
            return "running", "正在继续校准项目学习方法"
        if project.method_samples:
            sample_statuses = {item.get("status") for item in project.method_samples if isinstance(item, dict)}
            completed_sample_ids = {
                item.get("material_id")
                for item in project.method_samples
                if isinstance(item, dict) and item.get("material_id")
            }
            expected_sample_ids = set(project.method_sample_material_ids or [])
            if (
                expected_sample_ids
                and completed_sample_ids == expected_sample_ids
                and sample_statuses <= {"passed", "review_revise"}
            ):
                return "awaiting_confirmation", "学习方案和真实小样已就绪，等待确认怎么学"
            if sample_statuses and sample_statuses <= {"passed", "review_revise"}:
                return "running", "正在继续生成项目学习方法"
            return "needs_attention", "项目方法小样仍需要处理"
        if project.method_version > 0:
            return "running", "正在继续生成项目学习方法"
        if any(item.status == "ready" for item in materials):
            return "ready", "材料正文已准备好，请选择代表样本"
        return "needs_attention", "项目没有可用于学习的材料"

    if project.stage == MATERIAL_LEARNING_STAGE:
        if any(item.status in {"pending", "learning", "review_revise"} for item in materials):
            return "running", "正在继续学习材料"
        attention = next((item for item in materials if item.status == "needs_attention"), None)
        if attention is not None:
            project.error_message = attention.error_message
            return "needs_attention", "有材料需要处理后才能组织主题"
        return "running", "正在继续收口材料学习结果"

    if project.stage == "organization":
        if project.organization_version == 0:
            return "preparing", "正在继续准备主题方案"
        batches = await repo.list_research_batches(project.project_id, project.organization_version)
        if any(batch.status in {"pending", "running"} for batch in batches):
            return "running", "正在继续准备候选主题"
        failed_batch = next((batch for batch in batches if batch.status == "needs_attention"), None)
        if failed_batch is not None:
            project.error_message = failed_batch.error_message
            return "needs_attention", "候选主题研究仍需要处理"
        topics = await repo.list_topics(project.project_id, project.organization_version)
        samples = [topic for topic in topics if topic.is_sample]
        attention = next((topic for topic in samples if topic.status == "needs_attention"), None)
        if attention is not None:
            project.error_message = attention.error_message
            return "needs_attention", "讲义小样仍需要处理"
        if samples and all(topic.status == "passed" for topic in samples):
            return "awaiting_confirmation", "主题方案和讲义小样已就绪，等待确认怎么组织"
        return "running", "正在继续准备主题方案和讲义小样"

    if project.stage == "handout_compilation":
        topics = await repo.list_topics(project.project_id, project.organization_version)
        attention = next((topic for topic in topics if topic.status == "needs_attention"), None)
        if attention is not None:
            project.error_message = attention.error_message
            return "needs_attention", "讲义仍需要处理后才能完成编译"
        return "running", "正在继续编译专家讲义"

    if project.stage == "release":
        return "ready", "专家 Skill 成品已就绪，等待预览后手动启用"
    raise ValueError("专家项目阶段无法恢复")


async def prepare_expert_project(project_id: str) -> dict[str, Any]:
    """转换项目内尚未完成的原始材料，并按项目记录决定下一步。"""

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None:
            raise ValueError("专家项目不存在")
        if project.status == "paused":
            return {"project_id": project_id, "status": "paused"}
        if project.stage != ORIENTATION_STAGE:
            return {"project_id": project_id, "status": project.status}
        materials = await repo.list_project_materials(project_id)
        pending_ids = [item.material_id for item in materials if item.status in {"pending", "converting"}]
        if not pending_ids:
            await _finish_material_preparation(db, project_id)
            return {"project_id": project_id, "status": project.status}
        await repo.update_project(
            project_id,
            status="preparing",
            message=f"正在转换 {len(pending_ids)} 份材料",
            error_message=None,
        )

    semaphore = asyncio.Semaphore(
        await get_expert_concurrency("expert_material_parse_concurrency", 8)
    )

    async def convert(material_id: str) -> None:
        async with semaphore:
            await _prepare_one_material(project_id, material_id)

    await asyncio.gather(*(convert(material_id) for material_id in pending_ids))
    async with pg_manager.get_async_session_context() as db:
        await _finish_material_preparation(db, project_id)
        project = await ExpertRepository(db).get_project(project_id)
        return {"project_id": project_id, "status": project.status if project else "missing"}


async def start_project_orientation(
    db: AsyncSession,
    *,
    project_id: str,
    created_by: str,
    representative_material_ids: list[str],
    feedback: str = "",
) -> int:
    """固定用户明确选择的样本并创建一个新的项目方法版本。"""

    selected_ids = list(dict.fromkeys(representative_material_ids))

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.created_by != created_by:
        raise ValueError("专家项目不存在")
    if project.stage != ORIENTATION_STAGE or project.status not in {
        "ready",
        "awaiting_confirmation",
        "needs_attention",
    }:
        raise ValueError("当前项目状态不能开始项目定向")

    materials = await repo.list_project_materials(project_id)
    ready_materials = [item for item in materials if item.status == "ready"]
    required_count = min(MIN_REPRESENTATIVE_MATERIALS, len(ready_materials))
    if not required_count <= len(selected_ids) <= MAX_REPRESENTATIVE_MATERIALS:
        if len(ready_materials) < MIN_REPRESENTATIVE_MATERIALS:
            raise ValueError("可用材料少于 4 份时必须全部选择")
        raise ValueError("请选择 4～20 份代表性材料")
    if len(ready_materials) < MIN_REPRESENTATIVE_MATERIALS and set(selected_ids) != {
        item.material_id for item in ready_materials
    }:
        raise ValueError("可用材料少于 4 份时必须全部选择")
    by_id = {item.material_id: item for item in materials}
    if any(material_id not in by_id or by_id[material_id].status != "ready" for material_id in selected_ids):
        raise ValueError("代表性材料必须已经转换为可靠正文")
    unresolved = [item for item in materials if item.status == "needs_attention"]
    if unresolved:
        raise ValueError("请先处理或排除无法转换的材料")

    for material in materials:
        material.is_representative = material.material_id in selected_ids
    project.method_version += 1
    project.method_confirmed_at = None
    project.method_calibration_completed_at = None
    project.method_calibration_baseline = []
    project.method_calibration_rounds = 0
    project.representative_material_ids = selected_ids
    project.orientation_report = None
    project.compilation_brief = None
    project.learning_plan = None
    project.prompt_pack = {}
    project.method_sample_material_ids = []
    project.method_samples = []
    project.method_feedback = str(feedback or "").strip()
    project.status = "running"
    project.message = "总编模型正在形成项目学习方法并试跑真实小样"
    project.error_message = None
    project.updated_at = utc_now_naive()
    await db.commit()
    return project.method_version


def _method_candidate_complete(
    *,
    orientation_report: str,
    compilation_brief: str,
    learning_plan: str,
    prompt_pack: Any,
) -> bool:
    """方法三件套与 Prompt 包是否齐备；齐备即视为已有方法候选，可复用不必重新生成。"""

    return bool(orientation_report.strip() and compilation_brief.strip() and learning_plan.strip() and prompt_pack)


async def run_project_orientation(
    project_id: str,
    method_version: int,
    *,
    model_tasks: ExpertModelTasks | None = None,
    auto_advance: bool = False,
    review_deferred: bool = True,
) -> dict[str, Any]:
    """生成当前方法版本的定向、专属 Prompt、笔记小样与 Review。

    首次定向默认只出小样初稿供用户确认方向（review_deferred）；
    方法校准复跑同批小样时传 review_deferred=False 跑完整 Review 以对比收敛效果。
    """

    async def _build_tasks() -> ExpertModelTasks:
        # 快照事实决定是否校验 Profile 资产：已有方法候选时允许资产缺失（复用旧产物）。
        async with pg_manager.get_async_session_context() as db:
            project = await ExpertRepository(db).get_project(project_id)
        if project is None:
            raise ValueError("专家项目不存在")
        return ExpertModelTasks(
            compiler_profile=get_expert_compiler_profile(
                project.compiler_profile_id,
                validate_assets=not _method_candidate_complete(
                    orientation_report=project.orientation_report or "",
                    compilation_brief=project.compilation_brief or "",
                    learning_plan=project.learning_plan or "",
                    prompt_pack=project.prompt_pack,
                ),
            ),
        )

    tasks = model_tasks or await _build_tasks()
    bind_activity = getattr(tasks, "bind_activity_context", None)
    if bind_activity is not None:
        bind_activity(stage=ORIENTATION_STAGE)
    try:
        return await _run_project_orientation(
            project_id,
            method_version,
            tasks,
            auto_advance=auto_advance,
            review_deferred=review_deferred,
        )
    finally:
        # 无论成败都把本任务积累的流水落库；写库失败由 _append_activity 自行吞掉。
        if getattr(tasks, "activity_events", None):
            await _append_activity(project_id, *_drain_activity_events(tasks))


def _emit_activity(tasks: Any, current_step: str, message: str, *, status: str = "info") -> None:
    """调用层补发的流水事件走同一实例缓冲，保证与任务层事件顺序一致。"""

    emit = getattr(tasks, "_emit_activity", None)
    if emit is not None:
        emit(current_step, message, status=status)


async def _run_project_orientation(
    project_id: str,
    method_version: int,
    tasks: ExpertModelTasks,
    *,
    auto_advance: bool = False,
    review_deferred: bool = True,
) -> dict[str, Any]:

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None:
            raise ValueError("专家项目不存在")
        if (
            project.stage != ORIENTATION_STAGE
            or project.status != "running"
            or project.method_version != method_version
        ):
            return {"project_id": project_id, "ignored": True}
        materials = await repo.list_project_materials(project_id)
        project_snapshot = {
            "name": project.name,
            "description": project.description,
            "model_spec": project.model_spec,
            "compiler_profile_id": project.compiler_profile_id,
            "feedback": project.method_feedback,
            "representative_ids": list(project.representative_material_ids or []),
            "orientation_report": project.orientation_report or "",
            "compilation_brief": project.compilation_brief or "",
            "learning_plan": project.learning_plan or "",
            "prompt_pack": dict(project.prompt_pack or {}),
            "method_sample_material_ids": list(project.method_sample_material_ids or []),
            "method_samples": list(project.method_samples or []),
        }

    aliases = {material.material_id: f"M{index:03d}" for index, material in enumerate(materials, start=1)}
    uses_asset_nodes = _project_uses_asset_nodes(project_snapshot["compiler_profile_id"])
    catalog = _build_material_catalog(materials, aliases)
    samples = []
    representative_materials = []
    for material_id in project_snapshot["representative_ids"]:
        material = next((item for item in materials if item.material_id == material_id), None)
        if material is None:
            raise ValueError("代表性材料正文不存在")
        representative_materials.append(material)
        samples.append(
            {
                "alias": aliases[material.material_id],
                "title": material.title,
                "source": material.source_path,
                "text": await read_expert_material_text(material),
            }
        )

    has_method_candidate = _method_candidate_complete(
        orientation_report=project_snapshot["orientation_report"],
        compilation_brief=project_snapshot["compilation_brief"],
        learning_plan=project_snapshot["learning_plan"],
        prompt_pack=project_snapshot["prompt_pack"],
    )
    if has_method_candidate:
        learning_plan = project_snapshot["learning_plan"]
        prompt_pack = ProjectPromptPack.model_validate(project_snapshot["prompt_pack"])
        sample_material_ids = project_snapshot["method_sample_material_ids"]
        _validate_method_contract(learning_plan, project_snapshot["compilation_brief"], prompt_pack)
    else:
        plan = await tasks.plan_project_method(
            model_spec=project_snapshot["model_spec"],
            project_name=project_snapshot["name"],
            project_description=project_snapshot["description"],
            material_catalog=catalog,
            samples=samples,
            feedback=project_snapshot["feedback"],
        )
        selected_aliases = set(plan.method_sample_aliases)
        available_aliases = {sample["alias"] for sample in samples}
        required_sample_count = min(MIN_REPRESENTATIVE_MATERIALS, len(samples))
        maximum_sample_count = min(MAX_METHOD_SAMPLES, len(samples))
        if (
            not selected_aliases <= available_aliases
            or not required_sample_count <= len(selected_aliases) <= maximum_sample_count
        ):
            if len(samples) < MIN_REPRESENTATIVE_MATERIALS:
                raise ValueError("项目方法必须使用全部代表材料试跑小样")
            raise ValueError(
                "项目方法没有从代表材料中选择 4～5 份试跑小样："
                f"收到 {sorted(selected_aliases)}，可选 {sorted(available_aliases)}"
            )
        sample_material_ids = [
            material.material_id
            for material, sample in zip(representative_materials, samples, strict=True)
            if sample["alias"] in selected_aliases
        ]
        learning_plan = plan.learning_plan_markdown
        prompt_pack = plan.prompt_pack

        async with pg_manager.get_async_session_context() as db:
            project = await ExpertRepository(db).lock_project(project_id)
            if (
                project is None
                or project.method_version != method_version
                or project.stage != ORIENTATION_STAGE
                or project.status != "running"
            ):
                return {"project_id": project_id, "ignored": True}
            project.orientation_report = plan.report_markdown
            project.compilation_brief = plan.compilation_brief_markdown
            project.learning_plan = learning_plan
            project.prompt_pack = prompt_pack.model_dump()
            project.method_sample_material_ids = sample_material_ids
            project.updated_at = utc_now_naive()
            await db.flush()

        # 方法候选先落库、后校验：校验失败时保留已生成的总编产物供诊断，恢复路径可复用，不重复消耗模型调用。
        _validate_method_contract(learning_plan, plan.compilation_brief_markdown, prompt_pack)
        _emit_activity(
            tasks,
            "plan",
            (
                f"定向计划已生成：选定 {len(sample_material_ids)} 份小样"
                f"（{', '.join(sorted(sample['alias'] for sample in samples if sample['alias'] in selected_aliases))}）"
            ),
        )

    if not sample_material_ids:
        raise ValueError("项目方法缺少试跑小样选择")
    sample_pairs = [
        (material, sample)
        for material, sample in zip(representative_materials, samples, strict=True)
        if material.material_id in set(sample_material_ids)
    ]
    completed_samples = {
        item.get("material_id"): item
        for item in project_snapshot["method_samples"]
        if isinstance(item, dict) and item.get("material_id") in sample_material_ids
    }

    async def run_sample(material: Any, sample: dict[str, str]) -> dict[str, Any]:
        result = completed_samples.get(material.material_id)
        if result is not None:
            return result

        result = await _run_method_sample(
            tasks=tasks,
            model_spec=project_snapshot["model_spec"],
            learning_plan=learning_plan,
            prompt_pack=prompt_pack,
            material=material,
            alias=sample["alias"],
            text=sample["text"],
            auto_advance=auto_advance,
            uses_asset_nodes=uses_asset_nodes,
            review_deferred=review_deferred,
        )
        async with pg_manager.get_async_session_context() as db:
            project = await ExpertRepository(db).lock_project(project_id)
            if (
                project is None
                or project.method_version != method_version
                or project.stage != ORIENTATION_STAGE
                or project.status != "running"
            ):
                raise ValueError("当前项目方法版本已经失效")
            retained = [
                item
                for item in list(project.method_samples or [])
                if not isinstance(item, dict) or item.get("material_id") != material.material_id
            ]
            project.method_samples = [*retained, result]
            project.updated_at = utc_now_naive()
            await db.flush()
        return result

    # 样本之间没有上下游依赖；每份内部仍保持笔记、Review 和定点修订的顺序。
    # 小样是给用户校验方法方向的预览，不是质量关卡：单份失败不让整批任务作废，
    # 成功的样本保留、失败样本带原因进入待确认，由用户决定调整方法或重跑。
    sample_outcomes = await asyncio.gather(
        *(run_sample(material, sample) for material, sample in sample_pairs),
        return_exceptions=True,
    )
    fatal_failures = [
        outcome
        for outcome in sample_outcomes
        if isinstance(outcome, Exception) and not isinstance(outcome, ValueError)
    ]
    if fatal_failures:
        raise fatal_failures[0]
    sample_results = [outcome for outcome in sample_outcomes if isinstance(outcome, dict)]
    degraded = [
        {"material_alias": sample["alias"], "error": str(outcome)}
        for (material, sample), outcome in zip(sample_pairs, sample_outcomes, strict=True)
        if isinstance(outcome, ValueError)
    ]

    needs_attention = [item for item in sample_results if item["status"] == "needs_attention"]
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.lock_project(project_id)
        if (
            project is None
            or project.method_version != method_version
            or project.stage != ORIENTATION_STAGE
            or project.status != "running"
        ):
            return {"project_id": project_id, "ignored": True}
        project.method_samples = sample_results
        project.status = "needs_attention" if needs_attention and not degraded else "awaiting_confirmation"
        ready_message = (
            f"小样初稿已就绪（{len(degraded)} 份降级，可在放行后由校准重跑），确认方向后自动校准并批量学习"
            if degraded
            else "小样初稿已就绪，请确认方向；放行后自动校准并批量学习"
        )
        project.message = (
            "笔记小样仍有明确问题，请调整学习方法后重新试跑"
            if needs_attention and not degraded
            else ready_message
        )
        project.error_message = needs_attention[0]["reason"] if needs_attention and not degraded else None
        project.updated_at = utc_now_naive()
        await db.flush()
    return {
        "project_id": project_id,
        "method_version": method_version,
        "status": "needs_attention" if needs_attention and not degraded else "awaiting_confirmation",
    }


async def confirm_project_method(
    db: AsyncSession,
    *,
    project_id: str,
    created_by: str,
    auto_advance: bool = False,
) -> int:
    """确认当前方法：首次确认触发校准，校准版本再次确认后进入批量学习。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.created_by != created_by:
        raise ValueError("专家项目不存在")
    if project.stage != ORIENTATION_STAGE or project.status != "awaiting_confirmation":
        raise ValueError("当前项目没有等待确认的学习方法")
    _validate_method_contract(
        project.learning_plan or "",
        project.compilation_brief or "",
        ProjectPromptPack.model_validate(project.prompt_pack),
    )
    if not project.method_samples:
        raise ValueError("项目方法缺少真实材料小样")
    _validate_method_samples_ready(project.method_samples)
    if project.method_calibration_completed_at is not None:
        project.method_confirmed_at = utc_now_naive()
        await _start_material_learning(db, project, repo)
        return project.method_version
    if project.method_confirmed_at is not None:
        raise ValueError("方法校准正在完成，不能再次确认")
    project.auto_advance = auto_advance
    sample_ids = list(project.method_sample_material_ids or [])
    samples_have_review = any(
        isinstance(review, dict) and review.get("issues")
        for sample in project.method_samples or []
        if isinstance(sample, dict)
        for review in (sample.get("review"), sample.get("final_review"))
    )
    if samples_have_review and not _method_calibration_issues(project.method_samples):
        # 小样已带完整 Review 且没有可执行问题：无需校准，直接进入批量学习。
        project.method_confirmed_at = utc_now_naive()
        await _start_material_learning(db, project, repo)
        return project.method_version

    project.method_version += 1
    project.method_confirmed_at = utc_now_naive()
    project.method_calibration_completed_at = None
    project.method_calibration_baseline = _build_method_calibration_samples(project.method_samples, sample_ids)
    project.method_calibration_rounds = 0
    project.status = "running"
    project.message = "学习方向已确认，总编模型正在依据小样 Review 收敛方法并重新试跑"
    project.error_message = None
    project.updated_at = utc_now_naive()
    await db.commit()
    return project.method_version


async def repair_project_method(
    db: AsyncSession,
    *,
    project_id: str,
    created_by: str,
) -> int:
    """依据既有小样 Review 创建一个校准版本，不重新执行项目定向；是校准死锁态的人工出口。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.created_by != created_by:
        raise ValueError("专家项目不存在")
    if project.stage != ORIENTATION_STAGE or project.status != "needs_attention":
        raise ValueError("当前项目没有等待修复的学习方法")
    if project.method_calibration_baseline and project.method_calibration_completed_at is None:
        # 基线已建、本轮尚未完成：这是在途校准，应走恢复而不是另起一轮。
        raise ValueError("当前项目方法校准已经开始，请恢复当前校准任务")
    if not str(project.orientation_report or "").strip():
        raise ValueError("项目方法缺少定向报告")

    _validate_method_contract(
        project.learning_plan or "",
        project.compilation_brief or "",
        ProjectPromptPack.model_validate(project.prompt_pack),
    )
    sample_ids = list(project.method_sample_material_ids or [])
    if not sample_ids:
        raise ValueError("项目方法缺少固定的小样材料清单")
    samples = list(project.method_samples or [])
    baseline = _build_method_calibration_samples(samples, sample_ids)

    project.method_version += 1
    project.method_confirmed_at = None
    project.method_calibration_completed_at = None
    project.method_calibration_baseline = baseline
    project.method_calibration_rounds = 0
    project.status = "running"
    project.message = "正在依据小样 Review 调整学习方法并重新试跑"
    project.error_message = None
    project.updated_at = utc_now_naive()
    await db.commit()
    return project.method_version


async def update_expert_project_model(
    db: AsyncSession,
    *,
    project_id: str,
    created_by: str,
    model_spec: str | None = None,
    max_concurrency: int | None = None,
) -> Any:
    """项目运行中切换学习模型或调整材料学习并发；写库即生效，之后新任务一律读最新配置。"""

    updates: list[str] = []
    if model_spec is not None:
        clean_spec = str(model_spec or "").strip()
        model_info = model_cache.get_model_info(clean_spec)
        if model_info is None or model_info.model_type != "chat":
            raise ValueError(f"专家项目模型不可用: {clean_spec}")
        if not int(model_info.context_length or 0):
            raise ValueError("专家项目模型缺少上下文长度配置，请前往「智能体 > 模型供应商 > 管理模型 > 配置」补充")

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.created_by != created_by:
        raise PermissionError("专家项目不存在")
    if project.status in {"deleted", "enabled", "paused"}:
        raise ValueError("项目已删除、已暂停或 Skill 已启用，不能修改模型")
    if project.stage == "release" and project.status in {"ready", "enabled"}:
        raise ValueError("项目已完成编译，不能修改模型")

    if model_spec is not None:
        clean_spec = str(model_spec or "").strip()
        if clean_spec != project.model_spec:
            project.model_spec = clean_spec
            updates.append(f"模型已切换为 {clean_spec}")
    if max_concurrency is not None:
        limit = int(max_concurrency)
        if not 0 <= limit <= EXPERT_MAX_MATERIAL_CONCURRENCY:
            raise ValueError(f"并发数量需在 0～{EXPERT_MAX_MATERIAL_CONCURRENCY} 之间（0 表示不限制）")
        if limit != int(project.max_concurrency or 0):
            project.max_concurrency = limit
            updates.append(
                f"并发数量调整为 {limit if limit else '不限制'}，已在执行中的材料会自然跑完后按新值继续"
            )
    if not updates:
        return project
    project.updated_at = utc_now_naive()
    await _append_activity(
        project.project_id,
        {
            "stage": project.stage,
            "current_step": "model_switch",
            "message": "；".join(updates) + "，从下一批任务起生效",
        },
    )
    await db.commit()
    return project


async def _start_material_learning(db: AsyncSession, project: Any, repo: ExpertRepository) -> int:
    """在已完成方法校准后初始化逐份材料学习。"""

    materials = await repo.list_project_materials(project.project_id)
    included = [material for material in materials if material.status != "excluded"]
    if not included:
        raise ValueError("项目没有可进入学习阶段的材料")
    for material in included:
        material.status = "pending"
        material.method_version = 0
        material.note = None
        material.note_contract = None
        material.review = None
        material.revision_count = 0
        material.error_message = None

    project.stage = MATERIAL_LEARNING_STAGE
    project.status = "running"
    project.message = _material_learning_progress(materials=included, done=0)
    project.error_message = None
    project.updated_at = utc_now_naive()
    await _append_activity(
        project.project_id,
        {
            "stage": MATERIAL_LEARNING_STAGE,
            "current_step": "stage_start",
            "message": f"开始逐份学习 {len(included)} 份材料",
        },
    )
    await db.commit()
    return project.method_version


def _material_learning_progress(*, materials: list[Any], done: int) -> str:
    """材料学习进度的统一措辞：总数与已完成数一眼可读。"""

    return f"正在学习 {len(materials)} 份材料（已完成 {done}）"


async def _refresh_learning_progress(repo: ExpertRepository, project: Any) -> None:
    """按材料事实刷新 expert_projects.message 的学习进度措辞。"""

    if project.stage != MATERIAL_LEARNING_STAGE:
        return
    materials = await repo.list_project_materials(project.project_id)
    active = [item for item in materials if item.status != "excluded"]
    done = [item for item in active if item.status in {"passed", "no_value", "needs_attention"}]
    project.message = _material_learning_progress(materials=active, done=len(done))


async def _append_activity(project_id: str, *events: dict[str, Any]) -> None:
    """把一批进展流水写入项目；失败只告警，不阻塞主流程。"""

    if not events:
        return
    try:
        async with pg_manager.get_async_session_context() as db:
            await ExpertRepository(db).append_activities(project_id, [dict(event) for event in events])
    except Exception as exc:  # noqa: BLE001 - 流水是旁路观测，任何失败都不能影响业务事实
        logger.warning(f"写入专家项目进展流水失败 project={project_id}: {exc!r}")


def _drain_activity_events(tasks: ExpertModelTasks) -> list[dict[str, Any]]:
    """读走调用层积累的流水事件并清空，避免重复落库。"""

    events = tasks.activity_events
    tasks.activity_events = []
    return [
        {
            "stage": event.stage,
            "status": event.status,
            "current_step": event.current_step,
            "material_id": event.material_id,
            "message": event.message,
        }
        for event in events
    ]


async def _drain_material_activity(
    project_id: str,
    material_id: str,
    tasks: ExpertModelTasks,
    *,
    extra: list[dict[str, Any]] | None = None,
) -> None:
    """落库一份材料任务积累的流水事件，材料 ID 由当前任务上下文提供。"""

    events = _drain_activity_events(tasks)
    for event in events:
        event.setdefault("material_id", material_id)
    await _append_activity(project_id, *(events + list(extra or [])))


async def trim_expert_project_activities(project_id: str) -> None:
    """项目进入终态时一次性截断流水到保留上限。"""

    try:
        async with pg_manager.get_async_session_context() as db:
            await ExpertRepository(db).trim_activities(project_id, keep=EXPERT_ACTIVITY_RETAIN_LIMIT)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"截断专家项目进展流水失败 project={project_id}: {exc!r}")


def _is_terminal_project_state(project: Any) -> bool:
    """编译完成或停机终态：enabled/failed/paused 与 release 阶段的 ready/enabled。"""

    if project.status in {"enabled", "failed", "paused"}:
        return True
    return project.stage == "release" and project.status in {"ready", "enabled"}


async def run_method_calibration(
    project_id: str,
    method_version: int,
    *,
    model_tasks: ExpertModelTasks | None = None,
) -> dict[str, Any]:
    """由总编收敛方法并复跑小样；自动模式下失败轮有界续跑，等待确认或交接。"""

    tasks = model_tasks or ExpertModelTasks()
    bind_activity = getattr(tasks, "bind_activity_context", None)
    if bind_activity is not None:
        bind_activity(stage=ORIENTATION_STAGE)
    _emit_activity(tasks, "calibration_start", "方法校准任务开始")
    try:
        return await _run_method_calibration(project_id, method_version, tasks)
    except Exception:
        _emit_activity(
            tasks,
            "calibration_failed",
            "方法校准任务失败，将按重试策略续跑",
            status="warning",
        )
        raise
    finally:
        if getattr(tasks, "activity_events", None):
            await _append_activity(project_id, *_drain_activity_events(tasks))


async def _run_method_calibration(
    project_id: str,
    method_version: int,
    tasks: ExpertModelTasks,
) -> dict[str, Any]:
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None:
            raise ValueError("专家项目不存在")
        if (
            project.stage == MATERIAL_LEARNING_STAGE
            and project.status == "running"
            and project.method_version == method_version
            and project.auto_advance
        ):
            return {"project_id": project_id, "method_version": method_version, "status": "running"}
        if (
            project.stage != ORIENTATION_STAGE
            or project.status not in {"running", "awaiting_confirmation"}
            or project.method_version != method_version
            or not project.method_calibration_baseline
        ):
            return {"project_id": project_id, "ignored": True}
        sample_ids = list(project.method_sample_material_ids or [])
        needs_method_calibration = project.method_calibration_completed_at is None
        v2_samples_ready = not needs_method_calibration and project.status == "awaiting_confirmation"
        materials = await repo.list_project_materials(project_id)
        materials_by_id = {material.material_id: material for material in materials}
        pending_review_samples = []
        if needs_method_calibration:
            # 首轮校准前需要补 Review 的 V1 预览小样：Author 已落库且 decision 为 learned。
            pending_review_samples = [
                item
                for item in project.method_samples or []
                if isinstance(item, dict)
                and item.get("review") is None
                and isinstance(item.get("note"), dict)
                and item["note"].get("decision") == "learned"
            ]
        snapshot = {
            "name": project.name,
            "model_spec": project.model_spec,
            "compiler_profile_id": project.compiler_profile_id,
            "learning_plan": project.learning_plan or "",
            "compilation_brief": project.compilation_brief or "",
            "prompt_pack": ProjectPromptPack.model_validate(project.prompt_pack),
            "auto_advance": bool(project.auto_advance),
            "method_samples": list(project.method_samples or []),
        }

    if needs_method_calibration:
        if pending_review_samples:
            # 放行后的第一轮校准：先对 V1 预览小样补独立 Review（不重写 Author），
            # 让总编基于带 Review 的基线收敛方法，而不是对无意见初稿盲调。
            backfilled: dict[str, dict[str, Any]] = {}
            for item in pending_review_samples:
                material = materials_by_id.get(item.get("material_id"))
                if material is None:
                    raise ValueError("代表性材料正文不存在")
                backfilled[item["material_id"]] = await _run_method_sample(
                    tasks=tasks,
                    model_spec=snapshot["model_spec"],
                    learning_plan=snapshot["learning_plan"],
                    prompt_pack=snapshot["prompt_pack"],
                    material=material,
                    alias=item["material_alias"],
                    text=await read_expert_material_text(material),
                    starting_note=MaterialNoteResult.model_validate(item["note"]),
                    auto_advance=snapshot["auto_advance"],
                    uses_asset_nodes=_project_uses_asset_nodes(snapshot["compiler_profile_id"]),
                )
            async with pg_manager.get_async_session_context() as db:
                project = await ExpertRepository(db).lock_project(project_id)
                if (
                    project is None
                    or project.stage != ORIENTATION_STAGE
                    or project.status != "running"
                    or project.method_version != method_version
                ):
                    return {"project_id": project_id, "ignored": True}
                project.method_samples = [
                    backfilled.get(item.get("material_id"), item)
                    for item in list(project.method_samples or [])
                ]
                samples = _build_method_calibration_samples(project.method_samples, sample_ids)
                project.method_calibration_baseline = samples
                project.updated_at = utc_now_naive()
                await db.flush()
        else:
            samples = _build_method_calibration_samples(snapshot["method_samples"], sample_ids)
        _emit_activity(tasks, "calibration", "总编正在依据小样 Review 收敛学习方法")
        calibrated = await tasks.calibrate_project_method(
            model_spec=snapshot["model_spec"],
            project_name=snapshot["name"],
            learning_plan=snapshot["learning_plan"],
            compilation_brief=snapshot["compilation_brief"],
            prompt_pack=snapshot["prompt_pack"],
            samples=samples,
        )
        _validate_method_contract(
            calibrated.learning_plan_markdown,
            calibrated.compilation_brief_markdown,
            calibrated.prompt_pack,
        )

        async with pg_manager.get_async_session_context() as db:
            project = await ExpertRepository(db).lock_project(project_id)
            if (
                project is None
                or project.stage != ORIENTATION_STAGE
                or project.status != "running"
                or project.method_version != method_version
            ):
                return {"project_id": project_id, "ignored": True}
            project.learning_plan = calibrated.learning_plan_markdown
            project.compilation_brief = calibrated.compilation_brief_markdown
            project.prompt_pack = calibrated.prompt_pack.model_dump()
            project.method_samples = []
            project.method_calibration_completed_at = utc_now_naive()
            project.message = "已生成校准后的方法，正在使用同一批小样重新试跑"
            project.updated_at = utc_now_naive()
            await db.flush()
        _emit_activity(tasks, "calibration", "校准后的学习方法已生成，开始用同一批小样重新试跑")

    if not v2_samples_ready:
        result = await run_project_orientation(
            project_id,
            method_version,
            model_tasks=tasks,
            auto_advance=snapshot["auto_advance"],
            review_deferred=False,
        )
        if result.get("status") != "awaiting_confirmation":
            # 小样复跑仍失败：自动模式开启下一轮或宣告预算耗尽；手动模式保持原有停机结果。
            next_method_version = await _settle_failed_auto_calibration(
                project_id,
                method_version,
                failure_reason=None,
            )
            if next_method_version is not None:
                return {
                    "project_id": project_id,
                    "method_version": method_version,
                    "next_method_version": next_method_version,
                    "status": "running",
                }
            return result

    async with pg_manager.get_async_session_context() as db:
        project = await ExpertRepository(db).get_project(project_id)
        if (
            project is None
            or project.stage != ORIENTATION_STAGE
            or project.status != "awaiting_confirmation"
            or project.method_version != method_version
            or not project.method_calibration_baseline
        ):
            return {"project_id": project_id, "ignored": True}
        _validate_method_samples_ready(project.method_samples)
        assessment_input = {
            "model_spec": project.model_spec,
            "v1_samples": list(project.method_calibration_baseline),
            "v2_samples": _build_method_calibration_samples(
                project.method_samples,
                list(project.method_sample_material_ids or []),
            ),
        }

    assessment = await tasks.assess_method_calibration(**assessment_input)
    _emit_activity(
        tasks,
        "assess",
        f"校准评估：{assessment.decision}——{assessment.reason}",
        status="info" if assessment.decision != "needs_attention" else "warning",
    )
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.lock_project(project_id)
        if (
            project is None
            or project.stage != ORIENTATION_STAGE
            or project.status != "awaiting_confirmation"
            or project.method_version != method_version
        ):
            return {"project_id": project_id, "ignored": True}
        project.method_calibration_baseline = []
        if assessment.decision == "needs_attention":
            if project.auto_advance:
                next_method_version = _advance_auto_calibration_round(
                    project,
                    failure_reason=assessment.reason,
                )
                await db.commit()
                if next_method_version is not None:
                    return {
                        "project_id": project_id,
                        "method_version": method_version,
                        "next_method_version": next_method_version,
                        "status": "running",
                    }
                return {
                    "project_id": project_id,
                    "method_version": method_version,
                    "status": "needs_attention",
                }
            project.method_confirmed_at = None
            project.status = "needs_attention"
            project.message = "校准后仍存在会影响批量学习的方法问题"
            project.error_message = assessment.reason
            project.updated_at = utc_now_naive()
            await db.commit()
            return {"project_id": project_id, "method_version": method_version, "status": "needs_attention"}
        # 方向已由用户在初稿阶段确认，校准（压 Review 问题）是机器自己的质量圈：
        # 通过即直接进入批量学习，不再要求二次确认（auto_advance 与否相同处理）。
        project.method_confirmed_at = utc_now_naive()
        await _start_material_learning(db, project, repo)
        return {"project_id": project_id, "method_version": method_version, "status": "running"}


async def _settle_failed_auto_calibration(
    project_id: str,
    method_version: int,
    *,
    failure_reason: str | None,
) -> int | None:
    """在锁内结算一次失败的自动校准轮：开启下一轮返回新版本号，预算耗尽落需要处理。

    只处理仍处于本轮失败现场（同版本、定向阶段、需要处理）的自动推进项目；
    项目已被人工移动、暂停或版本已推进时返回 None，不触碰任何事实。
    """

    async with pg_manager.get_async_session_context() as db:
        project = await ExpertRepository(db).lock_project(project_id)
        if (
            project is None
            or project.stage != ORIENTATION_STAGE
            or project.status != "needs_attention"
            or project.method_version != method_version
            or not bool(project.auto_advance)
        ):
            return None
        next_method_version = _advance_auto_calibration_round(
            project,
            failure_reason=failure_reason or project.error_message,
        )
        await db.commit()
        return next_method_version


def _advance_auto_calibration_round(project: Any, *, failure_reason: str) -> int | None:
    """自动推进项目专用：单轮失败后有界续跑。返回下一轮方法版本；预算耗尽返回 None 并停机。

    轮数语义：method_calibration_rounds 记录本确认代际内已失败的校准执行次数，
    第 N 次失败时 used=N；used 未达上限则把本轮小样连同 Review 作为下一轮基线继续收敛。
    """

    used_rounds = int(project.method_calibration_rounds or 0) + 1
    round_report = f"已用 {used_rounds}/{MAX_METHOD_CALIBRATION_ROUNDS} 轮"
    project.method_calibration_rounds = used_rounds
    if used_rounds >= MAX_METHOD_CALIBRATION_ROUNDS:
        project.status = "needs_attention"
        project.message = "自动方法校准仍未收敛，需要人工修复或重新定向"
        project.error_message = f"{str(failure_reason).strip()}（自动校准{round_report}）"
        project.updated_at = utc_now_naive()
        return None
    project.method_version += 1
    project.method_calibration_completed_at = None
    project.method_calibration_baseline = _build_method_calibration_samples(
        list(project.method_samples or []),
        list(project.method_sample_material_ids or []),
    )
    project.status = "running"
    project.message = (
        f"自动校准第 {used_rounds} 轮未收敛（{round_report}），"
        f"总编将依据最新小样 Review 继续第 {used_rounds + 1} 轮"
    )
    project.error_message = None
    project.updated_at = utc_now_naive()
    return project.method_version


async def run_material_learning_stage(
    project_id: str,
    material_id: str,
    method_version: int,
    *,
    model_tasks: ExpertModelTasks | None = None,
) -> dict[str, Any]:
    """在一个独立 Worker 工作中完成一份材料的笔记与 Review。"""

    tasks = model_tasks or ExpertModelTasks(prompt_cache_namespace=f"expert:{project_id}:method:{method_version}")
    # 测试桩可能不是 ExpertModelTasks 子类：无流水能力时静默跳过。
    bind_activity = getattr(tasks, "bind_activity_context", None)
    if bind_activity is not None:
        bind_activity(stage=MATERIAL_LEARNING_STAGE, material_id=material_id)
    await _learn_one_material(project_id, material_id, method_version, tasks)
    if bind_activity is not None:
        await _drain_material_activity(
            project_id,
            material_id,
            tasks,
            extra=[
                {"stage": MATERIAL_LEARNING_STAGE, "current_step": "material_done", "message": "材料学习任务结束"}
            ],
        )
    async with pg_manager.get_async_session_context() as db:
        status = await _finish_material_learning(db, project_id, method_version)
    return {
        "project_id": project_id,
        "material_id": material_id,
        "method_version": method_version,
        "status": status,
    }


async def mark_material_learning_attention(
    project_id: str,
    material_id: str,
    method_version: int,
    error: str,
) -> None:
    """把单材料工作的确定性失败保存为需要处理。"""

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.lock_project(project_id)
        material = await repo.lock_material(material_id)
        if (
            project is None
            or material is None
            or material.project_id != project_id
            or project.stage != MATERIAL_LEARNING_STAGE
            or project.method_version != method_version
            or project.status == "paused"
            or material.status not in {"pending", "learning", "review_revise"}
        ):
            return
        material.status = "needs_attention"
        material.error_message = error
        await _finish_material_learning(db, project_id, method_version)
    await _append_activity(
        project_id,
        {
            "stage": MATERIAL_LEARNING_STAGE,
            "status": "warning",
            "current_step": "material_failed",
            "material_id": material_id,
            "message": f"材料学习失败：{error}",
        },
    )


async def retry_material_learning(
    db: AsyncSession,
    *,
    project_id: str,
    material_id: str,
    created_by: str,
) -> int:
    """仅重跑一份在学习或 Review 中进入需要处理的材料。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    material = await repo.lock_material(material_id)
    if project is None or project.created_by != created_by or material is None or material.project_id != project_id:
        raise ValueError("专家材料不存在")
    if project.stage != MATERIAL_LEARNING_STAGE or material.status != "needs_attention":
        raise ValueError("当前材料不能重跑学习")
    material.status = "pending"
    material.error_message = None
    project.status = "running"
    project.message = f"正在重新学习《{material.title}》"
    project.error_message = None
    await db.commit()
    return project.method_version


async def retry_material_preparation(
    db: AsyncSession,
    *,
    project_id: str,
    material_id: str,
    created_by: str,
) -> None:
    """让用户修复配置后只重跑一份正文转换失败的材料。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    material = await repo.lock_material(material_id)
    if project is None or project.created_by != created_by or material is None or material.project_id != project_id:
        raise ValueError("专家材料不存在")
    if project.stage != ORIENTATION_STAGE or material.status != "needs_attention":
        raise ValueError("当前材料不能重跑正文转换")
    material.status = "pending"
    material.error_message = None
    project.status = "preparing"
    project.message = f"正在重新转换《{material.title}》"
    project.error_message = None
    await db.commit()


async def exclude_material(
    db: AsyncSession,
    *,
    project_id: str,
    material_id: str,
    created_by: str,
    reason: str,
) -> bool:
    """记录用户明确排除一份需要处理的材料。"""

    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ValueError("请说明排除材料的原因")
    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    material = await repo.lock_material(material_id)
    if project is None or project.created_by != created_by or material is None or material.project_id != project_id:
        raise ValueError("专家材料不存在")
    if material.status != "needs_attention":
        raise ValueError("只有需要处理的材料可以被明确排除")

    materials = await repo.list_project_materials(project_id)
    has_remaining_material = any(
        item.material_id != material_id and item.status not in {"excluded", "no_value"} for item in materials
    )
    if not has_remaining_material:
        raise ValueError("专家项目至少需要保留一份可继续学习的材料")

    material.status = "excluded"
    material.exclusion_reason = clean_reason
    material.error_message = None
    await db.flush()
    if project.stage == ORIENTATION_STAGE:
        await _finish_material_preparation(db, project_id)
    elif project.stage == MATERIAL_LEARNING_STAGE:
        await _finish_material_learning(db, project_id, project.method_version)
    should_start_organization = project.stage == "organization" and project.status == "preparing"
    await db.commit()
    return should_start_organization


AUTO_EXCLUDED_MATERIAL_REASON = "运行失败自动排除"


async def exclude_failed_material_for_auto_advance(
    project_id: str,
    material_id: str,
    method_version: int,
) -> bool:
    """无人值守模式专用：传输故障烧尽重投预算后按固定原因排除失败材料并推进交接。

    与用户显式排除共用同一套事实语义（status=excluded + exclusion_reason），
    但不要求至少保留一份材料——全部失败时由收尾逻辑停在「没有材料通过 Review」。
    返回 True 表示项目已进入组织阶段等待投递；手动推进或现场已变时返回 False 不触碰任何事实。
    """

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.lock_project(project_id)
        material = await repo.lock_material(material_id)
        if (
            project is None
            or material is None
            or material.project_id != project_id
            or project.stage != MATERIAL_LEARNING_STAGE
            or project.method_version != method_version
            or project.status != "needs_attention"
            or not bool(project.auto_advance)
            or material.status != "needs_attention"
        ):
            return False
        material.status = "excluded"
        material.exclusion_reason = AUTO_EXCLUDED_MATERIAL_REASON
        material.error_message = None
        await _finish_material_learning(db, project_id, method_version)
        should_start_organization = project.stage == "organization" and project.status == "preparing"
        await db.commit()
        return should_start_organization


async def mark_project_attention(project_id: str, error: str) -> None:
    """在有限系统重试耗尽后保存可行动错误。"""

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None:
            return
        if project.status == "paused":
            return
        await repo.update_project(
            project_id,
            status="needs_attention",
            message="当前阶段执行失败，请检查原因后重跑",
            error_message=str(error),
        )


async def _prepare_one_material(project_id: str, material_id: str) -> None:
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        material = await repo.lock_material(material_id)
        if (
            project is None
            or project.status != "preparing"
            or material is None
            or material.project_id != project_id
            or material.status not in {"pending", "converting"}
        ):
            return
        material.status = "converting"
        material.error_message = None
        source = StoredMaterialSource(
            material_id=material.material_id,
            title=material.title,
            source_path=material.source_path,
            raw_path=material.raw_path,
            file_type=material.file_type,
        )
        await db.commit()

    try:
        parsed = await parse_material(project_id, source)
    except Exception as exc:  # noqa: BLE001
        async with pg_manager.get_async_session_context() as db:
            await ExpertRepository(db).update_material(
                material_id,
                status="needs_attention",
                error_message=str(exc),
            )
        return

    async with pg_manager.get_async_session_context() as db:
        await ExpertRepository(db).update_material(
            material_id,
            status="ready",
            text_path=parsed.text_path,
            line_count=parsed.line_count,
            token_count=parsed.token_count,
            error_message=None,
        )


async def _learn_one_material(
    project_id: str,
    material_id: str,
    method_version: int,
    tasks: ExpertModelTasks,
) -> None:
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        material = await repo.lock_material(material_id)
        if (
            project is None
            or material is None
            or material.project_id != project_id
            or project.stage != MATERIAL_LEARNING_STAGE
            or project.status != "running"
            or project.method_version != method_version
            or material.status not in {"pending", "learning", "review_revise"}
        ):
            return
        resume_note_contract = material.note_contract if material.status in {"learning", "review_revise"} else None
        resume_review = material.review if resume_note_contract else None
        resume_revision_count = material.revision_count if resume_note_contract else 0
        material.status = "learning"
        material.error_message = None
        snapshot = {
            "model_spec": project.model_spec,
            "auto_advance": bool(project.auto_advance),
            "learning_plan": project.learning_plan or "",
            "prompt_pack": ProjectPromptPack.model_validate(project.prompt_pack),
            "title": material.title,
            "source_path": material.source_path,
            "line_count": material.line_count,
            "material": SimpleNamespace(
                project_id=material.project_id,
                material_unit_id=material.material_unit_id,
                source_file_id=material.source_file_id,
                source_start_line=material.source_start_line,
                source_end_line=material.source_end_line,
                text_path=material.text_path,
                line_count=material.line_count,
            ),
            "resume_note_contract": resume_note_contract,
            "resume_review": resume_review,
            "resume_revision_count": resume_revision_count,
        }
        await db.commit()

    text = await read_expert_material_text(snapshot["material"])
    material_view = SimpleNamespace(
        material_id=material_id,
        title=snapshot["title"],
        source_path=snapshot["source_path"],
        line_count=snapshot["line_count"],
    )
    result = await _run_method_sample(
        tasks=tasks,
        model_spec=snapshot["model_spec"],
        learning_plan=snapshot["learning_plan"],
        prompt_pack=snapshot["prompt_pack"],
        material=material_view,
        alias="CURRENT_MATERIAL",
        text=text,
        starting_note=(
            MaterialNoteResult.model_validate(snapshot["resume_note_contract"])
            if snapshot["resume_note_contract"]
            else None
        ),
        starting_review=(
            MaterialReviewResult.model_validate(snapshot["resume_review"]) if snapshot["resume_review"] else None
        ),
        starting_revision_count=snapshot["resume_revision_count"],
        auto_advance=snapshot["auto_advance"],
        uses_asset_nodes=_project_uses_asset_nodes(project.compiler_profile_id),
    )

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        material = await repo.lock_material(material_id)
        if (
            project is None
            or material is None
            or project.status != "running"
            or project.method_version != method_version
            or material.status != "learning"
        ):
            return
        final_note = result["revision"] or result["note"]
        material.status = result["status"]
        material.method_version = method_version
        material.note = final_note.get("note_markdown") or None
        material.note_contract = final_note
        material.review = result["final_review"]
        material.revision_count = result["revision_count"]
        material.error_message = result["reason"] or None


async def _finish_material_learning(db: AsyncSession, project_id: str, method_version: int) -> str:
    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.stage != MATERIAL_LEARNING_STAGE or project.method_version != method_version:
        return "ignored"
    if project.status == "paused":
        return "paused"
    materials = await repo.list_project_materials(project_id)
    unfinished = [item for item in materials if item.status in {"pending", "learning", "review_revise"}]
    attention = [item for item in materials if item.status == "needs_attention"]
    if unfinished:
        project.status = "running"
        await _refresh_learning_progress(repo, project)
        return project.status
    if attention:
        project.status = "needs_attention"
        await _refresh_learning_progress(repo, project)
        project.error_message = attention[0].error_message
        return project.status

    passed = [item for item in materials if item.status == "passed"]
    if not passed:
        project.status = "needs_attention"
        project.message = "材料处理已完成，但没有材料通过 Review"
        project.error_message = "全部材料均被模型判定不采用，无法形成可信专家 Skill"
        project.updated_at = utc_now_naive()
        return project.status

    project.stage = "organization"
    project.status = "preparing"
    project.message = "材料学习已完成，正在准备主题方案和讲义小样"
    project.error_message = None
    project.updated_at = utc_now_naive()
    return project.status


async def _finish_material_preparation(db: AsyncSession, project_id: str) -> None:
    repo = ExpertRepository(db)
    project = await repo.get_project(project_id)
    if project is None or project.stage != ORIENTATION_STAGE:
        return
    if project.status == "paused":
        return
    materials = await repo.list_project_materials(project_id)
    unresolved = [item for item in materials if item.status == "needs_attention"]
    ready = [item for item in materials if item.status == "ready"]
    if unresolved:
        project.status = "needs_attention"
        project.message = f"有 {len(unresolved)} 份材料需要处理"
        project.error_message = unresolved[0].error_message
    elif ready:
        project.status = "ready"
        project.message = "材料正文已准备好，请选择 1～20 份代表样本"
        project.error_message = None
    else:
        project.status = "needs_attention"
        project.message = "项目没有可用于学习的材料"
        project.error_message = "请重新导入至少一份能够转换为可靠正文的材料"
    project.updated_at = utc_now_naive()
    await db.flush()


async def _run_method_sample(
    *,
    tasks: ExpertModelTasks,
    model_spec: str,
    learning_plan: str,
    prompt_pack: ProjectPromptPack,
    material: Any,
    alias: str,
    text: str,
    starting_note: MaterialNoteResult | None = None,
    starting_review: MaterialReviewResult | None = None,
    starting_revision_count: int = 0,
    auto_advance: bool = False,
    uses_asset_nodes: bool = False,
    review_deferred: bool = False,
) -> dict[str, Any]:
    def _structure_gate(candidate: MaterialNoteResult) -> MaterialReviewResult | None:
        """规范化并校验资产结构；不合格时返回合成 revise 意见供修订循环消费。"""

        review_holder: list[MaterialReviewResult] = []
        _apply_asset_note_structure_gate(
            candidate,
            uses_asset_nodes=uses_asset_nodes,
            alias=alias,
            auto_advance=auto_advance,
            synthesized_review_out=review_holder,
        )
        return review_holder[0] if review_holder else None

    if starting_note is None and starting_review is not None:
        raise ValueError("材料恢复缺少笔记或 Review")
    if starting_note is None:
        note = await tasks.write_material_note(
            model_spec=model_spec,
            learning_plan=learning_plan,
            stage_prompt=prompt_pack.note_author,
            material_alias=alias,
            title=material.title,
            source=material.source_path,
            text=text,
        )
        _validate_note(note, alias=alias, line_count=material.line_count)
        if note.decision == "needs_attention":
            review = None
        elif review_deferred:
            # 定向预览模式：小样初稿直接给用户校验方向，结构门先规范化，
            # Review 与修订留给用户放行后的方法校准阶段执行。
            review = _structure_gate(note)
            return {
                "material_id": material.material_id,
                "material_alias": alias,
                "title": material.title,
                "source": material.source_path,
                "status": "passed" if note.decision == "learned" else "needs_attention",
                "note": note.model_dump(),
                "review": None,
                "revision": None,
                "revision_count": 0,
                "final_review": None,
                "reason": note.reason,
            }
        else:
            review = await tasks.review_material_note(
                model_spec=model_spec,
                learning_plan=learning_plan,
                stage_prompt=prompt_pack.note_reviewer,
                material_alias=alias,
                title=material.title,
                source=material.source_path,
                text=text,
                note=note,
            )
            _validate_review(review)
            structure_issue = _structure_gate(note)
            if structure_issue is not None and review.decision == "pass":
                review = structure_issue
    else:
        note = starting_note
        review = starting_review
        if review is None:
            # 定向预览小样的 Review 补跑：Author 初稿已落库，这里只补独立 Review 与结构门。
            review = await tasks.review_material_note(
                model_spec=model_spec,
                learning_plan=learning_plan,
                stage_prompt=prompt_pack.note_reviewer,
                material_alias=alias,
                title=material.title,
                source=material.source_path,
                text=text,
                note=note,
            )
            _validate_review(review)
            structure_issue = _structure_gate(note)
            if structure_issue is not None and review.decision == "pass":
                review = structure_issue

    revision = None
    revision_count = starting_revision_count
    current_note = note
    final_review = review
    learned_candidate = note if note.decision == "learned" else None
    while final_review is not None and final_review.decision == "revise" and revision_count < MAX_MATERIAL_REVISIONS:
        revision = await tasks.revise_material_note(
            model_spec=model_spec,
            learning_plan=learning_plan,
            stage_prompt=prompt_pack.note_author,
            material_alias=alias,
            title=material.title,
            source=material.source_path,
            text=text,
            note=current_note,
            review=final_review,
        )
        _validate_note(revision, alias=alias, line_count=material.line_count)
        revision_count += 1
        current_note = revision
        if revision.decision == "needs_attention":
            break
        if revision.decision == "learned":
            learned_candidate = revision
        final_review = await tasks.review_material_note(
            model_spec=model_spec,
            learning_plan=learning_plan,
            stage_prompt=prompt_pack.note_reviewer,
            material_alias=alias,
            title=material.title,
            source=material.source_path,
            text=text,
            note=current_note,
        )
        _validate_review(final_review)
        structure_issue = _structure_gate(revision)
        if structure_issue is not None and final_review.decision == "pass":
            final_review = structure_issue

    needs_content_resolution = current_note.decision == "needs_attention" or (
        final_review is not None and final_review.decision in {"revise", "needs_attention"}
    )
    if auto_advance and needs_content_resolution:
        resolution = await tasks.resolve_material_automatically(
            model_spec=model_spec,
            learning_plan=learning_plan,
            stage_prompt=prompt_pack.note_author,
            material_alias=alias,
            title=material.title,
            source=material.source_path,
            text=text,
            candidate_note=learned_candidate,
            latest_result=current_note,
            review=final_review,
        )
        _validate_note(resolution, alias=alias, line_count=material.line_count)
        revision = resolution
        current_note = resolution
        structure_issue = _structure_gate(resolution)
        final_review = structure_issue if structure_issue is not None else MaterialReviewResult(
            decision="pass" if resolution.decision == "learned" else "no_value",
            summary=resolution.reason,
            issues=[],
        )

    final_note = revision or note
    if current_note.decision == "needs_attention":
        status = "needs_attention"
        reason = current_note.reason
    elif final_review is not None and final_review.decision == "revise":
        status = "needs_attention"
        reason = f"已完成 {MAX_MATERIAL_REVISIONS} 次自动修订，Review 仍要求修订：{final_review.summary}"
    elif final_review is not None and final_review.decision == "needs_attention":
        status = "needs_attention"
        reason = final_review.summary
    elif final_note.decision == "no_value" or (final_review is not None and final_review.decision == "no_value"):
        status = "no_value"
        reason = final_note.reason or (final_review.summary if final_review is not None else "")
    else:
        status = "passed"
        reason = ""
    return {
        "material_id": material.material_id,
        "material_alias": alias,
        "title": material.title,
        "source": material.source_path,
        "status": status,
        "note": note.model_dump(),
        "review": review.model_dump() if review else None,
        "revision": revision.model_dump() if revision else None,
        "revision_count": revision_count,
        "final_review": final_review.model_dump() if final_review else None,
        "reason": reason,
    }


async def repair_organization_asset_notes(
    project_id: str,
    organization_version: int,
    materials: list[Any],
    note_aliases: dict[str, str],
    snapshot: dict[str, Any],
    tasks: ExpertModelTasks,
) -> list[Any]:
    """组织入口的病笔记修复：结构不合格的已通过笔记按学习阶段同一修订模式自动修复。

    结构合同是确定性输出合同（见 _apply_asset_note_structure_gate），修复不分人工/自动模式。
    规范化即可修复的偏差直接回写；仍不合格的材料落 needs_attention 并从本次组织输入移除；
    全部不可用时以明确原因停止组织阶段。
    """

    from yuxi.expert.organization_service import normalize_asset_note_markdown, validate_asset_note_structure

    healthy: list[Any] = []
    for material in materials:
        alias = note_aliases[material.material_id]
        normalized = normalize_asset_note_markdown(material.note or "", material_alias=alias)
        try:
            validate_asset_note_structure(normalized, material_alias=alias)
        except ValueError as exc:
            repaired = await _repair_one_asset_note(
                project_id,
                organization_version,
                material,
                alias,
                structure_error=str(exc),
                snapshot=snapshot,
                tasks=tasks,
            )
            if repaired is None:
                continue
            material = repaired
            normalized = material.note or ""
        if normalized != (material.note or ""):
            async with pg_manager.get_async_session_context() as db:
                await ExpertRepository(db).update_material(material.material_id, note=normalized)
            material.note = normalized
        healthy.append(material)
    if not healthy:
        raise ValueError("全部通过笔记的资产结构修复失败，没有可用于主题组织的笔记")
    return healthy


async def _repair_one_asset_note(
    project_id: str,
    organization_version: int,
    material: Any,
    alias: str,
    *,
    structure_error: str,
    snapshot: dict[str, Any],
    tasks: ExpertModelTasks,
) -> Any:
    """把一份结构不合格笔记交回学习修订循环；返回修复后的材料，不可修复返回 None。"""

    logger.warning(
        f"expert 组织阶段发现结构不合格笔记，开始自动修复 project={project_id} "
        f"material={material.material_id} alias={alias}: {structure_error}"
    )
    bind_activity = getattr(tasks, "bind_activity_context", None)
    if bind_activity is not None:
        bind_activity(stage="organization", material_id=material.material_id)
    material_view = SimpleNamespace(
        material_id=material.material_id,
        title=material.title,
        source_path=material.source_path,
        line_count=material.line_count,
    )
    text = await read_expert_material_text(material)
    result = await _run_method_sample(
        tasks=tasks,
        model_spec=snapshot["model_spec"],
        learning_plan=snapshot["learning_plan"],
        prompt_pack=snapshot["prompt_pack"],
        material=material_view,
        alias=alias,
        text=text,
        starting_note=(
            MaterialNoteResult.model_validate(material.note_contract)
            if material.note_contract
            else None
        ),
        starting_revision_count=int(material.revision_count or 0),
        auto_advance=bool(snapshot.get("auto_advance")),
        uses_asset_nodes=True,
    )

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        current = await repo.get_material(material.material_id)
        if (
            project is None
            or project.status != "running"
            or project.organization_version != organization_version
            or current is None
            or current.status != "passed"
        ):
            return None
        final_note = result["revision"] or result["note"]
        current.status = result["status"]
        current.note = final_note.get("note_markdown") or None
        current.note_contract = final_note
        current.review = result["final_review"]
        current.revision_count = result["revision_count"]
        current.error_message = result["reason"] or None

    if getattr(tasks, "activity_events", None):
        await _drain_material_activity(
            project_id,
            material.material_id,
            tasks,
            extra=[
                {
                    "stage": "organization",
                    "current_step": "note_structure_repair",
                    "status": "info" if result["status"] == "passed" else "warning",
                    "material_id": material.material_id,
                    "message": (
                        f"笔记 {alias} 结构自动修复完成"
                        if result["status"] == "passed"
                        else f"笔记 {alias} 结构修复未成功（{result['status']}），已移出本次组织"
                    ),
                }
            ],
        )
    if result["status"] == "passed":
        return current
    return None


def _validate_method_contract(learning_plan: str, compilation_brief: str, prompt_pack: ProjectPromptPack) -> None:
    """项目方法的最小完整性合同：学习方案与编译底稿非空、Prompt 包各阶段内容齐备。"""

    if not learning_plan.strip() or not compilation_brief.strip():
        raise ValueError("项目方法缺少学习方案或编译底稿")
    missing = [name for name, value in prompt_pack.model_dump().items() if not str(value).strip()]
    if missing:
        raise ValueError(f"项目 Prompt 包缺少阶段内容: {', '.join(missing)}")


def _validate_note(note: MaterialNoteResult, *, alias: str, line_count: int) -> None:
    if note.decision == "learned" and not note.note_markdown.strip():
        raise ValueError("学艺笔记响应缺少笔记正文")
    if note.decision == "learned" and not note.routing_labels:
        raise ValueError("学艺笔记缺少首轮分组 Labels")
    if note.decision == "learned" and not note.evidence:
        raise ValueError("学艺笔记缺少可定位的原文依据")
    if note.decision != "learned" and not note.reason.strip():
        raise ValueError("材料停止结论缺少具体原因")
    for anchor in note.evidence:
        if anchor.material_alias != alias:
            raise ValueError("笔记引用了当前任务以外的材料")
        if anchor.start_line > anchor.end_line or anchor.end_line > line_count:
            raise ValueError("笔记证据行号超出当前材料范围")


def _validate_review(review: MaterialReviewResult) -> None:
    if review.decision == "revise" and not review.issues:
        raise ValueError("Review 要求修订但没有给出具体问题")


def _method_calibration_issues(samples: list[Any]) -> list[dict[str, Any]]:
    """提取小样独立 Review 中可供总编收敛的具体问题。"""

    issues = [
        issue
        for sample in samples or []
        if isinstance(sample, dict)
        for review in (sample.get("review"), sample.get("final_review"))
        if isinstance(review, dict)
        for issue in review.get("issues") or []
        if isinstance(issue, dict) and str(issue.get("problem") or "").strip()
    ]
    return issues


def _validate_method_samples_ready(samples: list[Any]) -> None:
    """确认小样可交接；可修 Review 问题交给后续材料 Repair，no_value 须是自动终裁结论。

    首次定向的预览小样尚无 Review（final_review 为空），属于正常交接状态，
    Review 与修订由用户放行后的方法校准阶段补跑。
    """

    if not samples or any(
        not isinstance(sample, dict)
        or sample.get("status") not in {"passed", "review_revise", "no_value"}
        or (
            (sample.get("final_review") or {}).get("decision") not in {None, "pass", "revise", "no_value"}
        )
        for sample in samples
    ):
        raise ValueError("项目方法小样存在无法自动修复的问题")


def _build_method_calibration_samples(samples: list[Any], sample_ids: list[str]) -> list[dict[str, Any]]:
    """按固定小样清单交给总编最终笔记和独立 Review，不补入其他材料。"""

    samples_by_id = {
        sample.get("material_id"): sample
        for sample in samples or []
        if isinstance(sample, dict) and sample.get("material_id")
    }
    if set(samples_by_id) != set(sample_ids):
        raise ValueError("项目方法小样与固定的小样材料清单不一致")
    result = []
    for material_id in sample_ids:
        sample = samples_by_id[material_id]
        result.append(
            {
                "material_alias": sample.get("material_alias"),
                "note": sample.get("revision") or sample.get("note"),
                "review": sample.get("review"),
                "final_review": sample.get("final_review"),
            }
        )
    return result


def _build_material_catalog(materials: list[Any], aliases: dict[str, str]) -> str:
    lines = []
    for material in materials:
        facts = [
            aliases[material.material_id],
            material.title,
            material.source_path,
            material.file_type,
            f"{material.line_count} 行",
        ]
        if material.token_count:
            facts.append(f"约 {material.token_count} tokens")
        facts.append(material.status)
        lines.append(" | ".join(facts))
    return "\n".join(lines)


def _material_summary(materials: list[Any]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for material in materials:
        statuses[material.status] = statuses.get(material.status, 0) + 1
    return {"total": len(materials), "status_counts": statuses}


def _serialize_material(material: Any) -> dict[str, Any]:
    data = material.to_dict()
    data.pop("raw_path", None)
    data.pop("text_path", None)
    return data


__all__ = [
    "confirm_project_method",
    "create_expert_project",
    "delete_expert_project",
    "exclude_failed_material_for_auto_advance",
    "exclude_material",
    "get_expert_project_detail",
    "get_expert_material_text",
    "list_expert_compiler_profile_options",
    "list_expert_projects",
    "mark_material_learning_attention",
    "mark_project_attention",
    "pause_expert_project",
    "prepare_expert_project",
    "repair_project_method",
    "retry_material_learning",
    "retry_material_preparation",
    "resume_expert_project",
    "run_material_learning_stage",
    "run_method_calibration",
    "run_project_orientation",
    "start_project_orientation",
    "trim_expert_project_activities",
]
