"""资料库材料单元管理员 API 合同测试。"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_material_unit_routes_require_admin(test_client, standard_user) -> None:
    headers = standard_user["headers"]

    list_response = await test_client.get("/api/material-libraries/not-real/units", headers=headers)
    text_response = await test_client.get(
        "/api/material-libraries/not-real/units/not-real/text",
        headers=headers,
    )
    retry_response = await test_client.post(
        "/api/material-libraries/not-real/files/not-real/retry-units",
        headers=headers,
    )

    assert list_response.status_code == 403
    assert text_response.status_code == 403
    assert retry_response.status_code == 403


async def test_admin_unit_list_reports_unknown_library(test_client, admin_headers) -> None:
    response = await test_client.get("/api/material-libraries/not-real/units", headers=admin_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "资料库不存在"
