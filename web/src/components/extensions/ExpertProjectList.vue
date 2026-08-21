<template>
  <div class="expert-project-list extension-page-root">
    <PageShoulder v-model:search="searchQuery" search-placeholder="搜索专家项目...">
      <template #actions>
        <a-tooltip title="刷新项目">
          <a-button
            class="lucide-icon-btn"
            aria-label="刷新专家项目"
            :disabled="loading"
            @click="fetchProjects"
          >
            <RefreshCw :size="14" />
          </a-button>
        </a-tooltip>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreateModal">
          <Plus :size="15" />
          <span>新建专家项目</span>
        </a-button>
      </template>
    </PageShoulder>

    <div v-if="loading && projects.length === 0" class="project-loading">
      <a-spin />
    </div>

    <ResourceEmptyState
      v-else-if="filteredProjects.length === 0"
      :title="searchQuery ? '没有匹配的专家项目' : '还没有专家项目'"
      :description="
        searchQuery
          ? '换个关键词试试。'
          : '先在资料库处理材料，再选择就绪材料单元创建项目并确定学习方法。'
      "
      :icon="LibraryBig"
    >
      <template v-if="!searchQuery" #actions>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreateModal">
          <Plus :size="15" />
          新建专家项目
        </a-button>
      </template>
    </ResourceEmptyState>

    <ExtensionCardGrid v-else :min-width="320">
      <InfoCard
        v-for="project in filteredProjects"
        :key="project.project_id"
        :title="project.name"
        :subtitle="getExpertStageLabel(project.stage)"
        :description="
          project.error_message || project.message || project.description || '等待项目开始'
        "
        :tags="projectTags(project)"
        :status="projectStatus(project)"
        @click="openProject(project)"
      >
        <template #icon>
          <BookOpenCheck :size="18" />
        </template>
        <template #info>
          <div class="project-progress">
            <div class="project-progress-meta">
              <span>主流程</span>
              <span>{{ Math.round(getExpertProjectProgress(project)) }}%</span>
            </div>
            <a-progress
              :percent="Math.round(getExpertProjectProgress(project))"
              :show-info="false"
              :stroke-width="4"
            />
          </div>
        </template>
        <template #footer>
          <span class="project-updated">更新于 {{ formatProjectTime(project.updated_at) }}</span>
          <a-button type="link" size="small" class="open-project-action">
            打开项目
            <ArrowRight :size="13" />
          </a-button>
        </template>
      </InfoCard>
    </ExtensionCardGrid>

    <a-modal
      v-model:open="createOpen"
      title="创建专家项目"
      width="840px"
      :style="{ maxWidth: 'calc(100vw - 32px)' }"
      :body-style="{ maxHeight: 'calc(100vh - 210px)', overflowY: 'auto' }"
      :confirm-loading="creating"
      :mask-closable="!creating"
      :keyboard="!creating"
      ok-text="创建项目"
      cancel-text="取消"
      @ok="submitProject"
      @cancel="resetCreateForm"
    >
      <a-form ref="createFormRef" :model="createForm" :rules="createRules" layout="vertical">
        <a-form-item label="项目名称" name="name">
          <a-input v-model:value="createForm.name" placeholder="输入项目名称" :maxlength="120" />
        </a-form-item>

        <a-form-item
          label="专家背景与学习目标"
          name="description"
          extra="这段信息会作为项目定向的上下文，帮助模型判断材料范围、学习重点和最终使用场景。"
        >
          <a-textarea
            v-model:value="createForm.description"
            :rows="3"
            :maxlength="500"
            placeholder="例如：面向亚马逊运营人员，学习广告投放的判断条件、调优顺序和适用边界"
          />
        </a-form-item>

        <a-form-item label="学习模型" name="modelSpec">
          <ModelSelectorComponent
            :model_spec="createForm.modelSpec"
            size="middle"
            placeholder="使用系统默认模型"
            @select-model="selectModel"
          />
          <div class="model-settings-help">
            <span>创建前请确认模型已设置上下文长度。</span>
            <RouterLink
              :to="{ path: '/agent-manage', query: { tab: 'providers' } }"
              class="model-settings-link"
            >
              <Settings2 :size="13" />
              打开模型供应商配置
            </RouterLink>
          </div>
        </a-form-item>

        <a-form-item label="项目材料" name="materialUnitIds">
          <MaterialUnitSelector
            ref="materialSelectorRef"
            v-model="createForm.materialUnitIds"
            @open-library="openMaterialLibrary"
          />
        </a-form-item>

        <a-alert
          type="info"
          show-icon
          message="项目只关联已经处理完成的材料单元，不会重复上传、OCR 或解析来源文件。"
        />
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ArrowRight, BookOpenCheck, LibraryBig, Plus, RefreshCw, Settings2 } from 'lucide-vue-next'

import { expertApi } from '@/apis/expert_api'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import MaterialUnitSelector from '@/components/material-library/MaterialUnitSelector.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import ResourceEmptyState from '@/components/shared/ResourceEmptyState.vue'
import { useConfigStore } from '@/stores/config'
import {
  getExpertProjectProgress,
  getExpertStageLabel,
  getExpertStatusLabel,
  getExpertStatusTone
} from '@/utils/expertProject'
import { formatRelative } from '@/utils/time'
import ExtensionCardGrid from './ExtensionCardGrid.vue'

