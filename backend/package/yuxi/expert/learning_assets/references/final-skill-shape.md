# 最终 Skill 形态

最终交付是一个普通、自包含的 Skill：

```text
skill/<name>-expert/
  SKILL.md
  nav/
  references/
  wiki/
  search/
  sources.jsonl
```

- `references/` 保存通过 Review 的专题讲义和必要的精选依据，不放完整原始上传材料。
- `nav/` 保存用户和 Agent 都能理解的专题目录与定位线索；第一版不迁入旧工厂的 doors、ladders、cards 或专用命令体系。
- `search/` 从最终讲义和导航建立轻量检索入口，帮助当前 Yuxi Agent 定位后再精读相关内容。
- `sources.jsonl` 记录讲义使用的材料来源标识和必要定位信息。

项目 Prompt、侦察报告、笔记草稿、Review、失败信息、任务清单和原始材料都只保留在项目内部，不发布到最终 Skill。
