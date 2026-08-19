import { apiAdminDelete, apiAdminGet, apiAdminPost } from './base'

const BASE_URL = '/api/expert-projects'

/** 从资料库已经就绪的自然材料单元创建专家项目。 */
export const createExpertProject = async ({
  name,
  description = '',
  modelSpec = '',
  materialUnitIds
}) =>
  apiAdminPost(BASE_URL, {
    name,
    description,
    model_spec: modelSpec,
    material_unit_ids: materialUnitIds
  })

/** 列出当前用户创建的专家项目。 */
export const listExpertProjects = async () => apiAdminGet(BASE_URL)

/** 读取项目页面恢复当前阶段所需的正式事实。 */
export const getExpertProject = async (projectId) =>
  apiAdminGet(`${BASE_URL}/${encodeURIComponent(projectId)}`)

/** 读取一份已经转换完成的可靠正文。 */
export const getExpertMaterialText = async (projectId, materialId) =>
  apiAdminGet(
    `${BASE_URL}/${encodeURIComponent(projectId)}/materials/${encodeURIComponent(materialId)}/text`
  )

/** 选择代表性材料并开始或重新执行项目定向。 */
export const startExpertOrientation = async (projectId, representativeMaterialIds, feedback = '') =>
  apiAdminPost(`${BASE_URL}/${encodeURIComponent(projectId)}/orientation`, {
    representative_material_ids: representativeMaterialIds,
    feedback
  })

/** 确认当前项目方法版本并开始逐份学习。 */
export const confirmExpertMethod = async (projectId) =>
  apiAdminPost(`${BASE_URL}/${encodeURIComponent(projectId)}/method/confirm`, {})

/** 只重跑一份正文转换失败的材料。 */
export const retryExpertMaterialConversion = async (projectId, materialId) =>
  apiAdminPost(
    `${BASE_URL}/${encodeURIComponent(projectId)}/materials/${encodeURIComponent(materialId)}/retry-conversion`,
    {}
  )

/** 只重跑一份学习或 Review 失败的材料。 */
export const retryExpertMaterialLearning = async (projectId, materialId) =>
  apiAdminPost(
    `${BASE_URL}/${encodeURIComponent(projectId)}/materials/${encodeURIComponent(materialId)}/retry-learning`,
    {}
  )

/** 记录用户明确排除一份需要处理的材料。 */
export const excludeExpertMaterial = async (projectId, materialId, reason) =>
  apiAdminPost(
    `${BASE_URL}/${encodeURIComponent(projectId)}/materials/${encodeURIComponent(materialId)}/exclude`,
    { reason }
  )

/** 按用户反馈重新生成主题方案和讲义小样。 */
export const reviseExpertOrganization = async (projectId, feedback) =>
  apiAdminPost(`${BASE_URL}/${encodeURIComponent(projectId)}/organization/revise`, { feedback })

/** 确认当前知识组织版本并开始总编成书。 */
export const confirmExpertOrganization = async (projectId) =>
  apiAdminPost(`${BASE_URL}/${encodeURIComponent(projectId)}/organization/confirm`, {})

/** 只重跑一篇进入需要处理的讲义。 */
export const retryExpertTopic = async (projectId, topicId) =>
  apiAdminPost(
    `${BASE_URL}/${encodeURIComponent(projectId)}/topics/${encodeURIComponent(topicId)}/retry`,
    {}
  )

/** 重新执行全书收口。 */
export const retryExpertCompilation = async (projectId) =>
  apiAdminPost(`${BASE_URL}/${encodeURIComponent(projectId)}/compilation/retry`, {})

/** 读取候选 Skill 中允许发布的成品。 */
export const getExpertPreview = async (projectId) =>
  apiAdminGet(`${BASE_URL}/${encodeURIComponent(projectId)}/preview`)

/** 在用户完成预览后手动启用专家 Skill。 */
export const activateExpertProject = async (projectId) =>
  apiAdminPost(`${BASE_URL}/${encodeURIComponent(projectId)}/activate`, {})

/** 暂停项目，后台恢复扫描会等待用户显式恢复。 */
export const pauseExpertProject = async (projectId) =>
  apiAdminPost(`${BASE_URL}/${encodeURIComponent(projectId)}/pause`, {})

/** 从项目正式事实恢复暂停项目。 */
export const resumeExpertProject = async (projectId) =>
  apiAdminPost(`${BASE_URL}/${encodeURIComponent(projectId)}/resume`, {})

/** 卸载已启用 Skill，但保留项目候选成品。 */
export const uninstallExpertProject = async (projectId) =>
  apiAdminPost(`${BASE_URL}/${encodeURIComponent(projectId)}/uninstall`, {})

/** 删除项目记录和编译产物，不删除资料库材料。 */
export const deleteExpertProject = async (projectId) =>
  apiAdminDelete(`${BASE_URL}/${encodeURIComponent(projectId)}`)

export const expertApi = {
  create: createExpertProject,
  list: listExpertProjects,
  get: getExpertProject,
  getMaterialText: getExpertMaterialText,
  startOrientation: startExpertOrientation,
  confirmMethod: confirmExpertMethod,
  retryMaterialConversion: retryExpertMaterialConversion,
  retryMaterialLearning: retryExpertMaterialLearning,
  excludeMaterial: excludeExpertMaterial,
  reviseOrganization: reviseExpertOrganization,
  confirmOrganization: confirmExpertOrganization,
  retryTopic: retryExpertTopic,
  retryCompilation: retryExpertCompilation,
  preview: getExpertPreview,
  activate: activateExpertProject,
  pause: pauseExpertProject,
  resume: resumeExpertProject,
  uninstall: uninstallExpertProject,
  delete: deleteExpertProject
}

export default expertApi
