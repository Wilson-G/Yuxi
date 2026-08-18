<template>
  <div class="expert-packages extension-page-root">
    <PageShoulder v-model:search="searchQuery" search-placeholder="搜索专家包...">
      <template #actions>
        <a-button class="lucide-icon-btn" :disabled="loading" @click="fetchPackages">
          <RefreshCw :size="14" />
        </a-button>
        <a-button type="primary" class="lucide-icon-btn" @click="uploadOpen = true">
          <Upload :size="15" />
          <span>上传材料包</span>
        </a-button>
      </template>
    </PageShoulder>

    <div v-if="loading && packages.length === 0" class="expert-loading">
      <a-spin />
    </div>

    <ResourceEmptyState
      v-else-if="filteredPackages.length === 0"
      :title="searchQuery ? '没有匹配的专家包' : '还没有专家包'"
      :description="searchQuery ? '换个关键词试试。' : '上传 Markdown 或 ZIP 材料包后，可以批量编译为可检索 Skill。'"
      :icon="BookOpenCheck"
    >
      <template v-if="!searchQuery" #actions>
        <a-button type="primary" class="lucide-icon-btn" @click="uploadOpen = true">
          <Upload :size="15" />
          上传材料包
        </a-button>
      </template>
    </ResourceEmptyState>

    <ExtensionCardGrid v-else :min-width="320">
      <InfoCard
        v-for="item in filteredPackages"
        :key="item.id"
        :title="item.name"
        :subtitle="packageSubtitle(item)"
        :description="item.error || item.description || '暂无描述'"
        :tags="packageTags(item)"
        :class="{ 'expert-card-error': item.status === 'failed' }"
      >
        <template #icon>
          <BookOpenCheck :size="20" />
        </template>
        <template #actions>
          <a-button
            v-if="canCompile(item)"
            size="small"
            class="lucide-icon-btn"
            :loading="isBusy(item.id)"
            @click.stop="compilePackage(item)"
          >
            <Hammer :size="14" />
            编译
          </a-button>
          <a-button
            v-else-if="item.active"
            size="small"
            class="lucide-icon-btn"
            :loading="isBusy(item.id)"
            @click.stop="deactivatePackage(item)"
          >
            <Pause :size="14" />
            停用
          </a-button>
          <a-button
            v-else-if="item.status === 'compiled'"
            type="primary"
            size="small"
            class="lucide-icon-btn"
            :loading="isBusy(item.id)"
            @click.stop="activatePackage(item)"
          >
            <Play :size="14" />
            激活
          </a-button>
          <a-button v-else size="small" disabled>
            {{ statusLabel(item.status) }}
          </a-button>
        </template>
        <template #card-more-action-corner>
          <a-menu @click="({ key }) => key === 'delete' && confirmDeletePackage(item)">
            <a-menu-item key="delete" danger :disabled="item.active">
              <span class="lucide-menu-item">
                <Trash2 :size="14" />
                <span>删除专家包</span>
              </span>
            </a-menu-item>
          </a-menu>
        </template>
      </InfoCard>
    </ExtensionCardGrid>

    <a-modal
      v-model:open="uploadOpen"
      title="上传专家材料包"
      ok-text="上传"
      cancel-text="取消"
      :confirm-loading="uploading"
      :ok-button-props="{ disabled: !canSubmitUpload }"
      @ok="submitUpload"
      @cancel="resetUpload"
    >
      <a-form layout="vertical" class="expert-upload-form">
        <a-form-item label="专家包名称" required>
          <a-input v-model:value="uploadForm.name" placeholder="例如：亚马逊运营专家" />
        </a-form-item>
        <a-form-item label="使用范围">
          <a-textarea
            v-model:value="uploadForm.description"
            :rows="3"
            placeholder="说明这套材料适合回答什么问题，以及不应回答什么。"
          />
        </a-form-item>
        <a-form-item label="材料文件" required>
          <a-upload
            accept=".md,.markdown,.zip"
            :before-upload="selectUploadFile"
            :file-list="uploadFileList"
            :max-count="1"
            @remove="removeUploadFile"
          >
            <a-button class="lucide-icon-btn">
              <FileArchive :size="14" />
              选择 Markdown 或 ZIP
            </a-button>
          </a-upload>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  BookOpenCheck,
  FileArchive,
  Hammer,
  Pause,
  Play,
  RefreshCw,
  Trash2,
  Upload
} from 'lucide-vue-next'
import { expertApi } from '@/apis/expert_api'
import { useTaskerStore } from '@/stores/tasker'
import ExtensionCardGrid from './ExtensionCardGrid.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import ResourceEmptyState from '@/components/shared/ResourceEmptyState.vue'
import { formatFileSize } from '@/utils/file_utils'

const taskerStore = useTaskerStore()
const packages = ref([])
const loading = ref(false)
const uploading = ref(false)
const uploadOpen = ref(false)
const selectedFile = ref(null)
const searchQuery = ref('')
const busyIds = ref([])
const uploadForm = reactive({ name: '', description: '' })
let refreshTimer = null

