"""专家项目正式讲义、全书收口与候选 Skill 编译。"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.skills.repository import SkillRepository
from yuxi.expert.concurrency import get_expert_concurrency
from yuxi.agents.skills.service import (
    delete_skill,
    import_skill_dir,
    replace_skill_dir,
    update_skill_enabled,
    user_can_manage_skill,
)
from yuxi.expert.contracts import (
    BookReviewResult,
    HandoutDraftResult,
    HandoutReviewResult,
    ProjectPromptPack,
    ReviewIssue,
    TopicEvidencePack,
)
from yuxi.expert.materials import load_expert_material_source_texts
from yuxi.expert.model_tasks import ExpertContextLimitError, ExpertModelTasks
from yuxi.expert.organization_service import (
    format_handout_notes_with_evidence,
    format_topic_definition,
    prepare_topic_evidence,
    validate_handout_contract,
    validate_handout_review,
    write_topic_handout,
)
from yuxi.expert.publisher import build_candidate_skill, get_project_skill_slug, validate_candidate_skill
from yuxi.expert.service import trim_expert_project_activities
from yuxi.expert.storage import get_project_root
from yuxi.repositories.expert_repository import ExpertRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.utils.datetime_utils import utc_now_naive

MAX_HANDOUT_REVISIONS = 2


async def activate_expert_skill(
    db: AsyncSession,
    *,
    project_id: str,
    current_user: User,
) -> dict[str, Any]:
    """在用户预览后安装或替换当前项目的候选专家 Skill。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.created_by != str(current_user.uid):
        raise ValueError("专家项目不存在")
    if project.stage != "release" or project.status != "ready" or not project.skill_dir or not project.skill_slug:
        raise ValueError("专家 Skill 成品尚未准备完成")

    source = (get_project_root(project_id) / project.skill_dir).resolve()
    validate_candidate_skill(source)
    installed = await SkillRepository(db).get_by_slug(project.skill_slug)
    if installed is not None and not user_can_manage_skill(current_user, installed):
        raise ValueError("同名 Skill 已存在且当前用户无权替换")

    with tempfile.TemporaryDirectory(prefix="expert-skill-activate-") as temp_dir:
        staged = Path(temp_dir) / project.skill_slug
        await asyncio.to_thread(shutil.copytree, source, staged)
        if installed is None:
            installed = await import_skill_dir(
                db,
                source_dir=staged,
                created_by=str(current_user.uid),
            )
        else:
            installed = await replace_skill_dir(
                db,
                slug=project.skill_slug,
                source_dir=staged,
                updated_by=str(current_user.uid),
                version=str(project.organization_version),
            )
            if not installed.enabled:
                installed = await update_skill_enabled(
                    db,
                    slug=installed.slug,
                    enabled=True,
                    operator=current_user,
                )

    project = await repo.lock_project(project_id)
    if project is None:
        raise ValueError("专家项目不存在")
    project.status = "enabled"
    project.message = "当前专家 Skill 已启用"
    project.error_message = None
    project.updated_at = utc_now_naive()
    await db.commit()
    await trim_expert_project_activities(project_id)
    return installed.to_dict()


