"""专家项目导入、阶段产物与用户确认接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.agents.models import resolve_chat_model_spec
from yuxi.config.options import system_options
from yuxi.expert.compiler_profiles import DEFAULT_COMPILER_PROFILE_ID
from yuxi.expert.service import (
    confirm_project_method,
    create_expert_project,
    delete_expert_project,
    exclude_material,
    get_expert_material_text,
    get_expert_project_detail,
    list_expert_projects,
    list_expert_compiler_profile_options,
    pause_expert_project,
    repair_project_method,
    retry_material_learning,
    retry_material_preparation,
    resume_expert_project,
    start_project_orientation,
)
from yuxi.expert.compilation_service import (
    activate_expert_skill,
    get_candidate_preview,
    retry_book_compilation,
    retry_topic_compilation,
    uninstall_expert_skill,
)
from yuxi.expert.organization_service import (
    confirm_organization_stage,
    initialize_organization_stage,
    restart_organization_stage,
)
from yuxi.expert.worker import (
    enqueue_handout_compilation,
    enqueue_method_calibration,
    enqueue_material_learning,
    enqueue_pending_material_learning,
    enqueue_organization,
    enqueue_project_orientation,
    enqueue_project_preparation,
    enqueue_resumed_expert_project,
)
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger

expert_projects = APIRouter(
    prefix="/expert-projects",
    tags=["expert-projects"],
    dependencies=[Depends(get_admin_user)],
)


class CreateExpertProjectRequest(BaseModel):
    """新项目基础信息和已就绪资料库单元。"""

    name: str = Field(min_length=1)
    description: str
    model_spec: str = ""
    compiler_profile_id: str = DEFAULT_COMPILER_PROFILE_ID
    material_unit_ids: list[str] = Field(min_length=1)


class OrientationRequest(BaseModel):
    """用户明确选择代表性材料并开始或调整项目定向。"""

    representative_material_ids: list[str] = Field(min_length=1, max_length=20)
    feedback: str = ""


class ConfirmMethodRequest(BaseModel):
    """第一次确认时选择后续是否自动推进。"""

    auto_advance: bool = False


class ExcludeMaterialRequest(BaseModel):
    """用户明确排除需要处理的材料。"""

    reason: str = Field(min_length=1)


class OrganizationRevisionRequest(BaseModel):
    """用户对主题与讲义组织方式的明确调整意见。"""

    feedback: str = Field(min_length=1)


@expert_projects.get("")
async def list_expert_projects_route(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户的专家项目。"""

    return {"items": await list_expert_projects(db, created_by=str(current_user.uid))}


