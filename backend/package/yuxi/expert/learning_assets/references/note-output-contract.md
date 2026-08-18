# 学艺笔记输出约定

笔记是后续组织专题的工作产物，不是最终 Skill，也不是材料摘要。

模型返回一个 JSON 对象，只有以下字段：

```json
{
  "disposition": "usable | no_value | needs_attention",
  "note_md": "Markdown 笔记",
  "routing_labels": ["面向真实问题的短路标"]
}
```

- `usable`：材料中有可进入后续讲义的判断或经验。
- `no_value`：已完整阅读，但没有足够独立、可迁移的内容；这是正常完成。
- `needs_attention`：材料残缺、冲突或超出当前任务可安全处理的范围；应说明具体原因，不能伪装成 `no_value`。

`note_md` 写清来源支持的条件或信号、判断或选择、理由或经验依据、边界和反转。引用使用当前材料的真实行号。`routing_labels` 只帮助后续发现可能相关的主题，不等于最终主题，也不替代笔记正文。
