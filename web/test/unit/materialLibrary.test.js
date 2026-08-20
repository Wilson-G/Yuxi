import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createMaterialUploadQueue,
  getMaterialFileStatus,
  getMaterialFileStatusLabel,
  getReadyMaterialUnits
} from '../../src/utils/materialLibrary.js'

const flushPromises = () => new Promise((resolve) => globalThis.setTimeout(resolve, 0))

test('上传队列保留目录相对路径并按并发槽持续补位', async () => {
  const pending = []
  const startedPaths = []
  let latestItems = []
  const queue = createMaterialUploadQueue({
    concurrency: 2,
    upload: (_file, relativePath, onProgress) => {
      startedPaths.push(relativePath)
      onProgress(35)
      return new Promise((resolve) => pending.push(resolve))
    },
    onChange: (items) => {
      latestItems = items
    }
  })

  queue.enqueue([
    { name: 'a.md', webkitRelativePath: '课程/第一章/a.md' },
    { name: 'b.md', webkitRelativePath: '课程/第二章/b.md' },
    { name: 'c.md', webkitRelativePath: '课程/c.md' }
  ])

  assert.deepEqual(startedPaths, ['课程/第一章/a.md', '课程/第二章/b.md'])
  assert.deepEqual(
    latestItems.map((item) => item.status),
    ['uploading', 'uploading', 'queued']
  )
  assert.equal(latestItems[0].progress, 35)

  pending[0]({ item: { file_id: 'a' } })
  await flushPromises()
  assert.deepEqual(startedPaths, ['课程/第一章/a.md', '课程/第二章/b.md', '课程/c.md'])
})

test('失败文件保留原因且重试只重新执行这一份', async () => {
  let attempt = 0
  let latestItems = []
  const queue = createMaterialUploadQueue({
    upload: async () => {
      attempt += 1
      if (attempt === 1) throw new Error('文件格式无法解析')
      return { item: { file_id: 'ready' } }
    },
    onChange: (items) => {
      latestItems = items
    }
  })

  queue.enqueue([{ name: 'broken.pdf' }])
  await flushPromises()
  assert.equal(latestItems[0].status, 'failed')
  assert.equal(latestItems[0].error, '文件格式无法解析')

  assert.equal(queue.retry(latestItems[0].id), true)
  await flushPromises()
  assert.equal(attempt, 2)
  assert.equal(latestItems[0].status, 'success')
  assert.equal(queue.retry(latestItems[0].id), false)
})

test('处理投递失败的已上传文件保留返回事实且不会重新上传', async () => {
  let latestItems = []
  const response = {
    item: {
      file_id: 'attention-file',
      status: 'needs_attention',
      error_message: '来源文件任务投递失败: redis unavailable'
    }
  }
  const queue = createMaterialUploadQueue({
    upload: async () => response,
    onChange: (items) => {
      latestItems = items
    }
  })

  queue.enqueue([{ name: 'uploaded.md' }])
  await flushPromises()

  assert.equal(latestItems[0].status, 'success')
  assert.equal(latestItems[0].response, response)
  assert.equal(queue.retry(latestItems[0].id), false)
})

test('文件状态区分正文就绪和材料单元就绪', () => {
  assert.equal(getMaterialFileStatus({ status: 'processing', unit_status: 'pending' }), 'processing')
  assert.equal(getMaterialFileStatus({ status: 'ready', unit_status: 'processing' }), 'processing')
  assert.equal(
    getMaterialFileStatusLabel({ status: 'ready', unit_status: 'needs_attention' }),
    '单元需要处理'
  )
})

test('专家项目选择只接收 ready 材料单元', () => {
  const units = [
    { unit_id: 'ready', status: 'ready' },
    { unit_id: 'pending', status: 'pending' },
    { unit_id: 'failed', status: 'needs_attention' }
  ]
  assert.deepEqual(getReadyMaterialUnits(units), [{ unit_id: 'ready', status: 'ready' }])
})
