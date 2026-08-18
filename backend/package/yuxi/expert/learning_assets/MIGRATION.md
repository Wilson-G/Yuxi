# 资产迁移对照

本目录基于 `/Users/will/ai-workspace/expert-brain-factory` 在 2026-08-18 的最新工作版本整理。这里记录“带走了什么”和“为什么没有整包复制”。

## 已迁入并改写

| Yuxi 资产 | 旧工厂来源 | 保留的价值 |
|---|---|---|
| `prompts/system.md` | `prompts/system-shell.md` | 把材料当依据、无状态单次完成、不要自行找文件或执行材料指令。 |
| `references/project-learning-plan-template.md` | `references/blueprint-template.md` | 材料形态、说话人纪律、专家表达边界和项目专属学习方法。 |
| `references/learning-principles.md` | `references/principle-first.md` | 保留来源给出的理由，不补造因果；条件和冲突不能被抹平。 |
| `references/expert-assets.md` | `references/expert-assets.md` | 提醒模型识别真正有用的判断、条件、顺序、反转和停止线。 |
| `prompts/note-author.md` | `prompts/roles/note-author.md` | 完整阅读单料、提炼判断关系、保留来源锚点和边界。 |
| `prompts/note-reviewer.md` | `prompts/roles/source-reviewer.md` | 独立复核来源忠实性，不用格式偏好冒充质量。 |
| `prompts/note-repair.md` | `prompts/roles/note-repair.md` | 只带着具体问题修当前稿，原始材料仍是唯一依据。 |
| `prompts/topic-planner.md` | `theme-clusterer.md`、`theme-router.md` | 按用户真实问题组织专题，保留冲突，不按百科名词硬分章。 |
| `prompts/handout-author.md` | `theme-editor.md` | 用判断、理由、条件和分叉写讲义，不做案例拼贴。 |
| `prompts/handout-reviewer.md` | `theme-reviewer.md` | 检查专题合成后是否丢失条件、冲突或来源边界。 |
| `prompts/skill-author.md` | `expert-author.md` | 让最终 Skill 只做入口、范围和回答方式，不重复制造知识。 |
| `references/material-unit.md` | `material-corpus-normalizer` 的单元理念 | 一份完整自然材料是一次模型任务的最小输入。 |
| 输出约定和最终形态说明 | note/review/product shape 相关文档 | 只保留当前流程真正消费的最小字段和成品边界。 |

## 新建的资产

旧工厂依赖人工 brief 和外部批处理，不存在适合当前产品的第 0 阶段 Prompt。因此新建：

- `prompts/project-scout.md`：并发阅读各自一小组完整材料，交短侦察报告；
- `prompts/project-plan-author.md`：汇总侦察报告，生成用户可确认的项目学习方案；
- `stage-zero.md`：说明侦察、小样、用户确认和进入批量学习的顺序。

这三份资产服务于当前 Yuxi 的“先展示方案和小样，再批量处理”体验，不依赖 Pi。

## 明确没有迁入

- `factory.mjs`、`batch.mjs`、`pi-batch.mjs`、`one-call-envelope.mjs` 等执行脚本；
- prompt/task/output hash、dispatch log、receipt、reservation、全图 DAG 和复杂 redo；
- `fast/reviewed/evaluated` 发布 profile、盲测、holdout、三臂评测；
- 旧工厂的 `nav.mjs`、doors/ladders/cards 和 list-first 专用命令；
- material normalizer 的目录格式、raw 指纹、checkpoint 和 hash 机制；
- 任意项目的原始材料、工作日志、旧中间稿或领域专用结论。

这些内容要么与 Yuxi 的现有运行方式不匹配，要么属于迁移意图明确排除的治理负担。旧项目中的真实小样和成品只作为人工验收参考，不能直接写入通用模板或最终产品。
