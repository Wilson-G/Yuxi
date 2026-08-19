import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import {
  canExcludeExpertMaterial,
  canPreviewExpertMaterial,
  canReviseExpertOrganization,
  getExcludedExpertMaterials,
  getExpertMaterialProgress,
  getExpertProjectProgress,
  getExpertStageIndex,
  getExpertTopicProgress,
  isExpertMethodPreparationRunning,
  shouldPollExpertProject
} from '../../src/utils/expertProject.js'

test('专家项目只在后台推进状态轮询', () => {
  assert.equal(shouldPollExpertProject({ status: 'preparing' }), true)
  assert.equal(shouldPollExpertProject({ status: 'running' }), true)
  assert.equal(shouldPollExpertProject({ status: 'awaiting_confirmation' }), false)
  assert.equal(shouldPollExpertProject({ status: 'needs_attention' }), false)
})

test('材料和讲义进度按真实终态计算', () => {
  assert.equal(
    getExpertMaterialProgress([
      { status: 'passed' },
      { status: 'needs_attention' },
      { status: 'learning' },
      { status: 'pending' }
    ]),
    50
  )
  assert.equal(
    getExpertTopicProgress([{ status: 'passed' }, { status: 'writing' }, { status: 'needs_attention' }]),
    67
  )
})

test('总体进度遵循确认点和当前阶段', () => {
  assert.equal(getExpertProjectProgress({ stage: 'orientation', status: 'awaiting_confirmation' }), 20)
  assert.equal(
    getExpertProjectProgress({
      stage: 'material_learning',
      status: 'running',
      materials: [{ status: 'passed' }, { status: 'learning' }]
    }),
    35
  )
  assert.equal(getExpertProjectProgress({ stage: 'release', status: 'ready' }), 100)
})

test('未纳入材料和正文预览边界来自材料事实', () => {
  const materials = [
    { status: 'excluded', line_count: 10 },
    { status: 'no_value', line_count: 8 },
    { status: 'passed', line_count: 20 },
    { status: 'converting', line_count: 0 }
  ]
  assert.deepEqual(
    getExcludedExpertMaterials(materials).map((item) => item.status),
    ['excluded', 'no_value']
  )
  assert.equal(canPreviewExpertMaterial(materials[2]), true)
  assert.equal(canPreviewExpertMaterial(materials[3]), false)
  assert.equal(getExpertStageIndex('organization'), 2)
})

test('不能排除项目中最后一份仍可能进入学习的材料', () => {
  const blocked = { material_id: 'blocked', status: 'needs_attention' }

  assert.equal(canExcludeExpertMaterial([blocked], blocked.material_id), false)
  assert.equal(
    canExcludeExpertMaterial(
      [blocked, { material_id: 'no-value', status: 'no_value' }, { material_id: 'old', status: 'excluded' }],
      blocked.material_id
    ),
    false
  )
  assert.equal(
    canExcludeExpertMaterial(
      [blocked, { material_id: 'usable', status: 'ready' }],
      blocked.material_id
    ),
    true
  )
})

test('组织方案只有在等待确认或需要处理时允许提交反馈', () => {
  assert.equal(canReviseExpertOrganization('running'), false)
  assert.equal(canReviseExpertOrganization('preparing'), false)
  assert.equal(canReviseExpertOrganization('awaiting_confirmation'), true)
  assert.equal(canReviseExpertOrganization('needs_attention'), true)
})

test('项目定向运行时进入学习方案与小样生成视图', () => {
  assert.equal(
    isExpertMethodPreparationRunning({ stage: 'orientation', status: 'running' }),
    true
  )
  assert.equal(
    isExpertMethodPreparationRunning({ stage: 'orientation', status: 'preparing' }),
    false
  )
  assert.equal(
    isExpertMethodPreparationRunning({ stage: 'material_learning', status: 'running' }),
    false
  )
})

test('项目定向页面允许用户选择至多 20 份代表性材料', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')

  assert.match(view, /const maxRepresentativeMaterials = 20/)
  assert.match(view, /选择 1～20 份代表性材料/)
  assert.match(view, /最多选择 \$\{maxRepresentativeMaterials\} 份代表性材料/)
})

test('方法小样的系统失败可用原代表材料重新试跑', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')

  assert.match(view, /project\.status === 'needs_attention'/)
  assert.match(view, /重新试跑当前方法/)
  assert.match(view, /expertApi\.startOrientation\(projectId\.value, project\.value\.representative_material_ids/)
})

test('等待确认时保留用户反馈调整方法的入口', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')

  assert.match(view, /project\.status === 'awaiting_confirmation'/)
  assert.match(view, /按意见调整学习方向/)
  assert.match(view, /project\.learning_plan \|\| ''/)
  assert.doesNotMatch(view, /<h3>项目学习方案<\/h3>/)
})

test('方法确认后自动校准一次，不开放第二个方法确认点', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')
  const api = await readFile(new URL('../../src/apis/expert_api.js', import.meta.url), 'utf8')

  assert.match(view, /根据小样 Review 自动校准一次方法并复跑原小样/)
  assert.match(view, /确认并开始校准/)
  assert.doesNotMatch(view, /calibrateMethod/)
  assert.doesNotMatch(api, /method\/calibrate/)
})
