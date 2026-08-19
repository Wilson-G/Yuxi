import assert from 'node:assert/strict'
import test from 'node:test'

import { filterTasksByStatus, isActiveTaskStatus } from '../../src/utils/taskStatus.js'

const tasks = [
  { id: 'running', status: 'running' },
  { id: 'waiting', status: 'waiting' },
  { id: 'success', status: 'success' },
  { id: 'failed', status: 'failed' }
]

test('等待操作不进入进行中或已完成筛选', () => {
  assert.deepEqual(filterTasksByStatus(tasks, 'active').map((task) => task.id), ['running'])
  assert.deepEqual(filterTasksByStatus(tasks, 'waiting').map((task) => task.id), ['waiting'])
  assert.deepEqual(filterTasksByStatus(tasks, 'success').map((task) => task.id), ['success'])
  assert.equal(isActiveTaskStatus('waiting'), false)
})
