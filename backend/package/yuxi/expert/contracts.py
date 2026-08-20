"""专家学习模型任务的结构化业务响应合同。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExpertContract(BaseModel):
    """禁止模型响应携带未声明的机器字段。"""

    model_config = ConfigDict(extra="forbid")


class ProjectOrientationResult(ExpertContract):
    """项目定向任务的业务结论。"""

    template_name: Literal[
        "mentor-oral-teaching.md",
        "operations-decision.md",
        "tutorial-procedure.md",
        "interview-multi-source.md",
    ]
    material_type: str
    expert_identity: str
    report_markdown: str
    source_risks: list[str] = Field(default_factory=list)


class ProjectPromptPack(ExpertContract):
    """一个项目固定后供所有阶段直接使用的完整 Prompt 包。"""

    note_author: str
    note_reviewer: str
    note_reviser: str
    topic_researcher: str
    topic_editor: str
    handout_editor: str
    handout_reviewer: str
    handout_reviser: str
    book_editor: str
    navigation_author: str
    skill_author: str


class ProjectMethodResult(ExpertContract):
    """总编模型为当前材料形成的项目专属编译方法。"""

    learning_plan_markdown: str
    compilation_brief_markdown: str
    prompt_pack: ProjectPromptPack
    method_sample_aliases: list[str] = Field(min_length=1, max_length=5)


class MethodCalibrationResult(ExpertContract):
    """总编根据小样 Review 收敛后的下一版项目方法。"""

    learning_plan_markdown: str
    compilation_brief_markdown: str
    prompt_pack: ProjectPromptPack


class MethodCalibrationAssessment(ExpertContract):
    """比较 V1、V2 小样后对方法级残余问题的判断。"""

    decision: Literal["acceptable_residual", "needs_attention"]
    reason: str


class EvidenceAnchor(ExpertContract):
    """模型在当前任务内使用的材料行号依据。"""

    material_alias: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    supports: str


class MaterialNoteResult(ExpertContract):
    """单份材料学习后的笔记或停止结论。"""

    decision: Literal["learned", "no_value", "needs_attention"]
    note_markdown: str = ""
    reason: str = ""
    routing_labels: list[str] = Field(default_factory=list)
    candidate_questions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceAnchor] = Field(default_factory=list)


class ReviewIssue(ExpertContract):
    """Reviewer 提交的一条可执行问题。"""

    problem: str
    affected_part: str
    source_lines: list[str] = Field(default_factory=list)
    revision_direction: str


class MaterialReviewResult(ExpertContract):
    """独立 Reviewer 对单份笔记的业务判断。"""

    decision: Literal["pass", "revise", "no_value", "needs_attention"]
    summary: str
    issues: list[ReviewIssue] = Field(default_factory=list)


class PlanningGroup(ExpertContract):
    """由模型依据判断路标形成的有意义规划范围。"""

    group_key: str
    title: str
    rationale: str
    note_aliases: list[str]


class PlanningGroupsResult(ExpertContract):
    """大批笔记进入候选主题研究前的语义分组。"""

    groups: list[PlanningGroup]
    unresolved: list[str] = Field(default_factory=list)


class CandidateTopic(ExpertContract):
    """普通研究助手提交给总编的候选主题。"""

    candidate_key: str
    reader_question: str
    rationale: str
    note_aliases: list[str]
    source_conflicts: list[str] = Field(default_factory=list)
    omissions: list[str] = Field(default_factory=list)


class TopicResearchResult(ExpertContract):
    """一个规划范围内的候选主题、冲突和缺口。"""

    group_key: str
    candidates: list[CandidateTopic]
    group_findings: str


class TopicDefinition(ExpertContract):
    """总编确定的一项正式讲义主题。"""

    topic_key: str
    title: str
    section: str
    reader_question: str
    purpose: str
    boundaries: str
    note_aliases: list[str]
    unresolved_questions: list[str] = Field(default_factory=list)
    order: int = Field(ge=1)


class UnassignedNote(ExpertContract):
    """总编明确不放入主题地图的一份已审笔记。"""

    note_alias: str
    reason: str


class TopicMapResult(ExpertContract):
    """总编形成的项目级主题地图和当前编辑方案。"""

    plan_markdown: str
    editorial_plan_markdown: str
    topics: list[TopicDefinition]
    unassigned_notes: list[UnassignedNote] = Field(default_factory=list)
    sample_topic_keys: list[str]
    sample_selection_reason: str


class HumanizedTopicDefinition(ExpertContract):
    """展示给用户的主题标题、问题与边界。"""

    topic_key: str
    title: str
    section: str
    reader_question: str
    purpose: str
    boundaries: str


class HumanizedTopicMapResult(ExpertContract):
    """说人话任务改写后的主题地图展示内容。"""

    plan_markdown: str
    topics: list[HumanizedTopicDefinition]
    unassigned_notes: list[UnassignedNote] = Field(default_factory=list)
    sample_selection_reason: str


class HumanizedTextResult(ExpertContract):
    """说人话任务改写后可直接展示或发布的正文。"""

    content_markdown: str = Field(min_length=1)


class TopicEvidenceItem(ExpertContract):
    """助手为某个主题选择的一条原文依据。"""

    note_alias: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    supports: str


class TopicEvidencePack(ExpertContract):
    """讲义总编任务所需的证据、冲突和遗漏输入。"""

    topic_key: str
    evidence: list[TopicEvidenceItem]
    conflicts: list[str] = Field(default_factory=list)
    omissions: list[str] = Field(default_factory=list)


class HandoutDraftResult(ExpertContract):
    """总编模型提交的讲义候选稿、关键引用和下一版完整编辑方案。"""

    topic_key: str
    handout_markdown: str
    citations: list[TopicEvidenceItem]
    editorial_plan_markdown: str = Field(min_length=1)


class HandoutReviewResult(ExpertContract):
    """独立 Reviewer 对讲义候选稿的业务判断。"""

    decision: Literal["pass", "revise", "needs_attention"]
    summary: str
    issues: list[ReviewIssue] = Field(default_factory=list)


class BookConsistencyIssue(ExpertContract):
    """全书收口时发现的一项跨讲义问题。"""

    topic_keys: list[str]
    problem: str
    revision_direction: str


class BookReviewResult(ExpertContract):
    """总编对全书术语、边界、冲突、重复和缺口的检查结论。"""

    decision: Literal["pass", "revise", "needs_attention"]
    summary: str
    issues: list[BookConsistencyIssue] = Field(default_factory=list)


class NavigationResult(ExpertContract):
    """面向真实读者问题的最终讲义导航。"""

    navigation_markdown: str


class SkillBodyResult(ExpertContract):
    """最终专家 Skill 的身份、判断姿态与使用入口正文。"""

    skill_markdown: str