async def uninstall_expert_skill(
    db: AsyncSession,
    *,
    project_id: str,
    current_user: User,
) -> Any:
    """复用通用 Skill 删除后，把项目恢复为可再次启用的候选成品。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.created_by != str(current_user.uid):
        raise ValueError("专家项目不存在")
    if project.status != "enabled" or not project.skill_slug:
        raise ValueError("当前项目没有已启用的专家 Skill")
    installed = await SkillRepository(db).get_by_slug(project.skill_slug)
    if installed is None:
        raise ValueError("已启用的专家 Skill 不存在")
    if not user_can_manage_skill(current_user, installed):
        raise ValueError("当前用户无权卸载专家 Skill")

    project.status = "ready"
    project.message = "专家 Skill 已卸载，可再次启用候选成品"
    project.error_message = None
    project.updated_at = utc_now_naive()
    await delete_skill(db, slug=project.skill_slug)
    return project


async def get_candidate_preview(
    db: AsyncSession,
    *,
    project_id: str,
    created_by: str,
) -> dict[str, Any]:
    """返回最终候选 Skill 中可发布成品和未纳入材料，不暴露过程文件。"""

    repo = ExpertRepository(db)
    project = await repo.get_project(project_id, created_by=created_by)
    if project is None:
        raise ValueError("专家项目不存在")
    if project.stage != "release" or project.status not in {"ready", "enabled"} or not project.skill_dir:
        raise ValueError("专家 Skill 成品尚未准备完成")
    root = (get_project_root(project_id) / project.skill_dir).resolve()
    validate_candidate_skill(root)
    materials = await repo.list_project_materials(project_id)
    excluded_materials = [
        {
            "material_id": material.material_id,
            "title": material.title,
            "source_path": material.source_path,
            "reason": material.exclusion_reason or material.error_message or "Review 判定不入选",
        }
        for material in materials
        if material.status in {"excluded", "no_value"}
    ]
    passed_materials = [material for material in materials if material.status == "passed"]
    material_by_note_alias = {f"N{index:03d}": material for index, material in enumerate(passed_materials, start=1)}
    for item in (project.topic_map or {}).get("unassigned_notes") or []:
        material = material_by_note_alias.get(str(item.get("note_alias") or ""))
        reason = str(item.get("reason") or "").strip()
        if material is None or not reason:
            raise ValueError("候选 Skill 的未纳入笔记记录无法映射到当前材料")
        excluded_materials.append(
            {
                "material_id": material.material_id,
                "title": material.title,
                "source_path": material.source_path,
                "reason": reason,
            }
        )

    return {
        "skill_md": (root / "SKILL.md").read_text(encoding="utf-8"),
        "navigation": (root / "nav" / "index.md").read_text(encoding="utf-8"),
        "handouts": [
            {"path": path.relative_to(root).as_posix(), "content": path.read_text(encoding="utf-8")}
            for path in sorted((root / "wiki").glob("*.md"))
        ],
        "references": [
            {"path": path.relative_to(root).as_posix(), "content": path.read_text(encoding="utf-8")}
            for path in sorted((root / "references").glob("*.md"))
        ],
        "sources": [
            json.loads(line)
            for line in (root / "sources.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ],
        "excluded_materials": excluded_materials,
    }


async def run_handout_compilation(
    project_id: str,
    organization_version: int,
    *,
    model_tasks: ExpertModelTasks | None = None,
) -> dict[str, Any]:
    """并发准备证据、串行定稿讲义，再完成全书收口和候选 Skill。"""

    tasks = model_tasks or ExpertModelTasks(
        prompt_cache_namespace=f"expert:{project_id}:organization:{organization_version}"
    )
    snapshot, materials, topics = await _load_compilation_inputs(project_id, organization_version)
    note_aliases = {
        material.material_id: f"N{index:03d}"
        for index, material in enumerate(
            [item for item in materials if item.status == "passed"],
            start=1,
        )
    }

    semaphore = asyncio.Semaphore(
        await get_expert_concurrency("expert_evidence_preparation_concurrency", 10)
    )

    async def prepare(topic_id: str) -> None:
        async with semaphore:
            await prepare_topic_evidence(
                project_id,
                organization_version,
                topic_id,
                note_aliases,
            )

    await asyncio.gather(
        *(prepare(topic.topic_id) for topic in topics if topic.status in {"pending", "preparing_evidence"})
    )
    topics = await _list_topics(project_id, organization_version)
    if await _stop_for_topic_attention(project_id, topics):
        return {"project_id": project_id, "status": "needs_attention"}

    # 同一项目的正式讲义由这个单一 runner 依次写入，保持统一作者性。
    for topic in topics:
        if topic.status != "passed":
            await write_topic_handout(
                project_id,
                organization_version,
                topic.topic_id,
                tasks,
                note_aliases,
            )
    topics = await _list_topics(project_id, organization_version)
    if await _stop_for_topic_attention(project_id, topics):
        return {"project_id": project_id, "status": "needs_attention"}
    if any(topic.status != "passed" for topic in topics):
        raise ValueError("正式讲义尚未全部达到通过状态")

    snapshot["editorial_plan"] = await _load_current_editorial_plan(project_id, organization_version)
    book_review = await _review_book_with_meaningful_groups(tasks, snapshot, topics)
    _validate_book_review(book_review, {topic.topic_key for topic in topics})
    if book_review.decision == "revise":
        revised = await _apply_book_revisions(
            project_id,
            organization_version,
            book_review,
            tasks,
            note_aliases,
        )
        if not revised:
            return {"project_id": project_id, "status": "needs_attention"}
        topics = await _list_topics(project_id, organization_version)
        snapshot["editorial_plan"] = await _load_current_editorial_plan(project_id, organization_version)
        book_review = await _review_book_with_meaningful_groups(tasks, snapshot, topics)
        _validate_book_review(book_review, {topic.topic_key for topic in topics})
    if book_review.decision != "pass":
        await _mark_compilation_attention(project_id, book_review.summary)
        return {"project_id": project_id, "status": "needs_attention"}

    navigation = await _write_navigation_with_meaningful_groups(tasks, snapshot, topics, book_review)
    if not navigation.strip():
        raise ValueError("总编没有生成可用的读者导航")
    skill_body = await tasks.write_skill_body(
        model_spec=snapshot["model_spec"],
        stage_prompt=snapshot["prompt_pack"].skill_author,
        learning_plan=snapshot["learning_plan"],
        editorial_plan=snapshot["editorial_plan"],
        book_review=book_review,
        navigation=navigation,
    )
    if not skill_body.skill_markdown.strip():
        raise ValueError("总编没有生成专家 Skill 使用入口")

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        materials = await repo.list_project_materials(project_id)
        topics = await repo.list_topics(project_id, organization_version)
        if (
            project is None
            or project.organization_version != organization_version
            or project.stage != "handout_compilation"
            or project.status != "running"
        ):
            return {"project_id": project_id, "ignored": True}
        published_materials = [material for material in materials if material.status == "passed"]
        material_source_texts = await load_expert_material_source_texts(published_materials)
        skill_dir = await asyncio.to_thread(
            build_candidate_skill,
            project=project,
            materials=published_materials,
            material_source_texts=material_source_texts,
            topics=topics,
            navigation=navigation,
            skill_body=skill_body.skill_markdown,
        )
        project.final_review = json.dumps(book_review.model_dump(), ensure_ascii=False)
        project.navigation = navigation
        project.skill_body = skill_body.skill_markdown
        project.skill_slug = get_project_skill_slug(project)
        project.skill_dir = skill_dir
        project.stage = "release"
        project.status = "ready"
        project.message = "专家 Skill 成品已就绪，等待预览后手动启用"
        project.error_message = None
        project.updated_at = utc_now_naive()
        await db.flush()
    # 成书完成是终态：一次性截断项目流水到保留上限。
    await trim_expert_project_activities(project_id)
    return {"project_id": project_id, "status": "ready", "skill_slug": project.skill_slug}


async def retry_topic_compilation(
    db: AsyncSession,
    *,
    project_id: str,
    topic_id: str,
    created_by: str,
) -> int:
    """只重跑一篇进入需要处理的正式讲义。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    topic = await repo.lock_topic(topic_id)
    if project is None or project.created_by != created_by or topic is None or topic.project_id != project_id:
        raise ValueError("专家主题不存在")
    if project.stage != "handout_compilation" or topic.status != "needs_attention":
        raise ValueError("当前主题不能重跑")
    topic.status = "evidence_ready" if topic.evidence_pack else "pending"
    project.status = "running"
    project.message = f"正在重新定稿《{topic.title}》"
    project.error_message = None
    await db.commit()
    return project.organization_version


