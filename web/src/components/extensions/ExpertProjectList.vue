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
          : '导入可靠材料，先确定学习方法，再逐份学习并由总编模型形成专家 Skill。'
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
      width="720px"
      :confirm-loading="creating"
      :mask-closable="!creating"
      :keyboard="!creating"
      ok-text="创建并准备材料"
      cancel-text="取消"
      @ok="submitProject"
      @cancel="resetCreateForm"
    >
      <a-form ref="createFormRef" :model="createForm" :rules="createRules" layout="vertical">
        <a-form-item label="项目名称" name="name">
          <a-input
            v-model:value="createForm.name"
            placeholder="例如：四柱判断方法"
            :maxlength="120"
          />
        </a-form-item>

        <a-form-item label="项目说明" name="description">
          <a-textarea
            v-model:value="createForm.description"
            :rows="3"
            :maxlength="500"
            placeholder="说明材料来源、希望继承的判断手艺和最终读者"
          />
        </a-form-item>

        <a-form-item label="学习模型" name="modelSpec">
          <ModelSelectorComponent
            :model_spec="createForm.modelSpec"
            size="middle"
            placeholder="使用系统默认模型"
            @select-model="selectModel"
          />
        </a-form-item>

        <a-form-item label="项目材料" name="files">
          <a-upload-dragger
            v-model:file-list="fileList"
            name="files"
            :multiple="true"
            :accept="acceptedMaterialTypes"
            :before-upload="selectFiles"
            :disabled="creating"
          >
            <div class="upload-prompt">
              <Upload :size="24" />
              <strong>选择文件或拖拽到此处</strong>
              <span>支持 Markdown、文本、PDF、Office、图片与 ZIP 材料包</span>
            </div>
          </a-upload-dragger>
        </a-form-item>

        <a-alert
          type="info"
          show-icon
          message="材料会先转换为可靠正文。无法完整阅读的材料会停在项目中等待处理，不会静默截断。"
        />
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ArrowRight, BookOpenCheck, LibraryBig, Plus, RefreshCw, Upload } from 'lucide-vue-next'

import { expertApi } from '@/apis/expert_api'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
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
const fileList = ref([])

const acceptedMaterialTypes = [
  '.txt',
  '.md',
  '.docx',
  '.html',
  '.htm',
  '.json',
  '.csv',
  '.xls',
  '.xlsx',
  '.pdf',
  '.pptx',
  '.jpg',
  '.jpeg',
  '.png',
  '.bmp',
  '.tiff',
  '.tif',
  '.zip'
].join(',')

const createForm = reactive({
  name: '',
  description: '',
  modelSpec: ''
})

const createRules = {
  name: [{ required: true, whitespace: true, message: '请输入项目名称', trigger: 'blur' }],
  files: [
    {
      validator: () =>
        fileList.value.length ? Promise.resolve() : Promise.reject(new Error('请至少选择一份材料')),
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
}

/** 更新创建表单选择的模型。 */
const selectModel = (modelSpec) => {
  createForm.modelSpec = modelSpec
}

/** 由 Ant Upload 保存待提交文件，不在此处提前上传。 */
const selectFiles = () => {
  queueMicrotask(() => createFormRef.value?.clearValidate(['files']))
  return false
}

/** 清空创建项目表单。 */
const resetCreateForm = () => {
  createForm.name = ''
  createForm.description = ''
  createForm.modelSpec = configStore.config?.default_model || ''
  fileList.value = []
  createFormRef.value?.clearValidate()
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
    const files = fileList.value.map((item) => item.originFileObj || item)
    const response = await expertApi.create({
      name: createForm.name.trim(),
      description: createForm.description.trim(),
      modelSpec: createForm.modelSpec,
      files
    })
    const project = response?.item
    if (!project?.project_id) throw new Error('创建结果缺少专家项目')
    message.success('专家项目已创建，正在准备材料')
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

.upload-prompt {
  display: flex;
  min-height: 132px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--gray-600);

  strong {
    color: var(--gray-900);
    font-size: 14px;
  }

  span {
    max-width: 420px;
    font-size: 12px;
    line-height: 1.6;
    text-align: center;
  }
}

:deep(.info-card-footer) {
  display: flex;
  align-items: center;
  gap: 8px;
}

:deep(.ant-upload-list) {
  max-height: 180px;
  overflow-y: auto;
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
