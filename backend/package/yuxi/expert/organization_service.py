"""专家项目主题组织、编辑方案与讲义小样阶段。"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.expert.compiler_profiles import get_expert_compiler_profile
from yuxi.expert.concurrency import get_expert_concurrency
from yuxi.expert.contracts import (
    HandoutDraftResult,
    HandoutReviewResult,
    PlanningGroupsResult,
    ProjectPromptPack,
    TopicEvidenceItem,
    TopicEvidencePack,
    TopicMapResult,
    TopicResearchResult,
)
from yuxi.expert.materials import format_expert_material_excerpt, load_expert_material_source_texts
from yuxi.expert.model_tasks import ExpertModelTasks, is_transient_expert_error
from yuxi.expert.storage import read_material_text as read_legacy_material_text
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.datetime_utils import utc_now_naive

# 组织阶段结构校验失败后的带反馈重试次数：校验错误是「模型没按合同交」，
# 交给模型修正而不是判死整阶段；耗尽后仍维持原 needs_attention 语义。
ORGANIZATION_VALIDATION_FEEDBACK_RETRIES = 2
# 校验反馈附带完整笔记目录，帮助总编把遗漏的别名归位而不是猜。
_TOPIC_MAP_FEEDBACK_CATALOG = "笔记目录（全部已通过笔记的唯一合法别名）：{aliases}"


async def initialize_organization_stage(project_id: str, *, feedback: str = "") -> int:
    """创建知识组织版本；已开始的同一版本直接返回以支持交接重试。"""

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.lock_project(project_id)
        if project is None:
            raise ValueError("专家项目不存在")
        if project.stage == "organization" and project.status == "running":
            if project.organization_version < 1:
                raise ValueError("当前项目缺少有效的知识组织版本")
            return project.organization_version
        if project.stage != "organization" or project.status not in {
            "preparing",
            "awaiting_confirmation",
            "needs_attention",
        }:
            raise ValueError("当前项目状态不能开始主题组织")

        if project.organization_version > 0:
            await repo.replace_research_batches(project_id, project.organization_version, [])
            await repo.replace_topics(project_id, project.organization_version, [])
        project.organization_version += 1
        project.organization_confirmed_at = None
        project.organization_feedback = str(feedback or "").strip()
        project.planning_groups = []
        project.topic_map = {}
        project.editorial_plan = None
        project.organization_samples = []
        project.status = "running"
        project.message = "研究助手正在准备候选主题，总编模型随后统一组织讲义"
        project.error_message = None
        project.updated_at = utc_now_naive()
        await db.flush()
        return project.organization_version


async def run_organization_stage(
    project_id: str,
    organization_version: int,
    *,
    model_tasks: ExpertModelTasks | None = None,
) -> dict[str, Any]:
    """形成有意义规划分组、候选研究、主题地图和讲义小样。"""

    tasks = model_tasks or ExpertModelTasks(
        prompt_cache_namespace=f"expert:{project_id}:organization:{organization_version}"
    )
    snapshot, materials = await _load_organization_inputs(project_id, organization_version)
    note_aliases = {material.material_id: f"N{index:03d}" for index, material in enumerate(materials, start=1)}

    if not snapshot["planning_groups"]:
        compiler_profile = get_expert_compiler_profile(
            snapshot["compiler_profile_id"],
            validate_assets=False,
        )
        include_asset_nodes = compiler_profile.includes_asset_node_catalog
        if include_asset_nodes:
            # 结构门上线前学习的存量病笔记在这里自动修复；延迟导入避免 service 环。
            from yuxi.expert.service import repair_organization_asset_notes

            materials = await repair_organization_asset_notes(
                project_id,
                organization_version,
                materials,
                note_aliases,
                snapshot,
                tasks,
            )
        grouping = await _plan_groups_with_feedback(
            tasks,
            snapshot=snapshot,
            note_catalog=_build_note_catalog(
                materials,
                note_aliases,
                include_asset_nodes=include_asset_nodes,
            ),
            include_asset_nodes=include_asset_nodes,
            known_aliases=set(note_aliases.values()),
        )
        await _save_planning_groups(project_id, organization_version, grouping, note_aliases)
        snapshot["planning_groups"] = grouping.model_dump()["groups"]

    async with pg_manager.get_async_session_context() as db:
        batches = await ExpertRepository(db).list_research_batches(project_id, organization_version)
    semaphore = asyncio.Semaphore(
        await get_expert_concurrency("expert_organization_research_concurrency", 10)
    )

    async def research(batch_id: str) -> None:
        async with semaphore:
            await _research_one_batch(project_id, organization_version, batch_id, tasks, note_aliases)

    await asyncio.gather(*(research(batch.batch_id) for batch in batches if batch.status in {"pending", "running"}))

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        batches = await repo.list_research_batches(project_id, organization_version)
        failed = [batch for batch in batches if batch.status == "needs_attention"]
        if failed:
            await repo.update_project(
                project_id,
                status="needs_attention",
                message="候选主题研究存在需要处理的范围",
                error_message=failed[0].error_message,
            )
            return {"project_id": project_id, "status": "needs_attention"}
        if any(batch.status != "completed" for batch in batches):
            raise ValueError("候选主题研究尚未全部完成")

    if snapshot["topic_map"]:
        async with pg_manager.get_async_session_context() as db:
            topics = await ExpertRepository(db).list_topics(project_id, organization_version)
        if not topics:
            raise ValueError("主题地图存在但正式主题记录缺失")
    else:
        topic_map = await _edit_topic_map_with_feedback(
            tasks,
            snapshot=snapshot,
            candidate_research=json.dumps(
                [batch.result for batch in batches], ensure_ascii=False, indent=2
            ),
            excluded_materials=_format_excluded_materials(snapshot["excluded_materials"]),
            feedback=snapshot["organization_feedback"],
            known_aliases=set(note_aliases.values()),
        )
        topics = await _save_topic_map(project_id, organization_version, topic_map, note_aliases)
        if topics is None:
            return {"project_id": project_id, "ignored": True}
        if not topics:
            return {
                "project_id": project_id,
                "organization_version": organization_version,
                "status": "needs_attention",
            }

    sample_results = []
    for topic in topics:
        if topic.is_sample:
            if topic.status in {"passed", "needs_attention"}:
                sample_results.append(_serialize_sample_topic(topic))
            else:
                sample_results.append(
                    await write_topic_handout(
                        project_id,
                        organization_version,
                        topic.topic_id,
                        tasks,
                        note_aliases,
                    )
                )

    needs_attention = [item for item in sample_results if item["status"] == "needs_attention"]
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.lock_project(project_id)
        if project is None or project.status != "running" or project.organization_version != organization_version:
            return {"project_id": project_id, "ignored": True}
        project.organization_samples = sample_results
        if needs_attention:
            project.status = "needs_attention"
            project.message = "讲义小样仍有明确问题，请调整组织方式后重新生成"
            project.error_message = needs_attention[0]["reason"]
            result_stage = "organization"
            result_status = "needs_attention"
        elif project.auto_advance:
            topics = await repo.list_topics(project_id, organization_version)
            _start_handout_compilation(project, topics)
            result_stage = "handout_compilation"
            result_status = "running"
        else:
            project.status = "awaiting_confirmation"
            project.message = "主题方案和讲义小样已就绪，等待确认怎么组织"
            project.error_message = None
            project.updated_at = utc_now_naive()
            result_stage = "organization"
            result_status = "awaiting_confirmation"
        await db.flush()
    return {
        "project_id": project_id,
        "organization_version": organization_version,
        "stage": result_stage,
        "status": result_status,
    }


async def restart_organization_stage(
    db: AsyncSession,
    *,
    project_id: str,
    created_by: str,
    feedback: str,
) -> int:
    """记录用户反馈并把当前组织版本交给后台重新生成。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.created_by != created_by:
        raise ValueError("专家项目不存在")
    if project.stage != "organization" or project.status not in {"awaiting_confirmation", "needs_attention"}:
        raise ValueError("当前项目没有可调整的主题组织方案")
    if not str(feedback or "").strip():
        raise ValueError("请说明需要怎样调整组织方式")
    if project.organization_version > 0:
        await repo.replace_research_batches(project_id, project.organization_version, [])
        await repo.replace_topics(project_id, project.organization_version, [])
    project.organization_version += 1
    project.organization_confirmed_at = None
    project.organization_feedback = str(feedback).strip()
    project.planning_groups = []
    project.topic_map = {}
    project.editorial_plan = None
    project.organization_samples = []
    project.status = "running"
    project.message = "正在按反馈重新组织主题和讲义小样"
    project.error_message = None
    project.updated_at = utc_now_naive()
    await db.commit()
    return project.organization_version


