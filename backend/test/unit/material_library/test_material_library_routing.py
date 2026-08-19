"""资料库 LITE 路由与图片 URL 合同测试。"""

from __future__ import annotations

import importlib

import pytest
from fastapi.routing import APIRoute
from yuxi.knowledge.parser.image_urls import build_parsed_image_proxy_url

import server.routers
from server.routers.material_library_router import document_images, material_libraries
from server.utils.auth_middleware import get_admin_user

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