async def retry_book_compilation(db: AsyncSession, *, project_id: str, created_by: str) -> int:
    """在讲义均已通过时重新执行全书收口和候选 Skill 组装。"""

    repo = ExpertRepository(db)
    project = await repo.lock_project(project_id)
    if project is None or project.created_by != created_by:
        raise ValueError("专家项目不存在")
    if project.stage != "handout_compilation" or project.status != "needs_attention":
        raise ValueError("当前项目不能重新执行全书收口")
    topics = await repo.list_topics(project_id, project.organization_version)
    if any(topic.status != "passed" for topic in topics):
        raise ValueError("请先处理未通过的讲义")
    project.status = "running"
    project.message = "正在重新执行全书一致性检查"
    project.error_message = None
    await db.commit()
    return project.organization_version


async def _load_compilation_inputs(project_id: str, organization_version: int) -> tuple[dict, list[Any], list[Any]]:
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None:
            raise ValueError("专家项目不存在")
        if (
            project.stage != "handout_compilation"
            or project.status != "running"
            or project.organization_version != organization_version
        ):
            raise ValueError("当前讲义编译版本已经失效")
        materials = await repo.list_project_materials(project_id)
        topics = await repo.list_topics(project_id, organization_version)
        if not topics:
            raise ValueError("当前组织版本没有正式主题")
        return (
            {
                "model_spec": project.model_spec,
                "learning_plan": project.learning_plan or "",
                "prompt_pack": ProjectPromptPack.model_validate(project.prompt_pack),
                "editorial_plan": project.editorial_plan or "",
            },
            materials,
            topics,
        )