@expert_projects.get("/compiler-profiles")
async def list_expert_compiler_profiles_route():
    """列出当前可供创建项目选择的内置编译方法。"""

    try:
        return {
            "items": list_expert_compiler_profile_options(),
            "default_profile_id": DEFAULT_COMPILER_PROFILE_ID,
        }
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@expert_projects.get("/{project_id}")
async def get_expert_project_route(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """读取项目页面恢复当前流程所需的正式事实。"""

    try:
        return {
            "item": await get_expert_project_detail(
                db,
                project_id=project_id,
                created_by=str(current_user.uid),
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@expert_projects.get("/{project_id}/materials/{material_id}/text")
async def get_expert_material_text_route(
    project_id: str,
    material_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """预览一份材料经系统转换后的完整可靠正文。"""

    try:
        return {
            "item": await get_expert_material_text(
                db,
                project_id=project_id,
                material_id=material_id,
                created_by=str(current_user.uid),
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@expert_projects.get("/{project_id}/preview")
async def get_expert_candidate_preview_route(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """预览候选 Skill 的讲义、导航、精选证据和未纳入材料。"""

    try:
        return {
            "item": await get_candidate_preview(
                db,
                project_id=project_id,
                created_by=str(current_user.uid),
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@expert_projects.post("")
async def create_expert_project_route(
    payload: CreateExpertProjectRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """从资料库中已经处理完成的材料单元创建专家项目。"""

    requested_model = str(payload.model_spec or "").strip()
    if not requested_model:
        requested_model = resolve_chat_model_spec((await system_options.get(db))["default_model"])

    try:
        project = await create_expert_project(
            db,
            name=payload.name,
            description=payload.description,
            model_spec=requested_model,
            compiler_profile_id=payload.compiler_profile_id,
            created_by=str(current_user.uid),
            material_unit_ids=payload.material_unit_ids,
        )
        return {"item": project.to_dict()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create expert project")
        raise HTTPException(status_code=500, detail="创建专家项目失败") from exc


@expert_projects.post("/{project_id}/orientation")
async def start_project_orientation_route(
    project_id: str,
    payload: OrientationRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """按用户选择的真实样本生成一个新的项目方法版本。"""

    try:
        method_version = await start_project_orientation(
            db,
            project_id=project_id,
            created_by=str(current_user.uid),
            representative_material_ids=payload.representative_material_ids,
            feedback=payload.feedback,
        )
        await enqueue_project_orientation(project_id, method_version)
        return {"project_id": project_id, "method_version": method_version, "status": "running"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to enqueue expert project orientation {}", project_id)
        raise HTTPException(
            status_code=503,
            detail="项目方法版本已保存，但后台任务暂时无法投递；Worker 恢复后会继续",
        ) from exc


@expert_projects.post("/{project_id}/method/repair")
async def repair_project_method_route(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """依据初始小样 Review 创建校准版本并复跑同一批小样。"""

    try:
        method_version = await repair_project_method(
            db,
            project_id=project_id,
            created_by=str(current_user.uid),
        )
        await enqueue_method_calibration(project_id, method_version)
        return {"project_id": project_id, "method_version": method_version, "status": "running"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to enqueue expert method repair {}", project_id)
        raise HTTPException(
            status_code=503,
            detail="方法修复版本已保存，但后台任务暂时无法投递；Worker 恢复后会继续",
        ) from exc


@expert_projects.post("/{project_id}/method/confirm")
async def confirm_project_method_route(
    project_id: str,
    payload: ConfirmMethodRequest | None = None,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """第一次用户确认：确认 V1 方向并启动一次方法校准。"""

    try:
        method_version = await confirm_project_method(
            db,
            project_id=project_id,
            created_by=str(current_user.uid),
            auto_advance=payload.auto_advance if payload is not None else False,
        )
        project = await ExpertRepository(db).get_project(project_id)
        if project is not None and project.stage == "orientation":
            await enqueue_method_calibration(project_id, method_version)
        else:
            await enqueue_pending_material_learning(project_id, method_version)
        return {
            "project_id": project_id,
            "method_version": method_version,
            "status": "running",
            "auto_advance": bool(project.auto_advance) if project is not None else False,
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to enqueue expert material learning {}", project_id)
        raise HTTPException(
            status_code=503,
            detail="确认结果已保存，但后台任务暂时无法投递；Worker 恢复后会继续",
        ) from exc


@expert_projects.post("/{project_id}/materials/{material_id}/retry-conversion")
async def retry_material_conversion_route(
    project_id: str,
    material_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """只重跑一份正文转换失败的材料。"""

    try:
        await retry_material_preparation(
            db,
            project_id=project_id,
            material_id=material_id,
            created_by=str(current_user.uid),
        )
        await enqueue_project_preparation(project_id, token=material_id)
        return {"project_id": project_id, "material_id": material_id, "status": "pending"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@expert_projects.post("/{project_id}/organization/revise")
async def revise_project_organization_route(
    project_id: str,
    payload: OrganizationRevisionRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """按用户反馈创建新的组织版本并重新生成讲义小样。"""

    try:
        organization_version = await restart_organization_stage(
            db,
            project_id=project_id,
            created_by=str(current_user.uid),
            feedback=payload.feedback,
        )
        await enqueue_organization(project_id, organization_version)
        return {
            "project_id": project_id,
            "organization_version": organization_version,
            "status": "running",
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@expert_projects.post("/{project_id}/organization/confirm")
async def confirm_project_organization_route(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """第二次用户确认：固定怎么组织，并开始串行总编成书。"""

    try:
        organization_version = await confirm_organization_stage(
            db,
            project_id=project_id,
            created_by=str(current_user.uid),
        )
        await enqueue_handout_compilation(project_id, organization_version)
        return {
            "project_id": project_id,
            "organization_version": organization_version,
            "status": "running",
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@expert_projects.post("/{project_id}/topics/{topic_id}/retry")
async def retry_expert_topic_route(
    project_id: str,
    topic_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """只重跑一篇进入需要处理的正式讲义。"""

    try:
        organization_version = await retry_topic_compilation(
            db,
            project_id=project_id,
            topic_id=topic_id,
            created_by=str(current_user.uid),
        )
        await enqueue_handout_compilation(project_id, organization_version, token=topic_id)
        return {"project_id": project_id, "topic_id": topic_id, "status": "running"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@expert_projects.post("/{project_id}/compilation/retry")
async def retry_expert_book_route(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """在讲义均通过后重新执行全书收口。"""

    try:
        organization_version = await retry_book_compilation(
            db,
            project_id=project_id,
            created_by=str(current_user.uid),
        )
        await enqueue_handout_compilation(project_id, organization_version, token="book-retry")
        return {"project_id": project_id, "status": "running"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@expert_projects.post("/{project_id}/activate")
async def activate_expert_skill_route(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """用户预览成品后手动启用当前专家 Skill。"""

    try:
        skill = await activate_expert_skill(
            db,
            project_id=project_id,
            current_user=current_user,
        )
        return {"project_id": project_id, "status": "enabled", "skill": skill}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@expert_projects.post("/{project_id}/pause")
async def pause_expert_project_route(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """暂停项目并让恢复扫描跳过它。"""

    try:
        project = await pause_expert_project(
            db,
            project_id=project_id,
            created_by=str(current_user.uid),
        )
        return {"project_id": project.project_id, "status": project.status}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@expert_projects.post("/{project_id}/resume")
async def resume_expert_project_route(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """显式恢复暂停项目，并按项目事实重新投递必要工作。"""

    try:
        project = await resume_expert_project(
            db,
            project_id=project_id,
            created_by=str(current_user.uid),
        )
        await enqueue_resumed_expert_project(project_id)
        return {
            "project_id": project.project_id,
            "stage": project.stage,
            "status": project.status,
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to enqueue resumed expert project {}", project_id)
        await pause_expert_project(
            db,
            project_id=project_id,
            created_by=str(current_user.uid),
        )
        raise HTTPException(
            status_code=503,
            detail="项目仍保持暂停，后台任务恢复后请再次重试",
        ) from exc


@expert_projects.post("/{project_id}/uninstall")
async def uninstall_expert_skill_route(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """卸载已启用专家 Skill，保留项目候选成品以便再次启用。"""

    try:
        project = await uninstall_expert_skill(
            db,
            project_id=project_id,
            current_user=current_user,
        )
        return {"project_id": project.project_id, "status": project.status}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@expert_projects.delete("/{project_id}")
async def delete_expert_project_route(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """删除暂停或未运行且未启用 Skill 的专家项目。"""

    try:
        await delete_expert_project(
            db,
            project_id=project_id,
            created_by=str(current_user.uid),
        )
        return {"project_id": project_id, "status": "deleted"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@expert_projects.post("/{project_id}/materials/{material_id}/retry-learning")
async def retry_material_learning_route(
    project_id: str,
    material_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """只重跑一份笔记或 Review 失败的材料。"""

    try:
        method_version = await retry_material_learning(
            db,
            project_id=project_id,
            material_id=material_id,
            created_by=str(current_user.uid),
        )
        await enqueue_material_learning(project_id, material_id, method_version)
        return {"project_id": project_id, "material_id": material_id, "status": "pending"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@expert_projects.post("/{project_id}/materials/{material_id}/exclude")
async def exclude_expert_material_route(
    project_id: str,
    material_id: str,
    payload: ExcludeMaterialRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """明确排除一份需要处理的材料，并保留未纳入记录。"""

    try:
        should_start_organization = await exclude_material(
            db,
            project_id=project_id,
            material_id=material_id,
            created_by=str(current_user.uid),
            reason=payload.reason,
        )
        if should_start_organization:
            organization_version = await initialize_organization_stage(project_id)
            await enqueue_organization(project_id, organization_version)
        return {"project_id": project_id, "material_id": material_id, "status": "excluded"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


__all__ = ["expert_projects"]
