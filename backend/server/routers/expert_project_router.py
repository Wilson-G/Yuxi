"""专家项目导入、阶段产物与用户确认接口。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.models import resolve_chat_model_spec
from yuxi.config.options import system_options
from yuxi.expert.service import (
    confirm_project_method,
    create_expert_project,
    exclude_material,
    get_expert_material_text,
    get_expert_project_detail,
    list_expert_projects,
    retry_material_learning,
    retry_material_preparation,
    start_project_orientation,
)
from yuxi.expert.compilation_service import (
    activate_expert_skill,
    get_candidate_preview,
    retry_book_compilation,
    retry_topic_compilation,
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
    enqueue_organization,
    enqueue_project_orientation,
    enqueue_project_preparation,
)
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger
from yuxi.utils.upload_utils import write_upload_to_path

expert_projects = APIRouter(prefix="/expert-projects", tags=["expert-projects"])
MAX_PROJECT_UPLOAD_BYTES = int(os.getenv("EXPERT_PROJECT_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))


class OrientationRequest(BaseModel):
    """用户明确选择代表性材料并开始或调整项目定向。"""

    representative_material_ids: list[str] = Field(min_length=1, max_length=20)
    feedback: str = ""


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
    name: str = Form(...),
    description: str = Form(""),
    model_spec: str = Form(""),
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """创建项目并把上传文件保存为待转换的自然材料单元。"""

    requested_model = str(model_spec or "").strip()
    if not requested_model:
        requested_model = resolve_chat_model_spec((await system_options.get(db))["default_model"])

    try:
        with tempfile.TemporaryDirectory(prefix="expert-project-upload-") as temp_dir:
            staged_uploads = []
            total_bytes = 0
            for index, upload in enumerate(files):
                remaining = MAX_PROJECT_UPLOAD_BYTES - total_bytes
                if remaining <= 0:
                    raise ValueError("专家项目上传内容过大")
                suffix = Path(upload.filename or "").suffix
                target = Path(temp_dir) / f"upload-{index}{suffix}"
                written = await write_upload_to_path(
                    upload,
                    target,
                    max_size_bytes=remaining,
                    too_large_message="专家项目上传内容过大",
                )
                total_bytes += written
                staged_uploads.append((upload.filename or f"material-{index}", target))

            project = await create_expert_project(
                db,
                name=name,
                description=description,
                model_spec=requested_model,
                created_by=str(current_user.uid),
                uploads=staged_uploads,
            )
        await enqueue_project_preparation(project.project_id)
        return {"item": project.to_dict()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create expert project")
        if "project" in locals():
            raise HTTPException(
                status_code=503,
                detail="项目已保存，但后台任务暂时无法投递；Worker 恢复后会继续",
            ) from exc
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


@expert_projects.post("/{project_id}/method/confirm")
async def confirm_project_method_route(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """第一次用户确认：确认 V1 方向并启动一次方法校准。"""

    try:
        method_version = await confirm_project_method(
            db,
            project_id=project_id,
            created_by=str(current_user.uid),
        )
        project = await ExpertRepository(db).get_project(project_id)
        if project is not None and project.stage == "orientation":
            await enqueue_method_calibration(project_id, method_version)
        else:
            await enqueue_material_learning(project_id, method_version)
        return {"project_id": project_id, "method_version": method_version, "status": "running"}
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
        await enqueue_material_learning(project_id, method_version, token=material_id)
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
