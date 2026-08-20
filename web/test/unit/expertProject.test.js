import test from 'node:test'
import assert from 'node:assert/strict'

import {
  canDeleteExpertProject,
  canExcludeExpertMaterial,
  canPauseExpertProject,
  canPreviewExpertMaterial,
  canReviseExpertOrganization,
  formatExpertFinalReview,
  getExcludedExpertMaterials,
  getExpertMaterialProgress,
  getExpertProjectProgress,
  getExpertStageIndex,
  getExpertTopicProgress,
  isInitialExpertMethodConfirmation,
  isExpertMethodPreparationRunning,
  shouldPollExpertProject
} from '../../src/utils/expertProject.js'

test('专家项目只在后台推进状态轮询', () => {
  assert.equal(shouldPollExpertProject({ status: 'preparing' }), true)
  assert.equal(shouldPollExpertProject({ status: 'running' }), true)
  assert.equal(shouldPollExpertProject({ status: 'awaiting_confirmation' }), false)
  assert.equal(shouldPollExpertProject({ status: 'needs_attention' }), false)
  assert.equal(shouldPollExpertProject({ status: 'paused' }), false)
})

test('项目生命周期动作遵循后端暂停、卸载和删除条件', () => {
  assert.equal(canPauseExpertProject({ status: 'running' }), true)
  assert.equal(canPauseExpertProject({ status: 'paused' }), false)
  assert.equal(canPauseExpertProject({ status: 'enabled' }), false)
  assert.equal(canDeleteExpertProject({ status: 'paused' }), true)
  assert.equal(canDeleteExpertProject({ status: 'needs_attention' }), true)
  assert.equal(canDeleteExpertProject({ status: 'running' }), false)
  assert.equal(canDeleteExpertProject({ status: 'enabled' }), false)
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
    getExpertTopicProgress([
      { status: 'passed' },
      { status: 'writing' },
      { status: 'needs_attention' }
    ]),
    67
  )
})

test('总体进度遵循确认点和当前阶段', () => {
  assert.equal(
    getExpertProjectProgress({ stage: 'orientation', status: 'awaiting_confirmation' }),
    20
  )
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
      [
        blocked,
        { material_id: 'no-value', status: 'no_value' },
        { material_id: 'old', status: 'excluded' }
      ],
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
  assert.equal(isExpertMethodPreparationRunning({ stage: 'orientation', status: 'running' }), true)
  assert.equal(
    isExpertMethodPreparationRunning({ stage: 'orientation', status: 'preparing' }),
    false
  )
  assert.equal(
    isExpertMethodPreparationRunning({ stage: 'material_learning', status: 'running' }),
    false
  )
})

test('自动推进只在 V1 方法和小样等待确认时选择', () => {
  assert.equal(
    isInitialExpertMethodConfirmation({
      stage: 'orientation',
      status: 'awaiting_confirmation',
      method_calibration_completed_at: null,
      auto_advance: false
    }),
    true
  )
  assert.equal(
    isInitialExpertMethodConfirmation({
      stage: 'orientation',
      status: 'awaiting_confirmation'
    }),
    true
  )
  assert.equal(
    isInitialExpertMethodConfirmation({
      stage: 'orientation',
      status: 'awaiting_confirmation',
      method_calibration_completed_at: '2026-08-20T08:00:00Z'
    }),
    false
  )
  assert.equal(
    isInitialExpertMethodConfirmation({
      stage: 'orientation',
      status: 'running',
      method_calibration_completed_at: null
    }),
    false
  )
  assert.equal(
    isInitialExpertMethodConfirmation({
      stage: 'organization',
      status: 'awaiting_confirmation',
      method_calibration_completed_at: null
    }),
    false
  )
})

test('全书 Review 按结构展示并兼容旧项目纯文本', () => {
  const review = JSON.stringify({
    summary: '全书需要补一处边界',
    issues: [
      {
        problem: '主题边界重复',
        topic_keys: ['topic-a', 'topic-b'],
        revision_direction: '合并重复段落'
      }
    ]
  })

  assert.match(formatExpertFinalReview(review), /全书需要补一处边界/)
  assert.match(formatExpertFinalReview(review), /涉及主题：`topic-a`、`topic-b`/)
  assert.equal(formatExpertFinalReview('旧项目全书检查结果'), '旧项目全书检查结果')
})