const router = useRouter()
const configStore = useConfigStore()
const projects = ref([])
const loading = ref(false)
const searchQuery = ref('')
const createOpen = ref(false)
const creating = ref(false)
const createFormRef = ref(null)
const materialSelectorRef = ref(null)

const createForm = reactive({
  name: '',
  description: '',
  modelSpec: '',
  materialUnitIds: []
})

const createRules = {
  name: [{ required: true, whitespace: true, message: '请输入项目名称', trigger: 'blur' }],
  description: [
    {
      required: true,
      whitespace: true,
      message: '请补充专家背景与学习目标，模型会据此定向学习材料',
      trigger: 'blur'
    }
  ],
  materialUnitIds: [
    {
      validator: () =>
        createForm.materialUnitIds.length
          ? Promise.resolve()
          : Promise.reject(new Error('请至少选择一份已处理材料')),
      trigger: 'change'
    }
  ]
}

const filteredProjects = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  if (!keyword) return projects.value
  return projects.value.filter((project) =>
    [project.name, project.description, project.message]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(keyword)
  )
})

/** 加载当前用户的专家项目。 */
const fetchProjects = async () => {
  loading.value = true
  try {
    const response = await expertApi.list()
    projects.value = response?.items || []
  } catch (error) {
    message.error(error.message || '加载专家项目失败')
  } finally {
    loading.value = false
  }
}

/** 返回项目卡片使用的状态标签。 */
const projectStatus = (project) => ({
  label: getExpertStatusLabel(project.status),
  level:
    { error: 'error', warning: 'warning', success: 'success' }[
      getExpertStatusTone(project.status)
    ] || 'info'
})

/** 返回项目卡片上有直接业务含义的标签。 */
const projectTags = (project) => {
  const tags = [{ name: getExpertStageLabel(project.stage), color: 'gray' }]
  if (project.method_version > 0)
    tags.push({ name: `方法 v${project.method_version}`, color: 'gray' })
  if (project.skill_slug) tags.push({ name: '专家 Skill', color: 'green' })
  return tags
}

/** 用相对时间展示最近一次项目更新。 */
const formatProjectTime = (value) => formatRelative(value) || '-'

/** 进入项目详情页。 */
const openProject = (project) => {
  router.push({
    name: 'ExtensionExpertProjectDetail',
    params: { projectId: project.project_id }
  })
}

/** 打开创建弹窗并使用当前系统默认模型。 */
const openCreateModal = async () => {
  if (!configStore.config?.default_model) {
    try {
      await configStore.refreshConfig()
    } catch {
      // 后端在 model_spec 为空时仍会使用系统默认模型。
    }
  }
  createForm.modelSpec = configStore.config?.default_model || ''
  createOpen.value = true
  await nextTick()
  await materialSelectorRef.value?.load()
}

/** 更新创建表单选择的模型。 */
const selectModel = (modelSpec) => {
  createForm.modelSpec = modelSpec
}

/** 清空创建项目表单。 */
const resetCreateForm = () => {
  createForm.name = ''
  createForm.description = ''
  createForm.modelSpec = configStore.config?.default_model || ''
  createForm.materialUnitIds = []
  createFormRef.value?.clearValidate()
}

/** 资料不足时转到独立资料库处理入口。 */
const openMaterialLibrary = () => {
  createOpen.value = false
  resetCreateForm()
  router.push({ path: '/extensions', query: { tab: 'materials' } })
}

/** 创建项目并进入以项目记录为事实源的详情页。 */
const submitProject = async () => {
  try {
    await createFormRef.value?.validate()
  } catch {
    return
  }

  creating.value = true
  try {
    const response = await expertApi.create({
      name: createForm.name.trim(),
      description: createForm.description.trim(),
      modelSpec: createForm.modelSpec,
      materialUnitIds: createForm.materialUnitIds
    })
    const project = response?.item
    if (!project?.project_id) throw new Error('创建结果缺少专家项目')
    message.success('专家项目已创建，请选择代表材料')
    createOpen.value = false
    resetCreateForm()
    openProject(project)
  } catch (error) {
    message.error(error.message || '创建专家项目失败')
  } finally {
    creating.value = false
  }
}

onMounted(fetchProjects)

defineExpose({ loading, fetchProjects })
</script>

<style scoped lang="less">
@import '@/assets/css/extensions.less';

.expert-project-list {
  min-width: 0;
}

.project-loading {
  display: flex;
  min-height: 280px;
  align-items: center;
  justify-content: center;
}

.model-settings-help {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  margin-top: 6px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 20px;
}

.model-settings-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--main-color);
}

.project-progress {
  width: 100%;

  &-meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 4px;
    color: var(--gray-600);
    font-size: 12px;
  }
}

.project-updated {
  min-width: 0;
  overflow: hidden;
  color: var(--gray-500);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.open-project-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  padding-right: 0;
}

:deep(.info-card-footer) {
  display: flex;
  align-items: center;
  gap: 8px;
}

@media (max-width: 768px) {
  :deep(.page-shoulder) {
    align-items: stretch;
    flex-direction: column;
  }

  :deep(.page-shoulder-left),
  :deep(.page-shoulder-right),
  :deep(.search-input) {
    width: 100%;
  }

  :deep(.page-shoulder-right) {
    justify-content: flex-end;
  }
}
</style>
