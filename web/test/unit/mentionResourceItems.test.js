import test from 'node:test'
import assert from 'node:assert/strict'

import { findMentionResourceItem } from '../../src/utils/mention_resource_items.js'

test('按 slug 找到可插入对话的专家 Skill', () => {
  const item = findMentionResourceItem(
    {
      skills: [
        { slug: 'expert-amazon', name: '亚马逊运营专家', description: '运营判断方法' }
      ]
    },
    'skill',
    'expert-amazon'
  )

  assert.deepEqual(item, {
    type: 'skill',
    value: 'expert-amazon',
    label: '亚马逊运营专家',
    tokenLabel: '@skill:亚马逊运营专家',
    description: '运营判断方法'
  })
})

test('不可用的 Skill 不生成提及项', () => {
  assert.equal(findMentionResourceItem({ skills: [] }, 'skill', 'missing'), undefined)
})