async def _list_topics(project_id: str, organization_version: int) -> list[Any]:
    async with pg_manager.get_async_session_context() as db:
        return await ExpertRepository(db).list_topics(project_id, organization_version)


async def _load_current_editorial_plan(project_id: str, organization_version: int) -> str:
    """读取当前组织版本已经通过 Review 的最新编辑方案。"""

    async with pg_manager.get_async_session_context() as db:
        project = await ExpertRepository(db).get_project(project_id)
        if project is None or project.organization_version != organization_version:
            raise ValueError("当前组织版本已经失效")
        editorial_plan = (project.editorial_plan or "").strip()
        if not editorial_plan:
            raise ValueError("当前项目缺少有效编辑方案")
        return editorial_plan


async def _stop_for_topic_attention(project_id: str, topics: list[Any]) -> bool:
    attention = [topic for topic in topics if topic.status == "needs_attention"]
    if not attention:
        return False
    await _mark_compilation_attention(project_id, attention[0].error_message or "讲义需要处理")
    return True


async def _mark_compilation_attention(project_id: str, reason: str) -> None:
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.get_project(project_id)
        if project is None or project.status == "paused":
            return
        await repo.update_project(
            project_id,
            status="needs_attention",
            message="讲义或全书收口存在需要处理的问题",
            error_message=reason,
        )


async def _review_book_with_meaningful_groups(
    tasks: ExpertModelTasks,
    snapshot: dict,
    topics: list[Any],
) -> BookReviewResult:
    try:
        return await tasks.review_book(
            model_spec=snapshot["model_spec"],
            learning_plan=snapshot["learning_plan"],
            stage_prompt=snapshot["prompt_pack"].book_editor,
            editorial_plan=snapshot["editorial_plan"],
            handouts=_format_handouts(topics),
        )
    except ExpertContextLimitError:
        reports = []
        for section, section_topics in _topics_by_section(topics).items():
            try:
                report = await tasks.review_book(
                    model_spec=snapshot["model_spec"],
                    learning_plan=snapshot["learning_plan"],
                    stage_prompt=snapshot["prompt_pack"].book_editor,
                    editorial_plan=snapshot["editorial_plan"],
                    handouts=_format_handouts(section_topics),
                )
                reports.append({"section": section, "review": report.model_dump()})
            except ExpertContextLimitError:
                for topic in section_topics:
                    report = await tasks.review_book(
                        model_spec=snapshot["model_spec"],
                        learning_plan=snapshot["learning_plan"],
                        stage_prompt=snapshot["prompt_pack"].book_editor,
                        editorial_plan=snapshot["editorial_plan"],
                        handouts=_format_handouts([topic]),
                    )
                    reports.append({"section": section, "topic_key": topic.topic_key, "review": report.model_dump()})
        return await tasks.merge_book_reviews(
            model_spec=snapshot["model_spec"],
            stage_prompt=snapshot["prompt_pack"].book_editor,
            editorial_plan=snapshot["editorial_plan"],
            section_reviews=json.dumps(reports, ensure_ascii=False, indent=2),
        )


