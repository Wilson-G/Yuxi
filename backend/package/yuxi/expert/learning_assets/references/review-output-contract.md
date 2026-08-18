# Review 输出约定

笔记和讲义的 Review 都返回一个 JSON 对象：

```json
{
  "verdict": "pass | revise | needs_attention",
  "issues": ["具体、可行动的问题"]
}
```

- `pass`：没有会改变后续使用的来源忠实性或组织问题。
- `revise`：有具体问题，当前稿可以根据原始输入定点修订。
- `needs_attention`：输入残缺、冲突或问题无法安全自动处理，应交给用户决定。

`issues` 只列真正影响判断、条件、适用范围、证据强度或下游使用的问题。不要为了凑检查数量列格式偏好，也不要用模糊评价替代可修复的问题。
