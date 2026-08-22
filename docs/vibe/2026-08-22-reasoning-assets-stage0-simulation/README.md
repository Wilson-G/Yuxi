# 第 0 阶段产物模拟：reasoning_assets 方法 ×「Soju子晴-亚马逊实战玩家」

日期：2026-08-22
性质：人工模拟产物，不是任何模型任务的运行输出。用于在接入真实材料前，对照检验 `reasoning_assets`（领域推导资产法）第 0 阶段交付物是否符合中央方法要求。

## 输入

- 编译方法：`backend/package/yuxi/expert/prompt_designer_profiles/reasoning_assets/SKILL.md`。初稿对照 commit `f37f6ab8` 时点生成；同日校准后 SKILL 明确"推导链是比较框架不是内容清单、单篇材料超出已知局面时照常重建路径并留路由路标"，本目录 03/04 已同步对齐现行 SKILL
- 材料：`/Users/will/ai-workspace/wiki-source/final/getnote/amazon-knowledge/Soju子晴-亚马逊实战玩家/`，170 份短视频口述转写 Markdown
- 项目名称（模拟）：Soju子晴 · 亚马逊站内运营与广告投放实战
- 项目说明（模拟）：把一位亚马逊精品卖家的实战短视频转写编译为可供智能体使用的站内运营专家 Skill
- 代表样本：模拟"用户确认"环节，选取覆盖不同方向的 5 篇并完整阅读：

| 别名 | 文件 | 校准方向 |
| --- | --- | --- |
| M004 | 0004-亚马逊竞价策略全解析 | 四种竞价策略的阶段化选择与控制权取舍 |
| M008 | 0008-大词冲首页，如何用小词高效带动 | 推广路径选择、权重传导、瓶颈期收缩 |
| M016 | 0016-服装类目地狱生存挑战，非标里找标品 | 品类形态判断如何决定打法与竞价预期 |
| M043 | 0043-这套数据分析sop让你更好进阶 | 单量异动的分层诊断顺序 |
| M044 | 0044-ASIN定位广告宝藏打法 | 关键词投放与 ASIN 投放的取舍、攻防用法 |

别名编号按文件名排序生成（M001–M170），与系统按上传顺序编号的方式一致。

## 产物与合同对应

| 文件 | 对应合同/任务 |
| --- | --- |
| [01-orientation-result.md](01-orientation-result.md) | `orient_project` → `ProjectOrientationResult` |
| [02-learning-plan.md](02-learning-plan.md) | `design_project_method` → `ProjectMethodResult.learning_plan_markdown`（用户确认点） |
| [03-compilation-brief.md](03-compilation-brief.md) | `ProjectMethodResult.compilation_brief_markdown`（含 `## 项目领域推导模型`） |
| [04-prompt-pack.md](04-prompt-pack.md) | `ProjectMethodResult.prompt_pack` 全部 11 个阶段 Prompt；`method_sample_aliases = ["M004","M008","M016","M043","M044"]` |

## 执行顺序（按 profile 固定方法）

1. 只凭项目信息与目录激活候选领域推导模型（模型已有知识，不写成"材料认为"）。
2. 完整阅读 5 篇代表样本，校准候选模型，按领域基础／样本观察／项目假设／材料缺口四层归位。
3. 生成本目录三层交付物；试跑样本选择即上表 5 份（≥4 份，符合合同 `min_length=4` 的业务约束中取 4~5 的要求）。

## 模拟中刻意遵守的纪律

- 不把样本术语变成后续材料的检查清单；样本观察只进入"样本观察"层。
- 转写噪音（"大慈"=大词、"A森/A层"=ASIN、"a call ass"=ACOS 等）按上下文还原后再判断归属，还原本身标注为领域补链。
- 无反馈的材料不补造回验；平台数字标注时效条件。
- 中央示例（profile 自带的 ACOS/Listing 校准样例）中的具体指标与结论未复制进本项目 Prompt。