async def _write_navigation_with_meaningful_groups(
    tasks: ExpertModelTasks,
    snapshot: dict,
    topics: list[Any],
    book_review: BookReviewResult,
) -> str:
    try:
        result = await tasks.write_navigation(
            model_spec=snapshot["model_spec"],
            stage_prompt=snapshot["prompt_pack"].navigation_author,
            editorial_plan=snapshot["editorial_plan"],
            book_review=book_review,
            handouts=_format_handouts(topics),
        )
        return result.navigation_markdown
    except ExpertContextLimitError:
        navigations = []
        for section, section_topics in _topics_by_section(topics).items():
            try:
                result = await tasks.write_navigation(
                    model_spec=snapshot["model_spec"],
                    stage_prompt=snapshot["prompt_pack"].navigation_author,
                    editorial_plan=snapshot["editorial_plan"],
                    book_review=book_review,
                    handouts=_format_handouts(section_topics),
                )
                navigations.append({"section": section, "navigation": result.navigation_markdown})
            except ExpertContextLimitError:
                for topic in section_topics:
                    result = await tasks.write_navigation(
                        model_spec=snapshot["model_spec"],
                        stage_prompt=snapshot["prompt_pack"].navigation_author,
                        editorial_plan=snapshot["editorial_plan"],
                        book_review=book_review,
                        handouts=_format_handouts([topic]),
                    )
                    navigations.append(
                        {"section": section, "topic_key": topic.topic_key, "navigation": result.navigation_markdown}
                    )
        merged = await tasks.merge_navigation(
            model_spec=snapshot["model_spec"],
            stage_prompt=snapshot["prompt_pack"].navigation_author,
            editorial_plan=snapshot["editorial_plan"],
            section_navigations=json.dumps(navigations, ensure_ascii=False, indent=2),
        )
        return merged.navigation_markdown


async def _apply_book_revisions(
    project_id: str,
    organization_version: int,
    book_review: BookReviewResult,
    tasks: ExpertModelTasks,
    note_aliases: dict[str, str],
) -> bool:
    issues_by_topic: dict[str, list[Any]] = defaultdict(list)
    for issue in book_review.issues:
        for topic_key in issue.topic_keys:
            issues_by_topic[topic_key].append(issue)

    for topic in await _list_topics(project_id, organization_version):
        issues = issues_by_topic.get(topic.topic_key)
        if not issues:
            continue
        if topic.revision_count >= MAX_HANDOUT_REVISIONS:
            await _mark_compilation_attention(project_id, f"《{topic.title}》已达到有限修订次数")
            return False
        succeeded = await _revise_topic_from_book_review(
            project_id,
            organization_version,
            topic.topic_id,
            issues,
            tasks,
            note_aliases,
        )
        if not succeeded:
            return False
    return True


