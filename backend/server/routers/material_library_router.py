"""资料库、来源文件与共享解析图片接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.material_library.service import (
    create_material_directory,
    create_material_library,
    create_material_source_file,
    delete_material_directory,
    delete_material_source_file,
    get_material_library_detail,
    get_material_source_text,
    get_material_unit_text,
    list_material_libraries,
    list_material_source_files,
    list_material_units,
    mark_material_source_enqueue_failed,
    mark_material_unit_enqueue_failed,
    reset_material_source_file,
    reset_material_source_units,
)
from yuxi.material_library.worker import enqueue_material_source_file, enqueue_material_source_units
from yuxi.storage.postgres.models_business import User
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES, read_upload_with_limit

from server.utils.auth_middleware import get_admin_user, get_db
from server.utils.private_image_response import create_private_image_response

material_libraries = APIRouter(prefix="/material-libraries", tags=["material-libraries"])
document_images = APIRouter(prefix="/document-images", tags=["document-images"])


class MaterialLibraryCreate(BaseModel):
    """创建资料库请求。"""

    name: str = Field(min_length=1, max_length=255)


class MaterialDirectoryCreate(BaseModel):
    """创建资料库目录请求。"""

    name: str = Field(min_length=1, max_length=512)
    parent_id: str | None = None


@material_libraries.get("")
async def list_material_libraries_route(
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """列出全部资料库。"""

    return {"items": await list_material_libraries(db)}


@material_libraries.post("", status_code=201)
async def create_material_library_route(
    payload: MaterialLibraryCreate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建资料库。"""

    try:
        item = await create_material_library(db, name=payload.name, created_by=str(current_user.uid))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@material_libraries.get("/{library_id}")
async def get_material_library_route(
    library_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """读取资料库目录和文件列表。"""

    try:
        return {"item": await get_material_library_detail(db, library_id=library_id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@material_libraries.post("/{library_id}/directories", status_code=201)
async def create_material_directory_route(
    library_id: str,
    payload: MaterialDirectoryCreate,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建一个资料库目录。"""

    try:
        item = await create_material_directory(
            db,
            library_id=library_id,
            name=payload.name,
            parent_id=payload.parent_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@material_libraries.delete("/{library_id}/directories/{directory_id}", status_code=204)
async def delete_material_directory_route(
    library_id: str,
    directory_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除一个空资料库目录。"""

    try:
        await delete_material_directory(db, library_id=library_id, directory_id=directory_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@material_libraries.get("/{library_id}/files")
async def list_material_source_files_route(
    library_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """列出资料库中的全部来源文件。"""

    try:
        return {"items": await list_material_source_files(db, library_id=library_id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@material_libraries.post("/{library_id}/files", status_code=202)
async def upload_material_source_file_route(
    library_id: str,
    file: UploadFile = File(...),
    relative_path: str = Form(""),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """逐文件上传原件，保留目录相对路径并提交独立处理任务。"""

    filename = file.filename or ""
    try:
        content = await read_upload_with_limit(
            file,
            max_size_bytes=MAX_UPLOAD_SIZE_BYTES,
            too_large_message="单个资料文件不能超过 100 MB",
        )
        item = await create_material_source_file(
            db,
            library_id=library_id,
            relative_path=relative_path,
            filename=filename,
            content=content,
            created_by=str(current_user.uid),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _enqueue_or_mark_attention(db, item["file_id"])
    return {"item": item}


@material_libraries.get("/{library_id}/units")
async def list_material_units_route(
    library_id: str,
    file_id: str | None = None,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """列出资料库中已经确认完成的可选材料单元。"""

    try:
        return {"items": await list_material_units(db, library_id=library_id, file_id=file_id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@material_libraries.get("/{library_id}/units/{unit_id}/text")
async def get_material_unit_text_route(
    library_id: str,
    unit_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """读取一个已确认材料单元的完整 Markdown 正文。"""

    try:
        return {"item": await get_material_unit_text(db, library_id=library_id, unit_id=unit_id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@material_libraries.get("/{library_id}/files/{file_id}/text")
async def get_material_source_text_route(
    library_id: str,
    file_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """预览来源文件的完整规范化 Markdown。"""

    try:
        return {"item": await get_material_source_text(db, library_id=library_id, file_id=file_id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@material_libraries.post("/{library_id}/files/{file_id}/retry", status_code=202)
async def retry_material_source_file_route(
    library_id: str,
    file_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """只重试指定来源文件。"""

    try:
        item = await reset_material_source_file(db, library_id=library_id, file_id=file_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _enqueue_or_mark_attention(db, file_id)
    return {"item": item}


@material_libraries.post("/{library_id}/files/{file_id}/retry-units", status_code=202)
async def retry_material_source_units_route(
    library_id: str,
    file_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """只重跑指定来源文件的自然边界确认。"""

    try:
        item = await reset_material_source_units(db, library_id=library_id, file_id=file_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _enqueue_units_or_mark_attention(db, file_id)
    return {"item": item}


@material_libraries.delete("/{library_id}/files/{file_id}", status_code=204)
async def delete_material_source_file_route(
    library_id: str,
    file_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除一份未进入下游引用阶段的来源文件。"""

    try:
        await delete_material_source_file(db, library_id=library_id, file_id=file_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@document_images.get("/{object_path:path}")
async def get_document_image_route(
    object_path: str,
    _current_user: User = Depends(get_admin_user),
):
    """经管理员鉴权读取资料库或专家解析图片。"""

    if not object_path.startswith(("material-libraries/", "expert-projects/")):
        raise HTTPException(status_code=400, detail="非法的文档图片路径")
    if ".." in object_path.split("/") or "\\" in object_path:
        raise HTTPException(status_code=400, detail="非法的文档图片路径")
    return await create_private_image_response(object_path)


async def _enqueue_or_mark_attention(db: AsyncSession, file_id: str) -> None:
    try:
        await enqueue_material_source_file(file_id)
    except Exception as exc:
        message = f"来源文件任务投递失败: {exc}"
        await mark_material_source_enqueue_failed(db, file_id=file_id, error=message)
        raise HTTPException(status_code=503, detail=message) from exc


async def _enqueue_units_or_mark_attention(db: AsyncSession, file_id: str) -> None:
    try:
        await enqueue_material_source_units(file_id)
    except Exception as exc:
        message = f"材料单元确认任务投递失败: {exc}"
        await mark_material_unit_enqueue_failed(db, file_id=file_id, error=message)
        raise HTTPException(status_code=503, detail=message) from exc


__all__ = ["document_images", "material_libraries"]
