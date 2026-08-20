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

test('批量学习状态可筛选，并说明修订由后台继续处理', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')
  const projectUtils = await readFile(new URL('../../src/utils/expertProject.js', import.meta.url), 'utf8')

  assert.match(view, /const materialFilter = ref\('all'\)/)
  assert.match(view, /v-for="option in materialFilterOptions"/)
  assert.match(view, /每份材料在后台完成笔记、Review\s+和最多两次自动修订/)
  assert.match(projectUtils, /review_revise: '自动修订中'/)
})

test('项目定向页面按当前材料数计算代表材料范围', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')

  assert.match(view, /Math\.min\(4, selectableMaterials\.value\.length\)/)
  assert.match(view, /Math\.min\(20, selectableMaterials\.value\.length\)/)
  assert.match(view, /选择 \{\{ representativeRangeLabel \}\}代表性材料/)
  assert.match(view, /selectedRepresentativeIds\.value\.length < minimumRepresentativeMaterials\.value/)
})

test('方法小样的系统失败可用原代表材料重新试跑', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')

  assert.match(view, /project\.status === 'needs_attention'/)
  assert.match(view, /重新试跑当前方法/)
  assert.match(view, /expertApi\.startOrientation\(projectId\.value, project\.value\.representative_material_ids/)
})

test('小样内容问题可由用户接受后开始逐份学习', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')
  const api = await readFile(new URL('../../src/apis/expert_api.js', import.meta.url), 'utf8')

  assert.match(view, /hasMethodSampleConcerns/)
  assert.match(view, /接受小样问题并开始学习/)
  assert.match(view, /expertApi\.acceptMethodWithConcerns\(projectId\.value\)/)
  assert.match(api, /method\/accept-with-concerns/)
})

test('等待确认时保留用户反馈调整方法的入口', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')

  assert.match(view, /project\.status === 'awaiting_confirmation'/)
  assert.match(view, /按意见调整学习方向/)
  assert.match(view, /project\.learning_plan \|\| ''/)
  assert.match(view, /historySelection === 'orientation-plan'/)
})

test('方法确认后自动校准一次，不开放第二个方法确认点', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')
  const api = await readFile(new URL('../../src/apis/expert_api.js', import.meta.url), 'utf8')

  assert.match(view, /根据小样 Review 中的可泛化问题自动校准一次并复跑原小样/)
  assert.match(view, /确认并开始校准/)
  assert.doesNotMatch(view, /calibrateMethod/)
  assert.doesNotMatch(api, /method\/calibrate/)
})

test('已完成项目可回看五个阶段的真实产物', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')
  const topicReader = await readFile(
    new URL('../../src/components/extensions/ExpertTopicReader.vue', import.meta.url),
    'utf8'
  )

  assert.match(view, /reviewStage === 'orientation'/)
  assert.match(view, /reviewStage === 'material_learning'/)
  assert.match(view, /reviewStage === 'organization'/)
  assert.match(view, /reviewStage === 'handout_compilation'/)
  assert.match(view, /historyMaterialStatusItems/)
  assert.match(view, /'review_revise'/)
  assert.match(view, /<ExpertTopicReader/)
  assert.match(topicReader, /讲义目录/)
  assert.match(topicReader, /selectedTopic\.handout/)
  assert.match(topicReader, /@media \(max-width: 768px\)/)
  assert.match(topicReader, /grid-template-columns: minmax\(0, 1fr\)/)
  assert.match(view, /finalReviewMarkdown/)
})

test('专家成品正文隐藏重复元数据并限制阅读宽度', async () => {
  const view = await readFile(new URL('../../src/views/ExpertProjectView.vue', import.meta.url), 'utf8')

  assert.match(view, /tab="专家说明"/)
  assert.match(view, /tab="使用导航"/)
  assert.match(view, /\.primary-preview-document/)
  assert.match(view, /:deep\(\.frontmatter-card\)/)
  assert.match(view, /max-width: 760px/)
})