async def confirm_organization_stage(db: AsyncSession, *, project_id: str, created_by: str) -> int:
    """第二次用户确认：固定主题地图和编辑方案，进入批量讲义编写。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.created_by != created_by:
        raise ValueError("专家项目不存在")
    if project.stage != "organization" or project.status != "awaiting_confirmation":
        raise ValueError("当前项目没有等待确认的组织方案")
    topics = await repo.list_topics(project_id, project.organization_version)
    _start_handout_compilation(project, topics)
    await db.commit()
    return project.organization_version


def _start_handout_compilation(project: Any, topics: list[Any]) -> None:
    """固定已通过 Review 的组织版本，并进入正式讲义编写。"""

    if not project.topic_map or not (project.editorial_plan or "").strip() or not project.organization_samples:
        raise ValueError("组织方案缺少主题地图、编辑方案或讲义小样")
    samples = [topic for topic in topics if topic.is_sample]
    if not samples or any(topic.status != "passed" for topic in samples):
        raise ValueError("讲义小样尚未全部通过独立 Review")
    for topic in topics:
        if not topic.is_sample:
            topic.status = "pending"
            topic.error_message = None

    project.organization_confirmed_at = utc_now_naive()
    project.stage = "handout_compilation"
    project.status = "running"
    project.message = f"总编模型正在依次定稿 {len(topics)} 篇讲义"
    project.error_message = None
    project.updated_at = utc_now_naive()


async def _load_organization_inputs(project_id: str, organization_version: int) -> tuple[dict, list[Any]]:
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None:
            raise ValueError("专家项目不存在")
        if (
            project.stage != "organization"
            or project.status != "running"
            or project.organization_version != organization_version
        ):
            raise ValueError("当前组织版本已经失效")
        all_materials = await repo.list_project_materials(project_id)
        materials = [material for material in all_materials if material.status == "passed"]
        if not materials:
            raise ValueError("没有通过 Review 的笔记可用于主题组织")
        snapshot = {
            "model_spec": project.model_spec,
            "compiler_profile_id": project.compiler_profile_id,
            "auto_advance": bool(project.auto_advance),
            "learning_plan": project.learning_plan or "",
            "prompt_pack": ProjectPromptPack.model_validate(project.prompt_pack),
            "planning_groups": list(project.planning_groups or []),
            "organization_feedback": project.organization_feedback,
            "topic_map": dict(project.topic_map or {}),
            "excluded_materials": [
                material for material in all_materials if material.status in {"excluded", "no_value"}
            ],
        }
        return snapshot, materials


async def _plan_groups_with_feedback(
    tasks: ExpertModelTasks,
    *,
    snapshot: dict[str, Any],
    note_catalog: str,
    include_asset_nodes: bool,
    known_aliases: set[str],
) -> Any:
    """规划分组带结构校验反馈的有界重试；只有失败的子步骤重跑，已落库产物不受影响。"""

    feedback = ""
    for attempt in range(ORGANIZATION_VALIDATION_FEEDBACK_RETRIES + 1):
        grouping = await tasks.plan_note_groups(
            model_spec=snapshot["model_spec"],
            learning_plan=snapshot["learning_plan"],
            stage_prompt=snapshot["prompt_pack"].topic_researcher,
            note_catalog=note_catalog,
            include_asset_nodes=include_asset_nodes,
            validation_feedback=feedback,
        )
        try:
            _validate_planning_groups(grouping, known_aliases)
            return grouping
        except ValueError as exc:
            feedback = str(exc)
            if attempt == ORGANIZATION_VALIDATION_FEEDBACK_RETRIES:
                raise


async def _edit_topic_map_with_feedback(
    tasks: ExpertModelTasks,
    *,
    snapshot: dict[str, Any],
    candidate_research: str,
    excluded_materials: str,
    feedback: str,
    known_aliases: set[str],
) -> Any:
    """主题地图带结构校验反馈的有界重试；校验错误直接喂回总编模型而不是判死阶段。"""

    validation_feedback = ""
    for attempt in range(ORGANIZATION_VALIDATION_FEEDBACK_RETRIES + 1):
        topic_map = await tasks.edit_topic_map(
            model_spec=snapshot["model_spec"],
            learning_plan=snapshot["learning_plan"],
            stage_prompt=snapshot["prompt_pack"].topic_editor,
            candidate_research=candidate_research,
            excluded_materials=excluded_materials,
            feedback=feedback,
            validation_feedback=validation_feedback,
        )
        try:
            _validate_topic_map(topic_map, known_aliases)
            return topic_map
        except ValueError as exc:
            validation_feedback = "\n".join(
                (
                    str(exc),
                    _TOPIC_MAP_FEEDBACK_CATALOG.format(aliases=", ".join(sorted(known_aliases))),
                )
            )
            if attempt == ORGANIZATION_VALIDATION_FEEDBACK_RETRIES:
                raise


async def _save_planning_groups(
    project_id: str,
    organization_version: int,
    grouping: PlanningGroupsResult,
    note_aliases: dict[str, str],
) -> None:
    material_by_alias = {alias: material_id for material_id, alias in note_aliases.items()}
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.lock_project(project_id)
        if project is None or project.status != "running" or project.organization_version != organization_version:
            return
        project.planning_groups = grouping.model_dump()["groups"]
        await repo.replace_research_batches(
            project_id,
            organization_version,
            [
                {
                    "batch_id": uuid.uuid4().hex,
                    "group_key": group.group_key,
                    "title": group.title,
                    "rationale": group.rationale,
                    "material_ids": [material_by_alias[alias] for alias in group.note_aliases],
                    "status": "pending",
                }
                for group in grouping.groups
            ],
        )


async def _research_one_batch(
    project_id: str,
    organization_version: int,
    batch_id: str,
    tasks: ExpertModelTasks,
    note_aliases: dict[str, str],
) -> None:
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        batch = await repo.lock_research_batch(batch_id)
        if (
            project is None
            or batch is None
            or project.status != "running"
            or project.organization_version != organization_version
            or batch.status not in {"pending", "running"}
        ):
            return
        materials = await repo.list_project_materials(project_id)
        selected = [material for material in materials if material.material_id in set(batch.material_ids or [])]
        batch.status = "running"
        batch.error_message = None
        snapshot = {
            "model_spec": project.model_spec,
            "learning_plan": project.learning_plan or "",
            "stage_prompt": ProjectPromptPack.model_validate(project.prompt_pack).topic_researcher,
            "group_key": batch.group_key,
            "rationale": batch.rationale,
            "notes": format_full_notes(selected, note_aliases),
        }
        await db.commit()

    result, error = await _call_twice(
        lambda: tasks.research_topic_candidates(
            model_spec=snapshot["model_spec"],
            learning_plan=snapshot["learning_plan"],
            stage_prompt=snapshot["stage_prompt"],
            group_key=snapshot["group_key"],
            group_rationale=snapshot["rationale"],
            notes=snapshot["notes"],
        )
    )
    if result is not None:
        try:
            _validate_topic_research(
                result,
                snapshot["group_key"],
                {note_aliases[item] for item in batch.material_ids},
            )
        except ValueError as exc:
            result = None
            error = exc
    if result is None and is_transient_expert_error(error):
        # 瞬时故障交给任务级重试与错峰重投，不沉淀为批次 needs_attention。
        raise error
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        batch = await repo.lock_research_batch(batch_id)
        if project is None or project.status != "running" or batch is None or batch.status != "running":
            return
        if result is None:
            batch.status = "needs_attention"
            batch.error_message = str(error)
            return
        batch.status = "completed"
        batch.result = result.model_dump()
        batch.error_message = None


async def _save_topic_map(
    project_id: str,
    organization_version: int,
    topic_map: TopicMapResult,
    note_aliases: dict[str, str],
) -> list[Any] | None:
    material_by_alias = {alias: material_id for material_id, alias in note_aliases.items()}
    sample_keys = set(topic_map.sample_topic_keys)
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.lock_project(project_id)
        if project is None or project.status != "running" or project.organization_version != organization_version:
            return None
        project.topic_map = topic_map.model_dump()
        project.editorial_plan = topic_map.editorial_plan_markdown
        topics = await repo.replace_topics(
            project_id,
            organization_version,
            [
                {
                    "topic_id": uuid.uuid4().hex,
                    "topic_key": topic.topic_key,
                    "title": topic.title,
                    "section": topic.section,
                    "reader_question": topic.reader_question,
                    "purpose": topic.purpose,
                    "boundaries": topic.boundaries,
                    "position": topic.order,
                    "material_ids": [material_by_alias[alias] for alias in topic.note_aliases],
                    "evidence_requirements": topic.unresolved_questions,
                    "is_sample": topic.topic_key in sample_keys,
                    "status": "pending",
                }
                for topic in topic_map.topics
            ],
        )
        if not topics:
            project.organization_samples = []
            project.status = "needs_attention"
            project.message = "总编未形成可用于成书的主题"
            project.error_message = "全部通过 Review 的笔记均被总编判定不适合纳入主题，无法形成可信专家 Skill"
            project.updated_at = utc_now_naive()
        return topics


async def prepare_topic_evidence(
    project_id: str,
    organization_version: int,
    topic_id: str,
    note_aliases: dict[str, str],
) -> dict[str, Any]:
    """用通过笔记的原文依据锚点机械化组装证据包，不经模型调用。"""

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        topic = await repo.lock_topic(topic_id)
        if (
            project is None
            or project.status != "running"
            or topic is None
            or project.organization_version != organization_version
        ):
            return {"topic_id": topic_id, "status": "needs_attention", "reason": "组织版本已经变化"}
        if topic.status == "passed":
            return _serialize_sample_topic(topic)
        materials = await repo.list_project_materials(project_id)
        selected = [material for material in materials if material.material_id in set(topic.material_ids or [])]
        allowed_materials = {note_aliases[item.material_id]: item for item in selected}
        topic.status = "preparing_evidence"
        topic.error_message = None
        await db.commit()

    try:
        evidence_pack = _build_topic_evidence_pack(topic.topic_key, selected, note_aliases)
        _validate_evidence_pack(evidence_pack, topic.topic_key, allowed_materials)
    except Exception as exc:  # noqa: BLE001
        if is_transient_expert_error(exc):
            raise
        return await _save_topic_failure(topic_id, str(exc))

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        topic = await repo.lock_topic(topic_id)
        if project is None or project.status != "running" or topic is None or topic.status != "preparing_evidence":
            return {"topic_id": topic_id, "status": "needs_attention", "reason": "主题状态已经变化"}
        topic.status = "evidence_ready"
        topic.evidence_pack = evidence_pack.model_dump()
        topic.error_message = None
        return {"topic_id": topic_id, "status": "evidence_ready", "reason": ""}


async def write_topic_handout(
    project_id: str,
    organization_version: int,
    topic_id: str,
    tasks: ExpertModelTasks,
    note_aliases: dict[str, str],
) -> dict[str, Any]:
    """由项目内串行总编形成、Review 并至多修订一次正式讲义。"""

    async with pg_manager.get_async_session_context() as db:
        topic = await ExpertRepository(db).lock_topic(topic_id)
        if topic is None:
            return {"topic_id": topic_id, "status": "needs_attention", "reason": "主题不存在"}
        if topic.status == "passed":
            return _serialize_sample_topic(topic)
        if topic.status == "writing" and topic.evidence_pack:
            topic.status = "evidence_ready"
        needs_evidence = topic.status in {"pending", "preparing_evidence"}

    if needs_evidence:
        evidence_result = await prepare_topic_evidence(
            project_id,
            organization_version,
            topic_id,
            note_aliases,
        )
        if evidence_result["status"] != "evidence_ready":
            return evidence_result

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        topic = await repo.lock_topic(topic_id)
        if (
            project is None
            or project.status != "running"
            or topic is None
            or project.organization_version != organization_version
        ):
            return {"topic_id": topic_id, "status": "needs_attention", "reason": "组织版本已经变化"}
        if topic is None or topic.status != "evidence_ready" or not topic.evidence_pack:
            return {"topic_id": topic_id, "status": "needs_attention", "reason": "主题证据包尚未准备完成"}
        materials = await repo.list_project_materials(project_id)
        selected = [material for material in materials if material.material_id in set(topic.material_ids or [])]
        source_texts = await load_expert_material_source_texts(selected)
        topic.status = "writing"
        snapshot = {
            "model_spec": project.model_spec,
            "learning_plan": project.learning_plan or "",
            "prompt_pack": ProjectPromptPack.model_validate(project.prompt_pack),
            "editorial_plan": project.editorial_plan or "",
            "topic": format_topic_definition(topic),
            "topic_key": topic.topic_key,
            "evidence_pack": TopicEvidencePack.model_validate(topic.evidence_pack),
        }
        topic.error_message = None
        snapshot["notes"] = format_handout_notes_with_evidence(
            project_id,
            selected,
            note_aliases,
            snapshot["evidence_pack"],
            source_texts,
        )
        await db.commit()

    evidence_pack = snapshot["evidence_pack"]
    try:
        handout = await tasks.write_handout(
            model_spec=snapshot["model_spec"],
            learning_plan=snapshot["learning_plan"],
            stage_prompt=snapshot["prompt_pack"].handout_editor,
            editorial_plan=snapshot["editorial_plan"],
            topic=snapshot["topic"],
            notes=snapshot["notes"],
            evidence_pack=evidence_pack,
        )
        handout = reconcile_citations_with_evidence(handout, evidence_pack, snapshot["topic_key"])
        validate_handout_contract(handout, snapshot["topic_key"], evidence_pack)
        review = await tasks.review_handout(
            model_spec=snapshot["model_spec"],
            learning_plan=snapshot["learning_plan"],
            stage_prompt=snapshot["prompt_pack"].handout_reviewer,
            editorial_plan=handout.editorial_plan_markdown,
            topic=snapshot["topic"],
            notes=snapshot["notes"],
            evidence_pack=evidence_pack,
            handout=handout,
        )
        validate_handout_review(review)
        revision = None
        final_review = review
        if review.decision == "revise":
            revision = await tasks.revise_handout(
                model_spec=snapshot["model_spec"],
                learning_plan=snapshot["learning_plan"],
                stage_prompt=snapshot["prompt_pack"].handout_editor,
                editorial_plan=handout.editorial_plan_markdown,
                topic=snapshot["topic"],
                notes=snapshot["notes"],
                evidence_pack=evidence_pack,
                handout=handout,
                review=review,
            )
            validate_handout_contract(revision, snapshot["topic_key"], evidence_pack)
            revision = reconcile_citations_with_evidence(revision, evidence_pack, snapshot["topic_key"])
            final_review = await tasks.review_handout(
                model_spec=snapshot["model_spec"],
                learning_plan=snapshot["learning_plan"],
                stage_prompt=snapshot["prompt_pack"].handout_reviewer,
                editorial_plan=revision.editorial_plan_markdown,
                topic=snapshot["topic"],
                notes=snapshot["notes"],
                evidence_pack=evidence_pack,
                handout=revision,
            )
            validate_handout_review(final_review)
    except Exception as exc:  # noqa: BLE001
        if is_transient_expert_error(exc):
            # 瞬时故障（429/断连/超时）交给任务级重试与错峰重投；主题保持 writing 以便续跑，
            # 不能把传输错误吞成主题级永久失败。
            raise
        return await _save_topic_failure(topic_id, str(exc))

    final_handout = revision or handout
    status = "passed" if final_review.decision == "pass" else "needs_attention"
    reason = "" if status == "passed" else final_review.summary
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.lock_project(project_id)
        topic = await repo.lock_topic(topic_id)
        if (
            project is None
            or topic is None
            or project.status != "running"
            or project.organization_version != organization_version
            or project.stage not in {"organization", "handout_compilation"}
            or topic.status != "writing"
        ):
            return {"topic_id": topic_id, "status": "needs_attention", "reason": "主题状态已经变化"}
        topic.status = status
        topic.handout = final_handout.handout_markdown
        topic.handout_contract = final_handout.model_dump()
        topic.review = final_review.model_dump()
        # 修订次数按累计口径保留：重跑只重写讲义，不重置全书修订预算。
        topic.revision_count = max(topic.revision_count, 1 if revision else 0)
        topic.error_message = reason or None
        if status == "passed":
            project.editorial_plan = final_handout.editorial_plan_markdown
            project.updated_at = utc_now_naive()
        return {
            "topic_id": topic.topic_id,
            "topic_key": topic.topic_key,
            "title": topic.title,
            "status": status,
            "handout": topic.handout,
            "review": topic.review,
            "reason": reason,
        }


async def _save_topic_failure(topic_id: str, error: str) -> dict[str, Any]:
    async with pg_manager.get_async_session_context() as db:
        topic = await ExpertRepository(db).lock_topic(topic_id)
        if topic is not None:
            topic.status = "needs_attention"
            topic.error_message = error
    return {"topic_id": topic_id, "status": "needs_attention", "reason": error}


async def _call_twice[ResultT](
    factory: Callable[[], Awaitable[ResultT]],
) -> tuple[ResultT | None, Exception | None]:
    error = None
    for attempt in range(2):
        try:
            return await factory(), None
        except Exception as exc:  # noqa: BLE001
            error = exc
            if attempt == 0:
                await asyncio.sleep(2)
    return None, error


def _validate_planning_groups(result: PlanningGroupsResult, known_aliases: set[str]) -> None:
    if result.unresolved:
        raise ValueError("模型无法为全部笔记形成有意义的规划范围: " + "；".join(result.unresolved))
    if not result.groups:
        raise ValueError("主题规划没有形成任何有意义分组")
    group_keys = [group.group_key for group in result.groups]
    if len(group_keys) != len(set(group_keys)):
        raise ValueError("主题规划分组标识重复")
    covered = set()
    for group in result.groups:
        aliases = set(group.note_aliases)
        if not aliases or not aliases <= known_aliases:
            raise ValueError("主题规划分组引用了未知或空的笔记范围")
        covered.update(aliases)
    if covered != known_aliases:
        raise ValueError("主题规划没有覆盖全部已通过笔记")


def _validate_topic_research(result: TopicResearchResult, group_key: str, allowed_aliases: set[str]) -> None:
    if result.group_key != group_key:
        raise ValueError("候选主题研究返回了错误的规划范围")
    for candidate in result.candidates:
        if not set(candidate.note_aliases) <= allowed_aliases:
            raise ValueError("候选主题引用了当前规划范围以外的笔记")


def _validate_topic_map(result: TopicMapResult, known_aliases: set[str]) -> None:
    if not result.plan_markdown.strip() or not result.editorial_plan_markdown.strip():
        raise ValueError("总编主题方案缺少可确认的规划或编辑方案")
    topic_keys = [topic.topic_key for topic in result.topics]
    if len(topic_keys) != len(set(topic_keys)):
        raise ValueError("总编主题标识重复")
    if result.topics:
        if not 1 <= len(result.sample_topic_keys) <= 2 or not set(result.sample_topic_keys) <= set(topic_keys):
            raise ValueError("总编必须从正式主题中选择 1～2 个讲义小样")
    elif result.sample_topic_keys:
        raise ValueError("没有正式主题时不能选择讲义小样")

    assigned = set()
    for topic in result.topics:
        aliases = set(topic.note_aliases)
        if not aliases or not aliases <= known_aliases:
            raise ValueError("正式主题引用了未知或空的笔记范围")
        assigned.update(aliases)
    unassigned_aliases = [item.note_alias for item in result.unassigned_notes]
    if len(unassigned_aliases) != len(set(unassigned_aliases)):
        raise ValueError("同一笔记不能重复出现在未分配清单")
    if any(not item.reason.strip() for item in result.unassigned_notes):
        raise ValueError("未分配笔记必须说明不采用原因")
    unassigned = set(unassigned_aliases)
    if assigned & unassigned:
        raise ValueError("同一笔记不能同时进入正式主题和未分配清单")
    if not unassigned <= known_aliases:
        unknown = ", ".join(sorted(unassigned - known_aliases))
        raise ValueError(f"未分配清单引用了未知笔记：{unknown}")
    missing = known_aliases - assigned - unassigned
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(
            f"主题地图没有明确交代全部已通过笔记：{missing_list} "
            f"既没有进入任何正式主题，也没有列入 unassigned_notes 说明原因"
        )


def _build_topic_evidence_pack(topic_key: str, materials: list[Any], aliases: dict[str, str]) -> TopicEvidencePack:
    """把各份通过笔记的原文依据锚点原样汇成证据包，不做选择、扩写或新增判断。"""

    evidence = [
        TopicEvidenceItem(
            note_alias=aliases[material.material_id],
            start_line=int(anchor["start_line"]),
            end_line=int(anchor["end_line"]),
            supports=str(anchor.get("supports") or ""),
        )
        for material in materials
        for anchor in (material.note_contract or {}).get("evidence") or []
    ]
    return TopicEvidencePack(topic_key=topic_key, evidence=evidence)


def _validate_evidence_pack(pack: TopicEvidencePack, topic_key: str, materials: dict[str, Any]) -> None:
    if pack.topic_key != topic_key or not pack.evidence:
        raise ValueError("主题证据包缺少当前主题的原文依据")
    for item in pack.evidence:
        material = materials.get(item.note_alias)
        if material is None or item.start_line > item.end_line or item.end_line > material.line_count:
            raise ValueError("主题证据引用超出获准材料范围")
        anchors = (material.note_contract or {}).get("evidence") or []
        covered_lines = {
            line_number
            for anchor in anchors
            for line_number in range(
                int(anchor.get("start_line") or 0),
                int(anchor.get("end_line") or 0) + 1,
            )
        }
        if not set(range(item.start_line, item.end_line + 1)) <= covered_lines:
            raise ValueError("主题证据没有落在已通过笔记的原文依据内")


def reconcile_citations_with_evidence(
    handout: HandoutDraftResult,
    evidence_pack: TopicEvidencePack,
    topic_key: str,
) -> HandoutDraftResult:
    """讲义引用与获准证据包的确定性对齐。

    证据包是获准原文锚点的权威事实；模型抄写行号时的越界引用按逐字 supports
    匹配回写到正确锚点，避免一次行号笔误烧掉整篇讲义的重试预算。
    """

    anchor_lines_by_alias: dict[str, set[int]] = {}
    exact_by_key: dict[tuple[str, str], tuple[int, int]] = {}
    for item in evidence_pack.evidence:
        lines = anchor_lines_by_alias.setdefault(item.note_alias, set())
        lines.update(range(item.start_line, item.end_line + 1))
        exact_by_key.setdefault((item.note_alias, item.supports.strip()), (item.start_line, item.end_line))

    changed = False
    citations = []
    for citation in handout.citations:
        citation_lines = set(range(citation.start_line, citation.end_line + 1))
        if citation_lines <= anchor_lines_by_alias.get(citation.note_alias, set()):
            citations.append(citation)
            continue
        exact = exact_by_key.get((citation.note_alias, citation.supports.strip()))
        if exact is not None:
            citation = citation.model_copy(update={"start_line": exact[0], "end_line": exact[1]})
            changed = True
        citations.append(citation)
    if not changed:
        return handout
    return handout.model_copy(update={"citations": citations, "topic_key": topic_key})


def validate_handout_contract(
    handout: HandoutDraftResult,
    topic_key: str,
    evidence_pack: TopicEvidencePack,
) -> None:
    if handout.topic_key != topic_key or not handout.handout_markdown.strip() or not handout.citations:
        raise ValueError("讲义候选缺少正文或关键判断引用")
    allowed_lines: dict[str, set[int]] = {}
    for item in evidence_pack.evidence:
        allowed_lines.setdefault(item.note_alias, set()).update(range(item.start_line, item.end_line + 1))
    for citation in handout.citations:
        citation_lines = set(range(citation.start_line, citation.end_line + 1))
        if not citation_lines <= allowed_lines.get(citation.note_alias, set()):
            raise ValueError("讲义引用了证据包以外的材料范围")


def validate_handout_review(review: HandoutReviewResult) -> None:
    if review.decision == "revise" and not review.issues:
        raise ValueError("讲义 Review 要求修订但没有给出具体问题")


def _build_note_catalog(
    materials: list[Any],
    aliases: dict[str, str],
    *,
    include_asset_nodes: bool = False,
) -> str:
    """组装全局笔记目录，并按编译方法透传确定性资产节点。"""

    rows = []
    for material in materials:
        routing_labels = (material.note_contract or {}).get("routing_labels") or []
        row = (
            f"{aliases[material.material_id]} | 来源：{material.source_path} | "
            f"Labels：{'；'.join(routing_labels) or '未提供'}"
        )
        if include_asset_nodes:
            validate_asset_note_structure(material.note or "", material_alias=aliases[material.material_id])
            asset_nodes = extract_asset_node_catalog(material.note or "")
            row += f" | 资产节点：{'；'.join(asset_nodes) or '未提供'}"
        rows.append(row)
    return "\n".join(rows)


def extract_asset_node_catalog(note_markdown: str) -> list[str]:
    """提取二级“资产节点目录”区块中的 Markdown 列表项。"""

    nodes: list[str] = []
    in_asset_catalog = False
    for raw_line in str(note_markdown or "").splitlines():
        line = raw_line.strip()
        if not in_asset_catalog:
            in_asset_catalog = line == "## 资产节点目录"
            continue
        if line.startswith("# ") or line.startswith("## "):
            break
        if line.startswith(("- ", "* ", "+ ")):
            nodes.append(line[2:].strip())
            continue
        marker, separator, content = line.partition(" ")
        if separator and marker[:-1].isdigit() and marker[-1:] in {".", ")"}:
            nodes.append(content.strip())
    return nodes


_ASSET_NODE_ENTRY_PATTERN = re.compile(
    r"^(?P<node_id>N\d{2}) \[(?P<node_type>[^[\]]+)\]\[(?P<source_label>[^[\]]+)\] .+$"
)
_ASSET_NODE_ENTRY_LOOSE_PATTERN = re.compile(
    r"^(?P<prefix>[A-Za-z])(?P<digits>\d{2}) \[(?P<node_type>[^[\]]+)\]\[(?P<source_label>[^[\]]+)\] .+$"
)
_ASSET_BODY_HEADING_PATTERN = re.compile(
    r"^(?P<node_id>N\d{2}) \[(?P<node_type>[^[\]]+)\]\[(?P<source_label>[^[\]]+)\](?: (?P<trailing>.+))?$"
)
_ASSET_BODY_HEADING_LOOSE_PATTERN = re.compile(r"^(?P<node_id>N\d{2})(?P<rest>.*)$")


_ASSET_NODE_PREFIX_PATTERN = re.compile(r"^(#+\s+|(?:[-*+]\s+)?)([A-Za-z])(\d{2})\b")


def _canonicalize_asset_node_prefix(text: str) -> str:
    """把模型自创的节点编号前缀（A01/B01 等）机械归一为 N 序列。"""

    return _ASSET_NODE_PREFIX_PATTERN.sub(
        lambda m: f"{m.group(1)}N{m.group(3)}" if m.group(2) != "N" else m.group(0), text
    )


def normalize_asset_note_markdown(note_markdown: str, *, material_alias: str) -> str:
    """机械修复资产笔记的确定性结构偏差。

    覆盖三类偏差：目录条目与正文标题的编号前缀漂移（A01/B01 归一为 N01）、
    正文标题缺失或多余的节点类型/来源标签（按同 ID 目录条目补齐或修剪）、
    标题标签后保留模型自然写出的短语。节点集合或顺序不一致不在此修复，
    仍由结构校验 fail-fast 交回内容修订。
    """

    lines = [
        _canonicalize_asset_node_prefix(raw_line) if raw_line.lstrip().startswith(("#", "- ", "* ", "+ ")) else raw_line
        for raw_line in str(note_markdown or "").splitlines()
    ]
    catalog_by_id: dict[str, tuple[str, str]] = {}
    in_catalog = False
    for raw_line in lines:
        line = raw_line.strip()
        if not in_catalog:
            in_catalog = line == "## 资产节点目录"
            continue
        if line.startswith("# "):
            break
        if line.startswith("## "):
            in_catalog = False
            continue
        if not line.startswith(("- ", "* ", "+ ")):
            continue
        entry_match = _ASSET_NODE_ENTRY_PATTERN.fullmatch(line[2:].strip())
        if entry_match is None:
            continue
        catalog_by_id.setdefault(
            entry_match["node_id"], (entry_match["node_type"], entry_match["source_label"])
        )

    if not catalog_by_id:
        return "\n".join(lines)

    normalized: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped.startswith("### "):
            normalized.append(raw_line)
            continue
        heading = stripped[4:].strip()
        loose = _ASSET_BODY_HEADING_LOOSE_PATTERN.fullmatch(heading)
        if loose is None or loose["node_id"] not in catalog_by_id:
            normalized.append(raw_line)
            continue
        node_id = loose["node_id"]
        node_type, source_label = catalog_by_id[node_id]
        canonical = f"{node_id} [{node_type}][{source_label}]"
        if heading == canonical:
            normalized.append(raw_line)
            continue
        trailing_text = re.sub(r"^(?:\s*\[[^[\]]*\])+", "", loose["rest"]).strip()
        normalized.append(f"### {canonical}" + (f" {trailing_text}" if trailing_text else ""))
    return "\n".join(normalized)


def validate_asset_note_structure(note_markdown: str, *, material_alias: str) -> None:
    """校验领域推导资产的结构合同：节点单来源标签，目录与正文三级标题同序一一对应。"""

    catalog_entries: list[tuple[str, str, str]] = []
    body_nodes: list[tuple[str, str, str]] = []
    in_catalog = False
    for raw_line in str(note_markdown or "").splitlines():
        line = raw_line.strip()
        if line.startswith("### "):
            heading = _ASSET_BODY_HEADING_PATTERN.fullmatch(line[4:].strip())
            if heading is not None:
                body_nodes.append((heading["node_id"], heading["node_type"], heading["source_label"]))
            continue
        if not in_catalog:
            in_catalog = line == "## 资产节点目录"
            continue
        if line.startswith("# "):
            break
        if line.startswith("## "):
            in_catalog = False
            continue
        if line.startswith(("- ", "* ", "+ ")):
            entry = line[2:].strip()
        else:
            marker, separator, content = line.partition(" ")
            if not (separator and marker[:-1].isdigit() and marker[-1:] in {".", ")"}):
                continue
            entry = content.strip()
        node_match = _ASSET_NODE_ENTRY_PATTERN.fullmatch(entry)
        if node_match is None:
            raise ValueError(
                f"{material_alias} 的资产节点目录条目不符合 'N01 [类型][单一来源标签] 单行摘要' 结构: {entry}"
            )
        catalog_entries.append((node_match["node_id"], node_match["node_type"], node_match["source_label"]))

    if not catalog_entries:
        raise ValueError(f"{material_alias} 的领域推导资产缺少可解析的资产节点目录")
    if len({node_id for node_id, _, _ in catalog_entries}) != len(catalog_entries):
        raise ValueError(f"{material_alias} 的资产节点目录存在重复节点 ID")
    if catalog_entries != body_nodes:
        catalog_ids = [node_id for node_id, _, _ in catalog_entries]
        body_ids = [node_id for node_id, _, _ in body_nodes]
        raw_body_headings = [
            line.strip()[4:]
            for line in str(note_markdown or "").splitlines()
            if line.strip().startswith("### ")
        ]
        first_heading = f"，正文首个标题: '{raw_body_headings[0]}'" if raw_body_headings else ""
        raise ValueError(
            f"{material_alias} 的资产节点目录与资产正文三级标题必须同序一一对应: "
            f"目录 {catalog_ids} vs 正文 {body_ids}{first_heading}"
        )


def format_full_notes(materials: list[Any], aliases: dict[str, str]) -> str:
    """组装当前主题获准使用的完整已审笔记。"""

    sections = []
    for material in materials:
        alias = aliases[material.material_id]
        note_contract = dict(material.note_contract or {})
        note_contract["evidence"] = [
            {**anchor, "material_alias": alias} for anchor in note_contract.get("evidence") or []
        ]
        sections.append(
            f"## {alias} · {material.title}\n"
            f"来源：{material.source_path}\n\n{material.note or ''}\n\n"
            f"笔记响应：\n{json.dumps(note_contract, ensure_ascii=False, indent=2)}"
        )
    return "\n\n".join(sections)


def format_handout_notes_with_evidence(
    project_id: str,
    materials: list[Any],
    aliases: dict[str, str],
    evidence_pack: TopicEvidencePack,
    source_texts: dict[str, str] | None = None,
) -> str:
    """把完整已审笔记与证据包指定的可靠正文原文组装为讲义输入。"""

    material_by_alias = {aliases[material.material_id]: material for material in materials}
    source_texts = source_texts or {
        material.material_id: read_legacy_material_text(project_id, material.text_path) for material in materials
    }
    evidence_sections = []
    for item in evidence_pack.evidence:
        material = material_by_alias.get(item.note_alias)
        if material is None:
            raise ValueError("主题证据引用了当前主题范围外的材料")
        excerpt = format_expert_material_excerpt(
            material,
            source_texts[material.material_id],
            start_line=item.start_line,
            end_line=item.end_line,
        )
        evidence_sections.append(
            f"### {item.note_alias} · {material.title} · L{item.start_line}-L{item.end_line}\n"
            f"来源：{material.source_path}\n\n{excerpt}"
        )

    return (
        f"# 完整已审笔记\n\n{format_full_notes(materials, aliases)}\n\n"
        f"# 当前主题获准原文摘录\n\n{'\n\n'.join(evidence_sections)}"
    )


def _format_excluded_materials(materials: list[Any]) -> str:
    return "\n".join(
        f"- {material.title}（{material.source_path}）："
        f"{material.exclusion_reason or material.error_message or 'Review 判定不入选'}"
        for material in materials
    )


def format_topic_definition(topic: Any) -> str:
    return json.dumps(
        {
            "topic_key": topic.topic_key,
            "title": topic.title,
            "section": topic.section,
            "reader_question": topic.reader_question,
            "purpose": topic.purpose,
            "boundaries": topic.boundaries,
            "evidence_requirements": topic.evidence_requirements or [],
            "retry_feedback": topic.error_message,
            "position": topic.position,
        },
        ensure_ascii=False,
        indent=2,
    )


def _serialize_sample_topic(topic: Any) -> dict[str, Any]:
    return {
        "topic_id": topic.topic_id,
        "topic_key": topic.topic_key,
        "title": topic.title,
        "status": topic.status,
        "handout": topic.handout,
        "review": topic.review,
        "reason": topic.error_message or "",
    }


__all__ = [
    "confirm_organization_stage",
    "format_full_notes",
    "extract_asset_node_catalog",
    "format_handout_notes_with_evidence",
    "format_topic_definition",
    "initialize_organization_stage",
    "prepare_topic_evidence",
    "restart_organization_stage",
    "run_organization_stage",
    "validate_handout_contract",
    "validate_handout_review",
    "write_topic_handout",
]
