"""资料库 LITE 路由与图片 URL 合同测试。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from yuxi.knowledge.parser.image_urls import build_parsed_image_proxy_url

import server.routers
import server.routers.material_library_router as router
from server.routers.material_library_router import document_images, material_libraries
from server.utils.auth_middleware import get_admin_user, get_db

pytestmark = pytest.mark.unit


def test_material_routes_are_registered_when_lite_mode_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITE_MODE", "true")
    router_module = importlib.reload(server.routers)
    route_paths = {route.path for route in router_module.router.routes if isinstance(route, APIRoute)}

    assert "/material-libraries" in route_paths
    assert "/material-libraries/{library_id}/files" in route_paths
    assert "/material-libraries/{library_id}/units" in route_paths
    assert "/material-libraries/{library_id}/units/{unit_id}/text" in route_paths
    assert "/material-libraries/{library_id}/files/{file_id}/retry-units" in route_paths
    assert "/document-images/{object_path:path}" in route_paths


def test_material_and_document_image_routes_require_admin() -> None:
    routes = [
        route
        for router in (material_libraries, document_images)
        for route in router.routes
        if isinstance(route, APIRoute)
    ]

    assert routes
    for route in routes:
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert get_admin_user in dependency_calls


def test_parser_keeps_legacy_kb_url_and_uses_shared_url_for_materials() -> None:
    assert build_parsed_image_proxy_url("kb-1/kb-images/diagram.png") == (
        "/api/knowledge/databases/kb-1/images/kb-images/diagram.png"
    )
    assert (
        build_parsed_image_proxy_url("material-libraries/library-1/source-files/file-1/images/user 1.png")
        == "/api/document-images/material-libraries/library-1/source-files/file-1/images/user%201.png"
    )
    assert build_parsed_image_proxy_url("expert-projects/project-1/material-1/image.png") == (
        "/api/document-images/expert-projects/project-1/material-1/image.png"
    )


def test_upload_and_retry_report_enqueue_failure_by_persisted_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次上传返回已保存事实，显式重试仍向用户报告投递失败。"""

    pending_item = {
        "file_id": "file-1",
        "relative_path": "guide.md",
        "status": "pending",
        "error_message": None,
    }
    attention_item = {
        **pending_item,
        "status": "needs_attention",
        "error_message": "来源文件任务投递失败: redis unavailable",
    }
    calls: list[str] = []

    async def fake_create_source_file(_db, **kwargs):
        assert kwargs["library_id"] == "library-1"
        assert kwargs["relative_path"] == "guide.md"
        assert kwargs["filename"] == "guide.md"
        assert kwargs["content"] == b"# Guide"
        assert kwargs["created_by"] == "admin-1"
        calls.append("create")
        return pending_item

    async def fail_enqueue(file_id: str) -> None:
        assert file_id == "file-1"
        calls.append("enqueue")
        raise ConnectionError("redis unavailable")

    async def fake_reset_source_file(_db, *, library_id: str, file_id: str):
        assert library_id == "library-1"
        assert file_id == "file-1"
        calls.append("reset")
        return pending_item

    async def fake_mark_enqueue_failed(_db, *, file_id: str, error: str):
        assert file_id == "file-1"
        assert error == "来源文件任务投递失败: redis unavailable"
        calls.append("mark")
        return attention_item

    monkeypatch.setattr(router, "create_material_source_file", fake_create_source_file)
    monkeypatch.setattr(router, "reset_material_source_file", fake_reset_source_file)
    monkeypatch.setattr(router, "enqueue_material_source_file", fail_enqueue)
    monkeypatch.setattr(router, "mark_material_source_enqueue_failed", fake_mark_enqueue_failed)

    app = FastAPI()
    app.include_router(material_libraries, prefix="/api")

    async def fake_admin_user():
        return SimpleNamespace(uid="admin-1")

    async def fake_db():
        yield object()

    app.dependency_overrides[get_admin_user] = fake_admin_user
    app.dependency_overrides[get_db] = fake_db

    response = TestClient(app).post(
        "/api/material-libraries/library-1/files",
        data={"relative_path": "guide.md"},
        files={"file": ("guide.md", b"# Guide", "text/markdown")},
    )

    assert response.status_code == 202
    assert response.json() == {"item": attention_item}
    assert calls == ["create", "enqueue", "mark"]

    calls.clear()
    retry_response = TestClient(app).post(
        "/api/material-libraries/library-1/files/file-1/retry",
    )

    assert retry_response.status_code == 503
    assert retry_response.json() == {"detail": "来源文件任务投递失败: redis unavailable"}
    assert calls == ["reset", "enqueue", "mark"]
