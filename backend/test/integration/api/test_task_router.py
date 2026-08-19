"""
Integration tests for the task management router.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete

from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_material import MaterialLibrary, MaterialSourceFile

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_LITE_MODE = os.getenv("LITE_MODE", "").lower() in {"true", "1"}


async def test_standard_user_keeps_tasker_read_contract_without_expert_projection(test_client, standard_user):
    """普通用户保留 Tasker 读取合同，但不能看到专家项目投影。"""
    headers = standard_user["headers"]

    list_response = await test_client.get("/api/tasks", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["tasks"] == []

    detail_response = await test_client.get("/api/tasks/some-task", headers=headers)
    assert detail_response.status_code == 404

    cancel_response = await test_client.post("/api/tasks/some-task/cancel", headers=headers)
    assert cancel_response.status_code == 403

    delete_response = await test_client.delete("/api/tasks/some-task", headers=headers)
    assert delete_response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/api/expert-projects", {}),
        ("get", "/api/expert-projects/not-real", {}),
        (
            "post",
            "/api/expert-projects",
            {"json": {"name": "普通用户项目", "material_unit_ids": ["unit-1"]}},
        ),
        (
            "post",
            "/api/expert-projects/not-real/orientation",
            {"json": {"representative_material_ids": ["material-1"]}},
        ),
    ],
)
async def test_expert_project_routes_require_admin(test_client, standard_user, method, path, kwargs):
    """普通用户不能创建、读取或推进编译中的专家项目。"""

    response = await getattr(test_client, method)(path, headers=standard_user["headers"], **kwargs)

    assert response.status_code == 403


async def test_admin_can_list_tasks(test_client, admin_headers):
    """Admin should receive a well-formed task list payload."""
    response = await test_client.get("/api/tasks", headers=admin_headers)
    assert response.status_code == 200, response.text

    payload = response.json()
    assert "tasks" in payload
    assert isinstance(payload["tasks"], list)
    assert "summary" in payload
    assert isinstance(payload["summary"], dict)


async def test_admin_can_read_waiting_expert_projection_without_completion(test_client, admin_headers):
    """管理员能读取专家项目投影，等待确认不会进入完成筛选。"""

    library_id = f"pytest-library-{uuid.uuid4().hex}"
    file_id = f"pytest-file-{uuid.uuid4().hex}"
    unit_id = f"pytest-unit-{uuid.uuid4().hex}"
    async with pg_manager.get_async_session_context() as db:
        repo = MaterialLibraryRepository(db)
        await repo.create_library(library_id=library_id, name="任务投影资料库", created_by="pytest")
        await repo.create_source_file(
            file_id=file_id,
            library_id=library_id,
            directory_id=None,
            original_filename="material.md",
            relative_path="material.md",
            file_type=".md",
            file_size=20,
            source_bucket="materials",
            source_object=f"pytest/{file_id}/original.md",
            markdown_bucket="materials",
            markdown_object=f"pytest/{file_id}/content.md",
            status="ready",
            unit_status="ready",
            progress=100,
            line_count=2,
            error_message=None,
            created_by="pytest",
        )
        await repo.create_material_units(
            file_id,
            [{"unit_id": unit_id, "title": "完整材料", "start_line": 1, "end_line": 2}],
        )
        await db.commit()

    create_response = await test_client.post(
        "/api/expert-projects",
        json={
            "name": f"pytest_task_project_{uuid.uuid4().hex[:8]}",
            "material_unit_ids": [unit_id],
        },
        headers=admin_headers,
    )
    assert create_response.status_code == 200, create_response.text
    project_id = create_response.json()["item"]["project_id"]

    try:
        for _ in range(40):
            project_response = await test_client.get(f"/api/expert-projects/{project_id}", headers=admin_headers)
            assert project_response.status_code == 200, project_response.text
            if project_response.json()["item"]["status"] == "ready":
                break
            await asyncio.sleep(0.25)
        else:
            pytest.fail("Expert project did not reach ready status within timeout")

        detail_response = await test_client.get(
            f"/api/tasks/expert-project:{project_id}",
            headers=admin_headers,
        )
        completed_response = await test_client.get(
            "/api/tasks",
            params={"status": "success"},
            headers=admin_headers,
        )

        assert detail_response.status_code == 200, detail_response.text
        task = detail_response.json()["task"]
        assert task["type"] == "expert_project"
        assert task["status"] == "pending"
        assert task["completed_at"] is None
        assert completed_response.status_code == 200, completed_response.text
        assert all(item["id"] != f"expert-project:{project_id}" for item in completed_response.json()["tasks"])
    finally:
        delete_response = await test_client.delete(f"/api/expert-projects/{project_id}", headers=admin_headers)
        assert delete_response.status_code == 200, delete_response.text
        async with pg_manager.get_async_session_context() as db:
            await db.execute(delete(MaterialSourceFile).where(MaterialSourceFile.file_id == file_id))
            await db.execute(delete(MaterialLibrary).where(MaterialLibrary.library_id == library_id))


async def test_admin_cannot_create_new_project_with_legacy_multipart_upload(test_client, admin_headers):
    response = await test_client.post(
        "/api/expert-projects",
        data={"name": "旧上传入口"},
        files={"files": ("material.md", b"material", "text/markdown")},
        headers=admin_headers,
    )

    assert response.status_code == 422


async def test_cancel_unknown_task_returns_client_error(test_client, admin_headers):
    """Cancelling a non-existent task should surface a 400 response."""
    response = await test_client.post("/api/tasks/not-real/cancel", headers=admin_headers)
    assert response.status_code == 400, response.text


async def test_enqueue_document_creates_task(
    test_client,
    admin_headers,
):
    """Trigger knowledge ingestion to ensure a task record is materialised."""
    if _LITE_MODE:
        enqueue_response = await test_client.post(
            "/api/knowledge/databases/lite-mode-disabled/documents",
            json={"items": [], "params": {"content_type": "file"}},
            headers=admin_headers,
        )
        assert enqueue_response.status_code == 404
        return

    create_response = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": f"pytest_task_router_{uuid.uuid4().hex[:8]}",
            "description": "Task router integration test",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "kb_type": "milvus",
            "additional_params": {},
        },
        headers=admin_headers,
    )
    assert create_response.status_code == 200, create_response.text
    kb_id = create_response.json()["kb_id"]

    try:
        upload_response = await test_client.post(
            "/api/knowledge/files/upload",
            params={"kb_id": kb_id},
            files={
                "file": (
                    f"pytest_task_{uuid.uuid4().hex[:8]}.txt",
                    b"task router integration test",
                    "text/plain",
                )
            },
            headers=admin_headers,
        )
        assert upload_response.status_code == 200, upload_response.text
        upload_payload = upload_response.json()
        file_path = upload_payload["file_path"]

        enqueue_response = await test_client.post(
            f"/api/knowledge/databases/{kb_id}/documents",
            json={
                "items": [file_path],
                "params": {
                    "content_type": "file",
                    "content_hashes": {file_path: upload_payload["content_hash"]},
                    "file_sizes": {file_path: upload_payload["size"]},
                },
            },
            headers=admin_headers,
        )
        assert enqueue_response.status_code == 200, enqueue_response.text

        enqueue_payload = enqueue_response.json()
        assert enqueue_payload.get("status") == "queued"
        task_id = enqueue_payload.get("task_id")
        assert task_id, "Knowledge ingestion did not return a task_id"

        # The task should be queryable immediately after enqueueing.
        detail_response = await test_client.get(f"/api/tasks/{task_id}", headers=admin_headers)
        assert detail_response.status_code == 200, detail_response.text
        detail_payload = detail_response.json().get("task", {})
        assert detail_payload.get("id") == task_id
        assert detail_payload.get("status") in {"queued", "pending", "running", "failed", "success", "cancelled"}

        # Ensure the task surfaces in the list endpoint within a short window.
        for _ in range(10):
            list_response = await test_client.get("/api/tasks", headers=admin_headers)
            assert list_response.status_code == 200, list_response.text
            all_tasks = list_response.json().get("tasks", [])
            if any(entry.get("id") == task_id for entry in all_tasks):
                break
            await asyncio.sleep(0.2)
        else:
            pytest.fail("Task did not appear in list endpoint within timeout window")

        # Poll for terminal state to validate worker bookkeeping.
        for _ in range(20):
            detail_response = await test_client.get(f"/api/tasks/{task_id}", headers=admin_headers)
            task_status = detail_response.json().get("task", {}).get("status")
            if task_status in {"success", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail("Task did not reach a terminal status within timeout window")
    finally:
        await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
