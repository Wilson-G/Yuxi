# 专家项目定向与 Prompt 设计资产

这里不是一套可直接套给所有项目的固定 Prompt。核心资产 [project_prompt_designer/SKILL.md](project_prompt_designer/SKILL.md) 是系统内部的元 Skill：它教 Prime 先调研材料类型和领域，再把方法论改写成当前项目专属的编译 Prompt 包。

旧 `prompts/`、`references/`、`stage-zero.md` 与 `MIGRATION.md` 保留为迁移参考。未来运行时不应把它们逐段拼成 Prompt，也不应把 `project_prompt_designer` 原文直接塞给笔记作者或 Reviewer。

## 第 0 阶段的真实作用

第 0 阶段是项目定向，不是提前完整学习整个材料包。

系统提供材料目录、基础信息和少量有目的的完整样本。用户优先指定样本；用户没有指定时，Agent 只能依据用户可见的标题、来源、类型和长度提出候选并说明理由，确认后才读取。调研要看清：

- 这是哪个领域，材料属于口述、案例、教程、运营复盘、访谈还是其他形态；
- 材料怎样传授判断或经验，谁在说话，哪些是案例、反馈或辅助信息；
- 哪些保真风险、来源差异和高风险边界会影响后续学习；
- 读者最终需要一位怎样的专家。

调研报告不是笔记，也不替第 1 阶段提炼每份材料的知识。

## 项目专属 Prompt 包

Prime Prompt 总编读取中央方法、材料类型模板和项目定向报告后，先写出项目专属编译底稿，再从底稿形成完整阶段 Prompt：

1. 材料侦察。
2. 项目学习方案。
3. 学艺笔记、Review 和修订。
4. 主题候选研究与 Prime 总编主题地图。
5. Prime 总编讲义、Review 和修订。
6. 全书收口、导航和最终 Skill。

项目专属 Prompt 不是“通用模板 + 项目补丁”。它应自然写出这位专家是谁、这批材料怎样说话、应从中偷学什么、怎样守住证据、最终怎样面对读者。之后用 1～2 篇真实材料试跑笔记和 Review；用户确认贴合后，整个 Prompt 包固定到项目中。

## 模型与系统的边界

模型只处理有业务意义的内容：材料调研、项目学习方案、笔记、审稿问题、主题地图、讲义、导航和最终专家使用说明。

任务、材料、版本、状态、尝试次数、路径、文件布局、来源映射、搜索索引、队列、恢复和发布均由系统保存。项目专属 Prompt 和模型输出不应出现任务 ID、哈希、输出路径、目录布局、文件名、队列状态或发布清单。

结构化响应可以由未来调用方约束并保存，但该 schema 不是 Prompt 资产。模型不需要知道自己结果如何被存储。

## 模板

`project_prompt_designer/templates/` 提供少量材料类型方法起点：

- `mentor-oral-teaching.md`：师傅口述与案例判断；
- `operations-decision.md`：运营复盘与数据决策；
- `tutorial-procedure.md`：教程与专业流程；
- `interview-multi-source.md`：访谈与多来源经验。

模板只帮助 Prime 问对问题，不能直接复制到项目 Prompt 中。项目确认后，中央方法或模板更新不影响已经开始的项目。

`project_prompt_designer/examples/bazi-full-stage-prompt-pack.md` 是元 Skill 必读的端到端高质量 Prompt 包示范。它展示八字项目如何从项目定向一路写到最终 Skill；它不是普通运行时模板，也不是其他项目可以直接复用的领域规则。
