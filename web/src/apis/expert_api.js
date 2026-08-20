import { apiGet, apiPost } from './base'

const BASE_URL = '/api/expert-projects'

/** 创建专家项目并上传本轮需要学习的完整材料。 */
export const createExpertProject = async ({ name, description = '', modelSpec = '', files }) => {
  const formData = new FormData()
  formData.append('name', name)
  formData.append('description', description)
  formData.append('model_spec', modelSpec)
  files.forEach((file) => formData.append('files', file))
  return apiPost(BASE_URL, formData)
}

/** 列出当前用户创建的专家项目。 */
export const listExpertProjects = async () => apiGet(BASE_URL)

/** 读取项目页面恢复当前阶段所需的正式事实。 */
export const getExpertProject = async (projectId) =>
  apiGet(`${BASE_URL}/${encodeURIComponent(projectId)}`)

/** 读取一份已经转换完成的可靠正文。 */
export const getExpertMaterialText = async (projectId, materialId) =>
  apiGet(
    `${BASE_URL}/${encodeURIComponent(projectId)}/materials/${encodeURIComponent(materialId)}/text`
  )

/** 选择代表性材料并开始或重新执行项目定向。 */
export const startExpertOrientation = async (projectId, representativeMaterialIds, feedback = '') =>
  apiPost(`${BASE_URL}/${encodeURIComponent(projectId)}/orientation`, {
    representative_material_ids: representativeMaterialIds,
    feedback
  })

/** 确认当前项目方法版本并开始逐份学习。 */
export const confirmExpertMethod = async (projectId) =>
  apiPost(`${BASE_URL}/${encodeURIComponent(projectId)}/method/confirm`, {})

/** 用户接受小样内容问题，按当前方法直接开始批量学习。 */
export const acceptExpertMethodWithConcerns = async (projectId) =>
  apiPost(`${BASE_URL}/${encodeURIComponent(projectId)}/method/accept-with-concerns`, {})

/** 只重跑一份正文转换失败的材料。 */
export const retryExpertMaterialConversion = async (projectId, materialId) =>
  apiPost(
    `${BASE_URL}/${encodeURIComponent(projectId)}/materials/${encodeURIComponent(materialId)}/retry-conversion`,
    {}
  )

/** 只重跑一份学习或 Review 失败的材料。 */
export const retryExpertMaterialLearning = async (projectId, materialId) =>
  apiPost(
    `${BASE_URL}/${encodeURIComponent(projectId)}/materials/${encodeURIComponent(materialId)}/retry-learning`,
    {}
  )

/** 记录用户明确排除一份需要处理的材料。 */
export const excludeExpertMaterial = async (projectId, materialId, reason) =>
  apiPost(
    `${BASE_URL}/${encodeURIComponent(projectId)}/materials/${encodeURIComponent(materialId)}/exclude`,
    { reason }
  )

/** 按用户反馈重新生成主题方案和讲义小样。 */
export const reviseExpertOrganization = async (projectId, feedback) =>
  apiPost(`${BASE_URL}/${encodeURIComponent(projectId)}/organization/revise`, { feedback })

/** 确认当前知识组织版本并开始总编成书。 */
export const confirmExpertOrganization = async (projectId) =>
  apiPost(`${BASE_URL}/${encodeURIComponent(projectId)}/organization/confirm`, {})

/** 只重跑一篇进入需要处理的讲义。 */
export const retryExpertTopic = async (projectId, topicId) =>
  apiPost(
    `${BASE_URL}/${encodeURIComponent(projectId)}/topics/${encodeURIComponent(topicId)}/retry`,
    {}
  )

/** 重新执行全书收口。 */
export const retryExpertCompilation = async (projectId) =>
  apiPost(`${BASE_URL}/${encodeURIComponent(projectId)}/compilation/retry`, {})

/** 读取候选 Skill 中允许发布的成品。 */
export const getExpertPreview = async (projectId) =>
  apiGet(`${BASE_URL}/${encodeURIComponent(projectId)}/preview`)

/** 在用户完成预览后手动启用专家 Skill。 */
export const activateExpertProject = async (projectId) =>
  apiPost(`${BASE_URL}/${encodeURIComponent(projectId)}/activate`, {})

export const expertApi = {
  create: createExpertProject,
  list: listExpertProjects,
  get: getExpertProject,
  getMaterialText: getExpertMaterialText,
  startOrientation: startExpertOrientation,
  confirmMethod: confirmExpertMethod,
  acceptMethodWithConcerns: acceptExpertMethodWithConcerns,
  retryMaterialConversion: retryExpertMaterialConversion,
  retryMaterialLearning: retryExpertMaterialLearning,
  excludeMaterial: excludeExpertMaterial,
  reviseOrganization: reviseExpertOrganization,
  confirmOrganization: confirmExpertOrganization,
  retryTopic: retryExpertTopic,
  retryCompilation: retryExpertCompilation,
  preview: getExpertPreview,
  activate: activateExpertProject
}

export default expertApi
