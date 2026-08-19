export const EXPERT_STAGE_ITEMS = [
  { key: 'orientation', label: '确定怎么学' },
  { key: 'material_learning', label: '逐份学材料' },
  { key: 'organization', label: '确定怎么组织' },
  { key: 'handout_compilation', label: '总编成书' },
  { key: 'release', label: '预览并启用' }
]

const PROJECT_STATUS_LABELS = {
  pending: '准备中',
  preparing: '准备中',
  ready: '已就绪',
  running: '进行中',
  awaiting_confirmation: '等待确认',
  needs_attention: '需要处理',
  paused: '已暂停',
  enabled: '已启用'
}

const MATERIAL_STATUS_LABELS = {
  pending: '等待处理',
  converting: '转换正文',
  ready: '正文就绪',
  learning: '学习中',
  completed: '准备完成',
  preparing_evidence: '准备证据',
  evidence_ready: '证据就绪',
  writing: '总编撰写中',
  passed: '已通过',
  review_revise: '自动修订中',
  no_value: '不入选',
  needs_attention: '需要处理',
  excluded: '已排除'
}

const TERMINAL_MATERIAL_STATUSES = new Set([
  'ready',
  'passed',
  'no_value',
  'needs_attention',
  'excluded'
])
const TERMINAL_TOPIC_STATUSES = new Set(['passed', 'needs_attention'])

/** 返回当前业务阶段在主流程中的位置。 */
export const getExpertStageIndex = (stage) => {
  const index = EXPERT_STAGE_ITEMS.findIndex((item) => item.key === stage)
  return index < 0 ? 0 : index
}

/** 返回当前业务阶段的用户可读名称。 */
export const getExpertStageLabel = (stage) =>
  EXPERT_STAGE_ITEMS.find((item) => item.key === stage)?.label || '专家项目'

/** 返回项目状态的用户可读名称。 */
export const getExpertStatusLabel = (status) => PROJECT_STATUS_LABELS[status] || status || '未知'

/** 返回材料状态的用户可读名称。 */
export const getExpertMaterialStatusLabel = (status) =>
  MATERIAL_STATUS_LABELS[status] || status || '未知'

/** 返回状态对应的克制视觉语义。 */
export const getExpertStatusTone = (status) => {
  if (status === 'needs_attention') return 'error'
  if (status === 'awaiting_confirmation' || status === 'paused') return 'warning'
  if (
    status === 'ready' ||
    status === 'passed' ||
    status === 'completed' ||
    status === 'evidence_ready' ||
    status === 'enabled'
  )
    return 'success'
  if (
    status === 'running' ||
    status === 'preparing' ||
    status === 'learning' ||
    status === 'preparing_evidence' ||
    status === 'writing'
  )
    return 'active'
  return 'neutral'
}

/** 仅对后台确实仍在推进的项目启用详情轮询。 */
export const shouldPollExpertProject = (project) =>
  project?.status === 'running' || project?.status === 'preparing'

/** 当前项目是否允许直接删除。 */
export const canDeleteExpertProject = (project) =>
  Boolean(project) && !['running', 'preparing', 'enabled'].includes(project.status)

/** 已启用项目先卸载，其余非暂停项目均可显式暂停。 */
export const canPauseExpertProject = (project) =>
  Boolean(project) && !['paused', 'enabled'].includes(project.status)

/** 区分项目定向中的方法生成，避免误显示为正文转换。 */
export const isExpertMethodPreparationRunning = (project) =>
  project?.stage === 'orientation' && project?.status === 'running'

/** 计算材料阶段已经得到明确处理结果的比例。 */
export const getExpertMaterialProgress = (materials = []) => {
  if (!materials.length) return 0
  const completed = materials.filter((item) => TERMINAL_MATERIAL_STATUSES.has(item.status)).length
  return Math.round((completed / materials.length) * 100)
}

/** 计算讲义阶段已经通过或进入明确处理状态的比例。 */
export const getExpertTopicProgress = (topics = []) => {
  if (!topics.length) return 0
  const completed = topics.filter((item) => TERMINAL_TOPIC_STATUSES.has(item.status)).length
  return Math.round((completed / topics.length) * 100)
}

/** 根据项目正式事实给出主流程总体进度。 */
export const getExpertProjectProgress = (project = {}) => {
  const stage = project.stage
  if (stage === 'release') return 100
  if (stage === 'handout_compilation') {
    return 65 + Math.round(getExpertTopicProgress(project.topics) * 0.3)
  }
  if (stage === 'organization') return project.status === 'awaiting_confirmation' ? 65 : 55
  if (stage === 'material_learning') {
    return 20 + Math.round(getExpertMaterialProgress(project.materials) * 0.3)
  }
  if (project.status === 'awaiting_confirmation') return 20
  if (project.status === 'ready') return 10
  return project.status === 'preparing'
    ? Math.round(getExpertMaterialProgress(project.materials) / 10)
    : 15
}

/** 返回主题和成品预览都要持续展示的未纳入材料。 */
export const getExcludedExpertMaterials = (materials = []) =>
  materials.filter((item) => item.status === 'excluded' || item.status === 'no_value')

/** 排除当前材料后，项目仍须保留至少一份可能进入后续学习的材料。 */
export const canExcludeExpertMaterial = (materials = [], materialId) =>
  materials.some(
    (item) =>
      item.material_id !== materialId && item.status !== 'excluded' && item.status !== 'no_value'
  )

/** 组织反馈只在后端允许创建新组织版本的状态提交。 */
export const canReviseExpertOrganization = (status) =>
  status === 'awaiting_confirmation' || status === 'needs_attention'

/** 判断一份材料是否已经有可供用户核对的可靠正文。 */
export const canPreviewExpertMaterial = (material) =>
  Number(material?.line_count || 0) > 0 && !['pending', 'converting'].includes(material?.status)
