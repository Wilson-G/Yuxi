"""专家项目第 0 阶段与材料学习交接用例。"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.expert.contracts import MaterialNoteResult, MaterialReviewResult, ProjectPromptPack
from yuxi.expert.model_tasks import ExpertModelTasks
from yuxi.expert.storage import (
    StoredMaterialSource,
    create_project_workspace,
    delete_project_workspace,
    parse_material,
    read_material_text,
    store_uploaded_sources,
)
from yuxi.models.providers.cache import model_cache
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.datetime_utils import utc_now_naive

MATERIAL_PARSE_CONCURRENCY = int(os.getenv("EXPERT_MATERIAL_PARSE_CONCURRENCY", "8"))
MATERIAL_LEARNING_CONCURRENCY = int(os.getenv("EXPERT_LEARNING_CONCURRENCY", "50"))
MAX_MATERIAL_REVISIONS = 2
ORIENTATION_STAGE = "orientation"
MATERIAL_LEARNING_STAGE = "material_learning"


async def create_expert_project(
    db: AsyncSession,
    *,
    name: str,
    description: str,
    model_spec: str,
    created_by: str,
    uploads: list[tuple[str, Path]],
) -> Any:
    """保存上传材料并创建等待正文转换的专家项目。"""

    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("专家项目名称不能为空")
    if not uploads:
        raise ValueError("请至少上传一份材料")
    model_info = model_cache.get_model_info(model_spec)
    if model_info is None or model_info.model_type != "chat":
        raise ValueError(f"专家项目模型不可用: {model_spec}")
    if not int(model_info.context_length or 0):
        raise ValueError("专家项目模型缺少上下文长度配置，请先在模型管理中补充")

    project_id = uuid.uuid4().hex
    create_project_workspace(project_id)
    try:
        sources = await asyncio.to_thread(store_uploaded_sources, project_id, uploads)
        repo = ExpertRepository(db)
        project = await repo.create_project(
            project_id=project_id,
            name=clean_name,
            description=str(description or "").strip(),
            model_spec=model_spec,
            created_by=created_by,
            stage=ORIENTATION_STAGE,
            status="preparing",
        )
        project.message = f"正在把 {len(sources)} 份材料转换为可靠正文"
        await repo.create_materials(
            project_id,
            [
                {
                    "material_id": source.material_id,
                    "title": source.title,
                    "source_path": source.source_path,
                    "raw_path": source.raw_path,
                    "file_type": source.file_type,
                    "status": "pending",
                }
                for source in sources
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
    if not material.text_path:
        raise ValueError("材料尚未得到可靠正文")
    return {
        "material_id": material.material_id,
        "title": material.title,
        "source_path": material.source_path,
        "text": read_material_text(project_id, material.text_path),
    }


async def prepare_expert_project(project_id: str) -> dict[str, Any]:
    """转换项目内尚未完成的原始材料，并按项目记录决定下一步。"""

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None:
            raise ValueError("专家项目不存在")
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

    semaphore = asyncio.Semaphore(max(1, MATERIAL_PARSE_CONCURRENCY))

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
    if not 1 <= len(selected_ids) <= 20:
        raise ValueError("请选择 1～20 份代表性材料")

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


async def run_project_orientation(
    project_id: str,
    method_version: int,
    *,
    model_tasks: ExpertModelTasks | None = None,
) -> dict[str, Any]:
    """生成当前方法版本的定向、专属 Prompt、笔记小样与 Review。"""

    tasks = model_tasks or ExpertModelTasks()
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
    catalog = _build_material_catalog(materials, aliases)
    samples = []
    representative_materials = []
    for material_id in project_snapshot["representative_ids"]:
        material = next((item for item in materials if item.material_id == material_id), None)
        if material is None or not material.text_path:
            raise ValueError("代表性材料正文不存在")
        representative_materials.append(material)
        samples.append(
            {
                "alias": aliases[material.material_id],
                "title": material.title,
                "source": material.source_path,
                "text": read_material_text(project_id, material.text_path),
            }
        )

    has_method_candidate = all(
        (
            project_snapshot["orientation_report"].strip(),
            project_snapshot["compilation_brief"].strip(),
            project_snapshot["learning_plan"].strip(),
            project_snapshot["prompt_pack"],
        )
    )
    if has_method_candidate:
        learning_plan = project_snapshot["learning_plan"]
        prompt_pack = ProjectPromptPack.model_validate(project_snapshot["prompt_pack"])
        sample_material_ids = project_snapshot["method_sample_material_ids"]
        _validate_method_contract(learning_plan, project_snapshot["compilation_brief"], prompt_pack)
    else:
        orientation = await tasks.orient_project(
            model_spec=project_snapshot["model_spec"],
            project_name=project_snapshot["name"],
            project_description=project_snapshot["description"],
            material_catalog=catalog,
            samples=samples,
            feedback=project_snapshot["feedback"],
        )
        method = await tasks.design_project_method(
            model_spec=project_snapshot["model_spec"],
            project_name=project_snapshot["name"],
            orientation=orientation,
            material_catalog=catalog,
            samples=samples,
        )
        _validate_method_contract(method.learning_plan_markdown, method.compilation_brief_markdown, method.prompt_pack)
        selected_aliases = set(method.method_sample_aliases)
        available_aliases = {sample["alias"] for sample in samples}
        if not selected_aliases <= available_aliases or not 1 <= len(selected_aliases) <= 2:
            raise ValueError("项目方法没有从代表材料中选择 1～2 份试跑小样")
        sample_material_ids = [
            material.material_id
            for material, sample in zip(representative_materials, samples, strict=True)
            if sample["alias"] in selected_aliases
        ]
        learning_plan = method.learning_plan_markdown
        prompt_pack = method.prompt_pack

        async with pg_manager.get_async_session_context() as db:
            project = await ExpertRepository(db).lock_project(project_id)
            if (
                project is None
                or project.method_version != method_version
                or project.stage != ORIENTATION_STAGE
                or project.status != "running"
            ):
                return {"project_id": project_id, "ignored": True}
            project.orientation_report = orientation.report_markdown
            project.compilation_brief = method.compilation_brief_markdown
            project.learning_plan = learning_plan
            project.prompt_pack = prompt_pack.model_dump()
            project.method_sample_material_ids = sample_material_ids
            project.updated_at = utc_now_naive()
            await db.flush()

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
    sample_outcomes = await asyncio.gather(
        *(run_sample(material, sample) for material, sample in sample_pairs),
        return_exceptions=True,
    )
    failures = [outcome for outcome in sample_outcomes if isinstance(outcome, Exception)]
    if failures:
        raise failures[0]
    sample_results = [outcome for outcome in sample_outcomes if isinstance(outcome, dict)]

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
        project.status = "needs_attention" if needs_attention else "awaiting_confirmation"
        project.message = (
            "笔记小样仍有明确问题，请调整学习方法后重新试跑"
            if needs_attention
            else "学习方案和真实小样已就绪，等待确认怎么学"
        )
        project.error_message = needs_attention[0]["reason"] if needs_attention else None
        project.updated_at = utc_now_naive()
        await db.flush()
    return {
        "project_id": project_id,
        "method_version": method_version,
        "status": "needs_attention" if needs_attention else "awaiting_confirmation",
    }


async def confirm_project_method(db: AsyncSession, *, project_id: str, created_by: str) -> int:
    """确认当前方法：首次确认触发校准，校准后确认才进入批量学习。"""

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
    if project.method_confirmed_at is not None:
        raise ValueError("方法校准正在完成，不能再次确认")
    sample_ids = list(project.method_sample_material_ids or [])
    if not _method_calibration_issues(project.method_samples):
        project.method_confirmed_at = utc_now_naive()
        await _start_material_learning(db, project, repo)
        return project.method_version

    project.method_version += 1
    project.method_confirmed_at = utc_now_naive()
    project.method_calibration_completed_at = None
    project.method_calibration_baseline = _build_method_calibration_samples(project.method_samples, sample_ids)
    project.status = "running"
    project.message = "学习方向已确认，总编模型正在依据小样 Review 收敛方法并重新试跑"
    project.error_message = None
    project.updated_at = utc_now_naive()
    await db.commit()
    return project.method_version


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
    project.message = f"正在学习 {len(included)} 份材料"
    project.error_message = None
    project.updated_at = utc_now_naive()
    await db.commit()
    return project.method_version


async def run_method_calibration(
    project_id: str,
    method_version: int,
    *,
    model_tasks: ExpertModelTasks | None = None,
) -> dict[str, Any]:
    """由总编收敛方法、评估残余问题后直接决定是否批量学习。"""

    tasks = model_tasks or ExpertModelTasks()
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None:
            raise ValueError("专家项目不存在")
        if (
            project.stage != ORIENTATION_STAGE
            or project.status not in {"running", "awaiting_confirmation"}
            or project.method_version != method_version
            or project.method_confirmed_at is None
        ):
            return {"project_id": project_id, "ignored": True}
        sample_ids = list(project.method_sample_material_ids or [])
        has_pending_calibration = bool(project.method_samples)
        if has_pending_calibration:
            _method_calibration_issues(project.method_samples)
            samples = _build_method_calibration_samples(project.method_samples, sample_ids)
        snapshot = {
            "name": project.name,
            "model_spec": project.model_spec,
            "learning_plan": project.learning_plan or "",
            "compilation_brief": project.compilation_brief or "",
            "prompt_pack": ProjectPromptPack.model_validate(project.prompt_pack),
        }

    if has_pending_calibration:
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

    result = await run_project_orientation(project_id, method_version, model_tasks=tasks)
    if result.get("status") != "awaiting_confirmation":
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
            project.status = "needs_attention"
            project.message = "校准后仍存在会影响批量学习的方法问题"
            project.error_message = assessment.reason
            project.updated_at = utc_now_naive()
            await db.commit()
            return {"project_id": project_id, "method_version": method_version, "status": "needs_attention"}
        await _start_material_learning(db, project, repo)
    return {"project_id": project_id, "method_version": method_version, "status": "running"}


async def run_material_learning_stage(
    project_id: str,
    method_version: int,
    *,
    model_tasks: ExpertModelTasks | None = None,
) -> dict[str, Any]:
    """以持续补位并发逐份完成笔记、Review 和最多两次定点修订。"""

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None:
            raise ValueError("专家项目不存在")
        if project.stage != MATERIAL_LEARNING_STAGE or project.method_version != method_version:
            return {"project_id": project_id, "ignored": True}
        materials = await repo.list_project_materials(project_id)
        pending_ids = [
            item.material_id for item in materials if item.status in {"pending", "learning", "review_revise"}
        ]

    tasks = model_tasks or ExpertModelTasks()
    semaphore = asyncio.Semaphore(max(1, MATERIAL_LEARNING_CONCURRENCY))

    async def learn(material_id: str) -> None:
        async with semaphore:
            await _learn_one_material(project_id, material_id, method_version, tasks)

    await asyncio.gather(*(learn(material_id) for material_id in pending_ids))
    async with pg_manager.get_async_session_context() as db:
        status = await _finish_material_learning(db, project_id, method_version)
    return {"project_id": project_id, "method_version": method_version, "status": status}


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


async def mark_project_attention(project_id: str, error: str) -> None:
    """在有限系统重试耗尽后保存可行动错误。"""

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None:
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
        material = await repo.lock_material(material_id)
        if material is None or material.project_id != project_id or material.status not in {"pending", "converting"}:
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
            or project.method_version != method_version
            or material.status not in {"pending", "learning", "review_revise"}
        ):
            return
        if not material.text_path:
            material.status = "needs_attention"
            material.error_message = "材料缺少可靠正文"
            return
        resume_note_contract = material.note_contract if material.status in {"learning", "review_revise"} else None
        resume_review = material.review if resume_note_contract else None
        resume_revision_count = material.revision_count if resume_note_contract else 0
        material.status = "learning"
        material.error_message = None
        snapshot = {
            "model_spec": project.model_spec,
            "learning_plan": project.learning_plan or "",
            "prompt_pack": ProjectPromptPack.model_validate(project.prompt_pack),
            "title": material.title,
            "source_path": material.source_path,
            "text_path": material.text_path,
            "line_count": material.line_count,
            "resume_note_contract": resume_note_contract,
            "resume_review": resume_review,
            "resume_revision_count": resume_revision_count,
        }
        await db.commit()

    text = read_material_text(project_id, snapshot["text_path"])
    result = None
    last_error = None
    for attempt in range(2):
        try:
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
                    MaterialReviewResult.model_validate(snapshot["resume_review"])
                    if snapshot["resume_review"]
                    else None
                ),
                starting_revision_count=snapshot["resume_revision_count"],
            )
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(2)

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        material = await repo.lock_material(material_id)
        if (
            project is None
            or material is None
            or project.method_version != method_version
            or material.status != "learning"
        ):
            return
        if result is None:
            material.status = "needs_attention"
            material.error_message = str(last_error or "材料学习任务未返回结果")
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
    project = await repo.get_project(project_id)
    if project is None or project.stage != MATERIAL_LEARNING_STAGE or project.method_version != method_version:
        return "ignored"
    materials = await repo.list_project_materials(project_id)
    unfinished = [item for item in materials if item.status in {"pending", "learning", "review_revise"}]
    attention = [item for item in materials if item.status == "needs_attention"]
    if unfinished:
        project.status = "running"
        project.message = f"还有 {len(unfinished)} 份材料正在学习"
        return project.status
    if attention:
        project.status = "needs_attention"
        project.message = f"有 {len(attention)} 份材料需要处理后才能组织主题"
        project.error_message = attention[0].error_message
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
) -> dict[str, Any]:
    if (starting_note is None) != (starting_review is None):
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
    else:
        note = starting_note
        review = starting_review

    revision = None
    revision_count = starting_revision_count
    current_note = note
    final_review = review
    while final_review.decision == "revise" and revision_count < MAX_MATERIAL_REVISIONS:
        revision = await tasks.revise_material_note(
            model_spec=model_spec,
            learning_plan=learning_plan,
            stage_prompt=prompt_pack.note_reviser,
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

    final_note = revision or note
    if final_review.decision == "revise":
        status = "needs_attention"
        reason = f"已完成 {MAX_MATERIAL_REVISIONS} 次自动修订，Review 仍要求修订：{final_review.summary}"
    elif final_note.decision in {"no_value", "needs_attention"} or final_review.decision in {
        "no_value",
        "needs_attention",
    }:
        status = "no_value"
        reason = final_note.reason or final_review.summary
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
        "review": review.model_dump(),
        "revision": revision.model_dump() if revision else None,
        "revision_count": revision_count,
        "final_review": final_review.model_dump(),
        "reason": reason,
    }


def _validate_method_contract(learning_plan: str, compilation_brief: str, prompt_pack: ProjectPromptPack) -> None:
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
    """确认小样可交接；可修 Review 问题交给后续材料 Repair。"""

    if not samples or any(
        not isinstance(sample, dict)
        or sample.get("status") not in {"passed", "review_revise"}
        or (sample.get("final_review") or {}).get("decision") not in {"pass", "revise"}
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
        lines.append(
            " | ".join(
                [
                    aliases[material.material_id],
                    material.title,
                    material.source_path,
                    material.file_type,
                    f"{material.line_count} 行",
                    f"约 {material.token_count} tokens",
                    material.status,
                ]
            )
        )
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
    "exclude_material",
    "get_expert_project_detail",
    "get_expert_material_text",
    "list_expert_projects",
    "mark_project_attention",
    "prepare_expert_project",
    "retry_material_learning",
    "retry_material_preparation",
    "run_material_learning_stage",
    "run_method_calibration",
    "run_project_orientation",
    "start_project_orientation",
]