async def _revise_topic_from_book_review(
    project_id: str,
    organization_version: int,
    topic_id: str,
    issues: list[Any],
    tasks: ExpertModelTasks,
    note_aliases: dict[str, str],
) -> bool:
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
            return False
        materials = await repo.list_project_materials(project_id)
        selected = [item for item in materials if item.material_id in set(topic.material_ids or [])]
        source_texts = await load_expert_material_source_texts(selected)
        evidence_pack = TopicEvidencePack.model_validate(topic.evidence_pack)
        snapshot = {
            "model_spec": project.model_spec,
            "learning_plan": project.learning_plan or "",
            "prompt_pack": ProjectPromptPack.model_validate(project.prompt_pack),
            "editorial_plan": project.editorial_plan or "",
            "topic": format_topic_definition(topic),
            "notes": format_handout_notes_with_evidence(
                project_id,
                selected,
                note_aliases,
                evidence_pack,
                source_texts,
            ),
            "evidence_pack": evidence_pack,
            "handout": HandoutDraftResult.model_validate(topic.handout_contract),
            "revision_count": topic.revision_count,
        }

    review = HandoutReviewResult(
        decision="revise",
        summary="全书一致性检查要求定点修订当前讲义",
        issues=[
            ReviewIssue(
                problem=issue.problem,
                affected_part="、".join(issue.topic_keys),
                source_lines=[],
                revision_direction=issue.revision_direction,
            )
            for issue in issues
        ],
    )
    try:
        revised = await tasks.revise_handout(
            model_spec=snapshot["model_spec"],
            learning_plan=snapshot["learning_plan"],
            stage_prompt=snapshot["prompt_pack"].handout_editor,
            editorial_plan=snapshot["handout"].editorial_plan_markdown,
            topic=snapshot["topic"],
            notes=snapshot["notes"],
            evidence_pack=snapshot["evidence_pack"],
            handout=snapshot["handout"],
            review=review,
        )
        validate_handout_contract(revised, snapshot["handout"].topic_key, snapshot["evidence_pack"])
        final_review = await tasks.review_handout(
            model_spec=snapshot["model_spec"],
            learning_plan=snapshot["learning_plan"],
            stage_prompt=snapshot["prompt_pack"].handout_reviewer,
            editorial_plan=revised.editorial_plan_markdown,
            topic=snapshot["topic"],
            notes=snapshot["notes"],
            evidence_pack=snapshot["evidence_pack"],
            handout=revised,
        )
        validate_handout_review(final_review)
    except Exception as exc:  # noqa: BLE001
        await _mark_topic_attention(topic_id, str(exc))
        return False
    if final_review.decision != "pass":
        await _mark_topic_attention(topic_id, final_review.summary)
        return False

    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        project = await repo.lock_project(project_id)
        topic = await repo.lock_topic(topic_id)
        if (
            project is None
            or project.status != "running"
            or topic is None
            or project.organization_version != organization_version
        ):
            return False
        topic.handout = revised.handout_markdown
        topic.handout_contract = revised.model_dump()
        topic.review = final_review.model_dump()
        topic.revision_count = snapshot["revision_count"] + 1
        topic.status = "passed"
        topic.error_message = None
        project.editorial_plan = revised.editorial_plan_markdown
        project.updated_at = utc_now_naive()
    return True


async def _mark_topic_attention(topic_id: str, reason: str) -> None:
    async with pg_manager.get_async_session_context() as db:
        repo = ExpertRepository(db)
        topic = await repo.lock_topic(topic_id)
        if topic is None:
            return
        project = await repo.get_project(topic.project_id)
        if project is None or project.status == "paused":
            return

        topic.status = "needs_attention"
        topic.error_message = reason
        await repo.update_project(
            topic.project_id,
            status="needs_attention",
            message="全书收口修订存在需要处理的讲义",
            error_message=reason,
        )


def _validate_book_review(review: BookReviewResult, topic_keys: set[str]) -> None:
    if review.decision == "revise" and not review.issues:
        raise ValueError("全书收口要求修订但没有给出具体问题")
    for issue in review.issues:
        if not issue.topic_keys or not set(issue.topic_keys) <= topic_keys:
            raise ValueError("全书收口问题引用了未知主题")


def _format_handouts(topics: list[Any]) -> str:
    return "\n\n".join(
        f"## [{topic.topic_key}] {topic.title}\n分区：{topic.section}\n\n{topic.handout or ''}"
        for topic in sorted(topics, key=lambda item: (item.position, item.topic_key))
    )


def _topics_by_section(topics: list[Any]) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = {}
    for topic in sorted(topics, key=lambda item: (item.position, item.topic_key)):
        groups.setdefault(topic.section, []).append(topic)
    return groups


__all__ = [
    "activate_expert_skill",
    "get_candidate_preview",
    "retry_book_compilation",
    "retry_topic_compilation",
    "run_handout_compilation",
    "uninstall_expert_skill",
]