const uploadFileList = computed(() => (selectedFile.value ? [selectedFile.value] : []))
const canSubmitUpload = computed(() => Boolean(uploadForm.name.trim() && selectedFile.value))
const filteredPackages = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return packages.value
  return packages.value.filter((item) =>
    [item.name, item.description, item.skill_slug].filter(Boolean).join(' ').toLowerCase().includes(query)
  )
})
const hasRunningPackages = computed(() =>
  packages.value.some((item) => ['queued', 'processing'].includes(item.status))
)

const STATUS_LABELS = {
  uploaded: '待编译',
  queued: '已排队',
  processing: '编译中',
  compiled: '可激活',
  active: '已激活',
  failed: '失败'
}

const statusLabel = (status) => STATUS_LABELS[status] || status || '未知'
const canCompile = (item) => ['uploaded', 'failed'].includes(item.status)
const isBusy = (id) => busyIds.value.includes(id)

const packageSubtitle = (item) => {
  const parts = [`${item.source_files || 0} 份材料`]
  if (item.source_bytes) parts.push(formatFileSize(item.source_bytes))
  if (item.indexed_documents) parts.push(`${item.indexed_documents} 份已索引`)
  return parts.join(' · ')
}

const packageTags = (item) => [
  {
    name: statusLabel(item.status),
    color: item.status === 'failed' ? 'red' : item.active ? 'green' : 'gray'
  },
  { name: 'Skill 检索', color: 'gray' },
  ...(item.skill_slug ? [{ name: item.skill_slug, color: 'gray' }] : [])
]

const fetchPackages = async () => {
  loading.value = true
  try {
    const response = await expertApi.list()
    packages.value = response?.items || []
    syncPolling()
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '加载专家包失败')
  } finally {
    loading.value = false
  }
}

const selectUploadFile = (file) => {
  selectedFile.value = file
  return false
}

const removeUploadFile = () => {
  selectedFile.value = null
  return true
}

const resetUpload = () => {
  uploadForm.name = ''
  uploadForm.description = ''
  selectedFile.value = null
}

const submitUpload = async () => {
  if (!canSubmitUpload.value) return
  uploading.value = true
  try {
    await expertApi.create(uploadForm.name.trim(), uploadForm.description.trim(), selectedFile.value)
    message.success('材料包已保存')
    uploadOpen.value = false
    resetUpload()
    await fetchPackages()
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '上传材料包失败')
  } finally {
    uploading.value = false
  }
}

const compilePackage = async (item) => {
  busyIds.value.push(item.id)
  try {
    const result = await expertApi.compile(item.id)
    taskerStore.registerQueuedTask({
      task_id: result.task_id,
      name: `编译专家包：${item.name}`,
      task_type: 'expert_package_compile',
      message: result.message,
      payload: { package_id: item.id }
    })
    message.success(result.message)
    await fetchPackages()
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '提交编译失败')
  } finally {
    busyIds.value = busyIds.value.filter((id) => id !== item.id)
  }
}

const activatePackage = async (item) => {
  busyIds.value.push(item.id)
  try {
    await expertApi.activate(item.id)
    message.success('专家包已激活，Agent 新会话可以使用')
    await fetchPackages()
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '激活专家包失败')
  } finally {
    busyIds.value = busyIds.value.filter((id) => id !== item.id)
  }
}

const deactivatePackage = async (item) => {
  busyIds.value.push(item.id)
  try {
    await expertApi.deactivate(item.id)
    message.success('专家包已停用')
    await fetchPackages()
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '停用专家包失败')
  } finally {
    busyIds.value = busyIds.value.filter((id) => id !== item.id)
  }
}

const confirmDeletePackage = (item) => {
  if (item.active) return
  Modal.confirm({
    title: '删除专家包',
    content: `确定删除“${item.name}”及其已安装 Skill 吗？原始材料和编译产物都会被删除。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      busyIds.value.push(item.id)
      try {
        await expertApi.delete(item.id)
        message.success('专家包已删除')
        await fetchPackages()
      } catch (error) {
        message.error(error?.response?.data?.detail || error.message || '删除专家包失败')
        throw error
      } finally {
        busyIds.value = busyIds.value.filter((id) => id !== item.id)
      }
    }
  })
}

const syncPolling = () => {
  if (hasRunningPackages.value && !refreshTimer) {
    refreshTimer = setInterval(fetchPackages, 4000)
  } else if (!hasRunningPackages.value && refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

onMounted(fetchPackages)
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

defineExpose({ loading, fetchPackages })
</script>

<style scoped lang="less">
@import '@/assets/css/extensions.less';

.expert-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 280px;
}

.expert-card-error :deep(.info-card-mini-desc) {
  color: var(--color-error-700);
}

.expert-upload-form {
  padding-top: 8px;
}

@media (max-width: 767px) {
  :deep(.page-shoulder) {
    align-items: stretch;
  }
}
</style>
