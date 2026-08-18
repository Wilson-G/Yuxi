"""专家材料包管理与快速编译接口。"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db
from yuxi.agents.skills.repository import SkillRepository
from yuxi.agents.skills.service import delete_skill, import_skill_dir, replace_skill_dir, update_skill_enabled
from yuxi.expert.service import (
    create_expert_package,
    delete_expert_package,
    get_compiled_skill_dir,
    get_expert_package,
    list_expert_packages,
    mark_expert_package_failed,
    update_expert_package,
)
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger

experts = APIRouter(prefix="/experts", tags=["experts"])
ACTIVE_TASK_STATUSES = {"queued", "processing"}


def _raise_value_error(error: ValueError) -> None:
    message = str(error)
    status = 404 if "不存在" in message else 400
    raise HTTPException(status_code=status, detail=message) from error


@experts.get("")
async def list_expert_packages_route(_current_user: User = Depends(get_admin_user)):
    """列出专家材料包。"""

    return {"items": await asyncio.to_thread(list_expert_packages)}


@experts.get("/{package_id}")
async def get_expert_package_route(package_id: str, _current_user: User = Depends(get_admin_user)):
    """读取专家材料包状态。"""

    try:
        return {"item": await asyncio.to_thread(get_expert_package, package_id)}
    except ValueError as error:
        _raise_value_error(error)


@experts.post("")
async def create_expert_package_route(
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    current_user: User = Depends(get_admin_user),
):
    """上传一个 Markdown 专家材料包。"""

    try:
        item = await asyncio.to_thread(
            create_expert_package,
            name=name,
            description=description,
            filename=file.filename or "",
            content=await file.read(),
            created_by=str(current_user.uid),
        )
        return {"item": item}
    except ValueError as error:
        _raise_value_error(error)
    except Exception as error:
        logger.exception("Failed to create expert package")
        raise HTTPException(status_code=500, detail="创建专家材料包失败") from error


@experts.post("/{package_id}/compile")
async def compile_expert_package_route(
    package_id: str,
    compiler: Literal["quick", "prime"] = Query("quick"),
    _current_user: User = Depends(get_admin_user),
):
    """提交可重复的批量索引与 Skill 编译任务。"""

    try:
        item = await asyncio.to_thread(get_expert_package, package_id)
    except ValueError as error:
        _raise_value_error(error)
    if item.get("active"):
        raise HTTPException(status_code=409, detail="请先停用专家包再重新编译")

    if item.get("status") in ACTIVE_TASK_STATUSES and item.get("active_task_id"):
        return {
            "status": item["status"],
            "task_id": item["active_task_id"],
            "task_created": False,
            "message": "专家包已有编译任务在执行",
        }

    task_id = uuid.uuid4().hex
    await asyncio.to_thread(
        update_expert_package,
        package_id,
        status="queued",
        active_task_id=task_id,
        progress=0,
        message="等待 Worker 执行",
        error=None,
    )
    try:
        pool = await get_arq_pool()
        await pool.enqueue_job(
            "process_expert_package_compile",
            package_id,
            task_id,
            compiler,
            _job_id=f"expert-compile:{package_id}:{compiler}:{task_id}",
        )
    except Exception as error:
        await asyncio.to_thread(mark_expert_package_failed, package_id, str(error))
        raise
    return {
        "status": "queued",
        "task_id": task_id,
        "task_created": True,
        "compiler": compiler,
        "message": "专家包编译任务已提交到 Worker",
    }


@experts.post("/{package_id}/activate")
async def activate_expert_package_route(
    package_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """将编译完成的专家包安装并启用为全局 Skill。"""

    try:
        item = await asyncio.to_thread(get_expert_package, package_id)
        source_dir = await asyncio.to_thread(get_compiled_skill_dir, package_id)
        installed_slug = item.get("skill_slug")
        installed = await SkillRepository(db).get_by_slug(installed_slug) if installed_slug else None
        with tempfile.TemporaryDirectory(prefix="expert-skill-") as temp_root:
            staged = Path(temp_root) / source_dir.name
            await asyncio.to_thread(shutil.copytree, source_dir, staged)
            if installed is None:
                installed_slug = None
                installed = await import_skill_dir(
                    db,
                    source_dir=staged,
                    created_by=str(current_user.uid),
                    share_config={
                        "version": 2,
                        "read_scope": {
                            "access_level": "global",
                            "department_ids": [],
                            "user_uids": [],
                        },
                        "manage_scope": None,
                    },
                )
                installed_slug = installed.slug
            elif not installed.enabled:
                installed = await update_skill_enabled(
                    db,
                    slug=installed.slug,
                    enabled=True,
                    operator=current_user,
                )
            installed = await replace_skill_dir(
                db,
                slug=installed.slug,
                source_dir=staged,
                updated_by=str(current_user.uid),
                version=str(item.get("version") or ""),
                content_hash=str(item.get("release_hash") or ""),
            )

        updated = await asyncio.to_thread(
            update_expert_package,
            package_id,
            status="active",
            stage="active",
            active=True,
            skill_slug=installed_slug,
            active_version=item.get("version"),
            progress=100,
            message="当前版本已激活",
            error=None,
        )
        return {"item": updated, "skill": installed.to_dict()}
    except ValueError as error:
        _raise_value_error(error)
    except Exception as error:
        logger.exception("Failed to activate expert package {}", package_id)
        await asyncio.to_thread(mark_expert_package_failed, package_id, str(error))
        raise HTTPException(status_code=500, detail="激活专家包失败") from error


@experts.post("/{package_id}/deactivate")
async def deactivate_expert_package_route(
    package_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """停用专家包对应的 Skill，但保留编译产物。"""

    try:
        item = await asyncio.to_thread(get_expert_package, package_id)
        skill_slug = item.get("skill_slug")
        installed = await SkillRepository(db).get_by_slug(skill_slug) if skill_slug else None
        if installed is not None and installed.enabled:
            await update_skill_enabled(
                db,
                slug=skill_slug,
                enabled=False,
                operator=current_user,
            )
        updated = await asyncio.to_thread(
            update_expert_package,
            package_id,
            status="compiled",
            stage="release",
            active=False,
            active_version=None,
            progress=0,
            message="专家包已停用",
            error=None,
        )
        return {"item": updated}
    except ValueError as error:
        _raise_value_error(error)
    except Exception as error:
        logger.exception("Failed to deactivate expert package {}", package_id)
        raise HTTPException(status_code=500, detail="停用专家包失败") from error


@experts.delete("/{package_id}")
async def delete_expert_package_route(
    package_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除停用后的专家包和对应 Skill。"""

    try:
        item = await asyncio.to_thread(get_expert_package, package_id)
        if item.get("active"):
            raise ValueError("请先停用专家包再删除")
        skill_slug = item.get("skill_slug")
        if skill_slug and await SkillRepository(db).get_by_slug(skill_slug):
            await delete_skill(db, slug=skill_slug)
        await asyncio.to_thread(delete_expert_package, package_id)
        return {"success": True, "package_id": package_id}
    except ValueError as error:
        _raise_value_error(error)
    except Exception as error:
        logger.exception("Failed to delete expert package {}", package_id)
        raise HTTPException(status_code=500, detail="删除专家包失败") from error
