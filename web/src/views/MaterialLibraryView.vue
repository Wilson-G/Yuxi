<template>
  <div class="material-library-view extension-page-root">
    <PageShoulder v-model:search="searchQuery" search-placeholder="搜索当前目录...">
      <template #search>
        <a-select
          v-model:value="activeLibraryId"
          :options="libraryOptions"
          :loading="loadingLibraries"
          placeholder="选择资料库"
          class="library-switcher"
        />
        <a-input
          v-model:value="searchQuery"
          allow-clear
          placeholder="搜索当前目录..."
          class="material-search"
        >
          <template #prefix><Search :size="14" /></template>
        </a-input>
      </template>
      <template #actions>
        <a-tooltip title="新建资料库">
          <a-button class="lucide-icon-btn" aria-label="新建资料库" @click="libraryModalOpen = true">
            <LibraryBig :size="15" />
          </a-button>
        </a-tooltip>
        <a-button
          :disabled="!activeLibraryId"
          class="lucide-icon-btn"
          @click="directoryModalOpen = true"
        >
          <FolderPlus :size="15" />
          新建目录
        </a-button>
        <a-dropdown :disabled="!activeLibraryId">
          <a-button type="primary" class="lucide-icon-btn">
            <Upload :size="15" />
            导入资料
            <ChevronDown :size="14" />
          </a-button>
          <template #overlay>
            <a-menu @click="openUpload">
              <a-menu-item key="file"><FileUp :size="15" /> 上传文件</a-menu-item>
              <a-menu-item key="folder"><FolderUp :size="15" /> 上传文件夹</a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
      </template>
    </PageShoulder>

    <div v-if="loadingLibraries && !libraries.length" class="page-loading"><a-spin /></div>

    <ResourceEmptyState
      v-else-if="!libraries.length"
      title="还没有资料库"
      description="先建立资料库，再导入需要学习的文件或文件夹。"
      :icon="LibraryBig"
    >
      <template #actions>
        <a-button type="primary" class="lucide-icon-btn" @click="libraryModalOpen = true">
          <Plus :size="15" />
          新建资料库
        </a-button>
      </template>
    </ResourceEmptyState>

    <div v-else class="library-content">
      <a-alert v-if="loadError" type="error" show-icon :message="loadError">
        <template #action><a-button size="small" @click="loadLibrary">重试</a-button></template>
      </a-alert>

      <FileBrowserTable
        :rows="currentRows"
        :columns="columns"
        :breadcrumbs="breadcrumbs"
        :loading="loadingLibrary"
        row-key="key"
        :scroll="{ x: 760 }"
        refreshable
        :refreshing="loadingLibrary"
        empty-text="当前目录暂无资料"
        @open-row="openRow"
        @breadcrumb-click="openBreadcrumb"
        @refresh="loadLibrary"
      >
        <template #name="{ row }">
          <div class="file-name-cell">
            <FileTypeIcon :name="row.name" :is-dir="row.is_dir" />
            <div>
              <strong :title="row.relative_path || row.name">{{ row.name }}</strong>
              <span v-if="row.error_message" class="file-error">{{ row.error_message }}</span>
            </div>
          </div>
        </template>
        <template #cell-kind="{ row }">
          {{ row.is_dir ? '目录' : row.file_type?.replace('.', '').toUpperCase() || '文件' }}
        </template>
        <template #status="{ row }">
          <template v-if="!row.is_dir">
            <span
              class="status-badge"
              :class="`tone-${getMaterialStatusTone(getMaterialFileStatus(row))}`"
            >
              {{ getMaterialFileStatusLabel(row) }}
            </span>
            <a-progress
              v-if="row.status === 'processing'"
              :percent="row.progress || 0"
              :show-info="false"
              :stroke-width="3"
              class="file-progress"
            />
          </template>
          <span v-else>-</span>
        </template>
        <template #cell-size="{ row }">
          {{ row.is_dir ? '-' : formatMaterialFileSize(row.file_size) }}
        </template>
        <template #cell-lines="{ row }">
          {{ row.is_dir || !row.line_count ? '-' : `${row.line_count} 行` }}
        </template>
        <template #row-actions="{ row }">
          <template v-if="row.is_dir">
            <a-tooltip title="删除空目录">
              <a-button
                type="text"
                danger
                class="table-icon-button"
                aria-label="删除目录"
                @click="confirmDeleteDirectory(row)"
              >
                <Trash2 :size="15" />
              </a-button>
            </a-tooltip>
          </template>
          <template v-else>
            <a-tooltip v-if="row.status === 'ready'" title="预览正文和材料单元">
              <a-button
                type="text"
                class="table-icon-button"
                aria-label="预览资料"
                @click="openPreview(row)"
              >
                <Eye :size="15" />
              </a-button>
            </a-tooltip>
            <a-tooltip v-if="row.status === 'needs_attention'" title="重新处理文件">
              <a-button
                type="text"
                class="table-icon-button"
                :loading="actionKey === `retry:${row.file_id}`"
                aria-label="重试文件处理"
                @click="retrySource(row)"
              >
                <RotateCcw :size="15" />
              </a-button>
            </a-tooltip>
            <a-tooltip
              v-if="row.status === 'ready' && row.unit_status === 'needs_attention'"
              title="重新确认材料单元"
            >
              <a-button
                type="text"
                class="table-icon-button"
                :loading="actionKey === `retry-units:${row.file_id}`"
                aria-label="重试材料单元确认"
                @click="retryUnits(row)"
              >
                <RefreshCw :size="15" />
              </a-button>
            </a-tooltip>
            <a-tooltip title="删除资料">
              <a-button
                type="text"
                danger
                class="table-icon-button"
                :disabled="row.status === 'processing' || row.unit_status === 'processing'"
                aria-label="删除资料"
                @click="confirmDeleteSource(row)"
              >
                <Trash2 :size="15" />
              </a-button>
            </a-tooltip>
          </template>
        </template>
      </FileBrowserTable>
    </div>

    <a-modal
      v-model:open="libraryModalOpen"
      title="新建资料库"
      ok-text="创建"
      cancel-text="取消"
      :confirm-loading="actionKey === 'create-library'"
      @ok="createLibrary"
      @cancel="newLibraryName = ''"
    >
      <a-form layout="vertical">
        <a-form-item label="资料库名称" required>
          <a-input v-model:value="newLibraryName" :maxlength="255" placeholder="例如：课程原始材料" />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="directoryModalOpen"
      title="新建目录"
      ok-text="创建"
      cancel-text="取消"
      :confirm-loading="actionKey === 'create-directory'"
      @ok="createDirectory"
      @cancel="newDirectoryName = ''"
    >
      <a-form layout="vertical">
        <a-form-item label="目录名称" required>
          <a-input v-model:value="newDirectoryName" :maxlength="512" placeholder="输入目录名称" />
        </a-form-item>
        <span class="form-hint">创建位置：{{ currentDirectoryPath || '根目录' }}</span>
      </a-form>
    </a-modal>

    <MaterialUploadModal
      v-model:open="uploadModalOpen"
      :library-id="activeLibraryId"
      :directory-path="currentDirectoryPath"
      :initial-mode="uploadInitialMode"
      @complete="scheduleLibraryRefresh"
    />

    <a-modal
      v-model:open="previewOpen"
      :title="previewFile?.relative_path || '资料预览'"
      width="1000px"
      :footer="null"
      :destroy-on-close="true"
    >
      <div v-if="previewLoading" class="preview-loading"><a-spin /></div>
      <a-alert v-else-if="previewError" type="error" show-icon :message="previewError">
        <template #action><a-button size="small" @click="reloadPreview">重试</a-button></template>
      </a-alert>
      <a-tabs v-else v-model:active-key="previewTab" class="preview-tabs">
        <a-tab-pane key="source" tab="完整正文">
          <div class="markdown-preview-shell">
            <MarkdownPreview :content="sourcePreview?.content || ''" compact />
          </div>
        </a-tab-pane>
        <a-tab-pane key="units" :tab="`材料单元 ${previewUnits.length}`">
          <div v-if="previewUnits.length" class="unit-preview-layout">
            <aside class="unit-list" aria-label="材料单元">
              <button
                v-for="unit in previewUnits"
                :key="unit.unit_id"
                type="button"
                :class="{ active: selectedUnitId === unit.unit_id }"
                @click="loadUnitPreview(unit)"
              >
                <strong>{{ unit.title }}</strong>
                <span>L{{ unit.start_line }}–L{{ unit.end_line }}</span>
              </button>
            </aside>
            <div class="unit-reader">
              <div v-if="unitPreviewLoading" class="preview-loading"><a-spin /></div>
              <a-alert v-else-if="unitPreviewError" type="error" show-icon :message="unitPreviewError" />
              <MarkdownPreview v-else :content="unitPreview?.content || ''" compact />
            </div>
          </div>
          <a-empty v-else description="这份文件还没有就绪的材料单元" />
        </a-tab-pane>
      </a-tabs>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  ChevronDown,
  Eye,
  FileUp,
  FolderPlus,
  FolderUp,
  LibraryBig,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  Upload
} from 'lucide-vue-next'

import { materialLibraryApi } from '@/apis/material_library_api'
import FileBrowserTable from '@/components/common/FileBrowserTable.vue'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import MaterialUploadModal from '@/components/material-library/MaterialUploadModal.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import ResourceEmptyState from '@/components/shared/ResourceEmptyState.vue'
import {
  formatMaterialFileSize,
  getMaterialFileStatus,
  getMaterialFileStatusLabel,
  getMaterialStatusTone
} from '@/utils/materialLibrary'

const libraries = ref([])
const activeLibraryId = ref('')
const libraryDetail = ref(null)
const currentDirectoryId = ref(null)
const loadingLibraries = ref(false)
const loadingLibrary = ref(false)
const loadError = ref('')
const searchQuery = ref('')
const actionKey = ref('')
const libraryModalOpen = ref(false)
const directoryModalOpen = ref(false)
const newLibraryName = ref('')
const newDirectoryName = ref('')
const uploadModalOpen = ref(false)
const uploadInitialMode = ref('file')
const previewOpen = ref(false)
const previewFile = ref(null)
const previewLoading = ref(false)
const previewError = ref('')
const previewTab = ref('source')
const sourcePreview = ref(null)
const previewUnits = ref([])
const selectedUnitId = ref('')
const unitPreview = ref(null)
const unitPreviewLoading = ref(false)
const unitPreviewError = ref('')
let pollingTimer = null
let refreshTimer = null
let libraryRequestInFlight = false

const columns = [
  { title: '名称', key: 'name', dataIndex: 'name' },
  { title: '类型', key: 'kind', width: 90, responsive: ['md'] },
  { title: '状态', key: 'status', width: 150 },
  { title: '大小', key: 'size', width: 100, responsive: ['lg'] },
  { title: '正文', key: 'lines', width: 100, responsive: ['lg'] },
  { title: '', key: 'actions', width: 120, fixed: 'right' }
]

const libraryOptions = computed(() =>
  libraries.value.map((library) => ({ label: library.name, value: library.library_id }))
)
const directories = computed(() => libraryDetail.value?.directories || [])
const files = computed(() => libraryDetail.value?.files || [])
const currentDirectory = computed(() =>
  directories.value.find((directory) => directory.directory_id === currentDirectoryId.value)
)
const currentDirectoryPath = computed(() => currentDirectory.value?.relative_path || '')
const breadcrumbs = computed(() => {
  const items = [{ key: 'root', name: libraryDetail.value?.name || '资料', directoryId: null }]
  if (!currentDirectory.value) return items
  const byId = new Map(directories.value.map((directory) => [directory.directory_id, directory]))
  const parents = []
  let cursor = currentDirectory.value
  while (cursor) {
    parents.unshift(cursor)
    cursor = cursor.parent_id ? byId.get(cursor.parent_id) : null
  }
  return items.concat(
    parents.map((directory) => ({
      key: directory.directory_id,
      name: directory.name,
      path: directory.relative_path,
      directoryId: directory.directory_id
    }))
  )
})
const currentRows = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  const rows = [
    ...directories.value
      .filter((directory) => (directory.parent_id || null) === currentDirectoryId.value)
      .map((directory) => ({ ...directory, key: `dir:${directory.directory_id}`, is_dir: true })),
    ...files.value
      .filter((file) => (file.directory_id || null) === currentDirectoryId.value)
      .map((file) => ({ ...file, key: `file:${file.file_id}`, name: file.original_filename, is_dir: false }))
  ]
  if (!keyword) return rows
  return rows.filter((row) =>
    [row.name, row.relative_path, row.error_message]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(keyword)
  )
})
const hasProcessingFiles = computed(() =>
  files.value.some(
    (file) =>
      ['pending', 'processing'].includes(file.status) ||
      (file.status === 'ready' && ['pending', 'processing'].includes(file.unit_status))
  )
)

/** 读取管理员可管理的资料库。 */
const loadLibraries = async () => {
  loadingLibraries.value = true
  loadError.value = ''
  try {
    const response = await materialLibraryApi.list()
    libraries.value = response?.items || []
    if (!libraries.value.some((library) => library.library_id === activeLibraryId.value)) {
      activeLibraryId.value = libraries.value[0]?.library_id || ''
    }
  } catch (error) {
    loadError.value = error.message || '加载资料库失败'
  } finally {
    loadingLibraries.value = false
  }
}

/** 读取当前资料库目录和文件事实。 */
const loadLibrary = async ({ silent = false } = {}) => {
  if (!activeLibraryId.value || libraryRequestInFlight) return
  const requestedLibraryId = activeLibraryId.value
  libraryRequestInFlight = true
  if (!silent) loadingLibrary.value = true
  loadError.value = ''
  try {
    const response = await materialLibraryApi.get(requestedLibraryId)
    if (activeLibraryId.value !== requestedLibraryId) return
    libraryDetail.value = response?.item || null
    if (
      currentDirectoryId.value &&
      !directories.value.some((directory) => directory.directory_id === currentDirectoryId.value)
    ) {
      currentDirectoryId.value = null
    }
  } catch (error) {
    if (activeLibraryId.value === requestedLibraryId) {
      loadError.value = error.message || '加载资料库内容失败'
    }
  } finally {
    if (!silent) loadingLibrary.value = false
    libraryRequestInFlight = false
    if (activeLibraryId.value && activeLibraryId.value !== requestedLibraryId) {
      void loadLibrary()
    }
  }
}

const runAction = async (key, action, successMessage) => {
  if (actionKey.value) return false
  actionKey.value = key
  try {
    await action()
    message.success(successMessage)
    await loadLibrary({ silent: true })
    return true
  } catch (error) {
    message.error(error.message || '操作失败')
    return false
  } finally {
    actionKey.value = ''
  }
}

const createLibrary = async () => {
  const name = newLibraryName.value.trim()
  if (!name) {
    message.warning('请输入资料库名称')
    return
  }
  actionKey.value = 'create-library'
  try {
    const response = await materialLibraryApi.create(name)
    await loadLibraries()
    activeLibraryId.value = response?.item?.library_id || activeLibraryId.value
    libraryModalOpen.value = false
    newLibraryName.value = ''
    message.success('资料库已创建')
  } catch (error) {
    message.error(error.message || '创建资料库失败')
  } finally {
    actionKey.value = ''
  }
}

const createDirectory = async () => {
  const name = newDirectoryName.value.trim()
  if (!name) {
    message.warning('请输入目录名称')
    return
  }
  const completed = await runAction(
    'create-directory',
    () => materialLibraryApi.createDirectory(activeLibraryId.value, name, currentDirectoryId.value),
    '目录已创建'
  )
  if (completed) {
    directoryModalOpen.value = false
    newDirectoryName.value = ''
  }
}

const openUpload = ({ key }) => {
  uploadInitialMode.value = key
  uploadModalOpen.value = true
}

const scheduleLibraryRefresh = () => {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = setTimeout(() => void loadLibrary({ silent: true }), 250)
}

const openRow = (row) => {
  if (row.is_dir) {
    currentDirectoryId.value = row.directory_id
    searchQuery.value = ''
    return
  }
  if (row.status === 'ready') openPreview(row)
}

const openBreadcrumb = ({ item }) => {
  currentDirectoryId.value = item.directoryId || null
  searchQuery.value = ''
}

const retrySource = (file) =>
  runAction(
    `retry:${file.file_id}`,
    () => materialLibraryApi.retrySource(activeLibraryId.value, file.file_id),
    `正在重新处理《${file.original_filename}》`
  )

const retryUnits = (file) =>
  runAction(
    `retry-units:${file.file_id}`,
    () => materialLibraryApi.retryUnits(activeLibraryId.value, file.file_id),
    `正在重新确认《${file.original_filename}》的材料单元`
  )

const confirmDeleteSource = (file) => {
  Modal.confirm({
    title: '删除这份资料？',
    content: `将删除“${file.relative_path}”的原文件、正文和材料单元。已被专家项目引用时不会删除。`,
    okText: '删除资料',
    okType: 'danger',
    cancelText: '取消',
    onOk: () =>
      runAction(
        `delete:${file.file_id}`,
        () => materialLibraryApi.deleteSource(activeLibraryId.value, file.file_id),
        '资料已删除'
      )
  })
}

const confirmDeleteDirectory = (directory) => {
  Modal.confirm({
    title: '删除空目录？',
    content: `目录“${directory.relative_path}”必须为空才能删除。`,
    okText: '删除目录',
    okType: 'danger',
    cancelText: '取消',
    onOk: () =>
      runAction(
        `delete-directory:${directory.directory_id}`,
        () => materialLibraryApi.deleteDirectory(activeLibraryId.value, directory.directory_id),
        '目录已删除'
      )
  })
}

/** 同时加载规范化全文和当前文件已经就绪的自然材料单元。 */
const openPreview = async (file) => {
  previewFile.value = file
  previewOpen.value = true
  previewTab.value = 'source'
  await reloadPreview()
}

const reloadPreview = async () => {
  if (!previewFile.value) return
  previewLoading.value = true
  previewError.value = ''
  sourcePreview.value = null
  previewUnits.value = []
  selectedUnitId.value = ''
  unitPreview.value = null
  try {
    const [sourceResponse, unitResponse] = await Promise.all([
      materialLibraryApi.getSourceText(activeLibraryId.value, previewFile.value.file_id),
      materialLibraryApi.listUnits(activeLibraryId.value, previewFile.value.file_id)
    ])
    sourcePreview.value = sourceResponse?.item || null
    previewUnits.value = unitResponse?.items || []
    if (previewUnits.value.length) await loadUnitPreview(previewUnits.value[0])
  } catch (error) {
    previewError.value = error.message || '加载资料预览失败'
  } finally {
    previewLoading.value = false
  }
}

const loadUnitPreview = async (unit) => {
  const requestedUnitId = unit.unit_id
  selectedUnitId.value = requestedUnitId
  unitPreviewLoading.value = true
  unitPreviewError.value = ''
  unitPreview.value = null
  try {
    const response = await materialLibraryApi.getUnitText(activeLibraryId.value, unit.unit_id)
    if (selectedUnitId.value === requestedUnitId) unitPreview.value = response?.item || null
  } catch (error) {
    if (selectedUnitId.value === requestedUnitId) {
      unitPreviewError.value = error.message || '加载材料单元失败'
    }
  } finally {
    if (selectedUnitId.value === requestedUnitId) unitPreviewLoading.value = false
  }
}

const syncPolling = () => {
  if (hasProcessingFiles.value && !pollingTimer) {
    pollingTimer = setInterval(() => void loadLibrary({ silent: true }), 4000)
    return
  }
  if (!hasProcessingFiles.value && pollingTimer) {
    clearInterval(pollingTimer)
    pollingTimer = null
  }
}

watch(activeLibraryId, () => {
  currentDirectoryId.value = null
  libraryDetail.value = null
  searchQuery.value = ''
  void loadLibrary()
})
watch(hasProcessingFiles, syncPolling, { immediate: true })

onMounted(loadLibraries)
onBeforeUnmount(() => {
  if (pollingTimer) clearInterval(pollingTimer)
  if (refreshTimer) clearTimeout(refreshTimer)
})

defineExpose({ loading: computed(() => loadingLibraries.value || loadingLibrary.value), loadLibrary })
</script>

<style scoped lang="less">
@import '@/assets/css/extensions.less';

.material-library-view {
  min-width: 0;
}

.library-switcher {
  width: min(260px, 32vw);
}

.material-search {
  width: min(300px, 34vw);
}

.page-loading,
.preview-loading {
  display: flex;
  min-height: 220px;
  align-items: center;
  justify-content: center;
}

.library-content {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  gap: 12px;
  padding: 16px var(--page-padding) var(--page-padding);
}

.library-content :deep(.file-browser-table-shell) {
  flex: 1;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.file-name-cell {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;

  > div {
    min-width: 0;
  }

  strong {
    display: block;
    overflow: hidden;
    color: var(--gray-900);
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.file-error {
  display: -webkit-box;
  margin-top: 2px;
  overflow: hidden;
  color: var(--color-error-700);
  font-size: 12px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 12px;
  font-weight: 500;

  &.tone-active {
    background: var(--color-info-50);
    color: var(--color-info-700);
  }

  &.tone-success {
    background: var(--color-success-50);
    color: var(--color-success-700);
  }

  &.tone-error {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }
}

.file-progress {
  width: 92px;
  margin-top: 3px;
}

.table-icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
}

.form-hint {
  color: var(--gray-500);
  font-size: 12px;
}

.markdown-preview-shell {
  max-height: min(68vh, 720px);
  overflow-y: auto;
  padding: 4px 12px 20px;
}

.unit-preview-layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  min-height: 480px;
  max-height: min(68vh, 720px);
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.unit-list {
  overflow-y: auto;
  border-right: 1px solid var(--gray-150);
  background: var(--gray-25);

  button {
    display: flex;
    width: 100%;
    min-width: 0;
    flex-direction: column;
    align-items: flex-start;
    gap: 3px;
    padding: 11px 12px;
    border: 0;
    border-bottom: 1px solid var(--gray-100);
    background: transparent;
    color: var(--gray-900);
    text-align: left;
    cursor: pointer;

    &:hover,
    &:focus-visible {
      background: var(--gray-50);
    }

    &.active {
      background: var(--main-30);
      color: var(--main-800);
    }

    strong {
      max-width: 100%;
      overflow: hidden;
      font-weight: 500;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    span {
      color: var(--gray-500);
      font-size: 12px;
    }
  }
}

.unit-reader {
  min-width: 0;
  overflow-y: auto;
  padding: 16px 20px;
}

@media (max-width: 768px) {
  :deep(.page-shoulder),
  :deep(.page-shoulder-left),
  :deep(.page-shoulder-right) {
    align-items: stretch;
    flex-direction: column;
  }

  .library-switcher,
  .material-search,
  :deep(.page-shoulder-left),
  :deep(.page-shoulder-right) {
    width: 100%;
  }

  .unit-preview-layout {
    grid-template-columns: 1fr;
  }

  .unit-list {
    max-height: 180px;
    border-right: 0;
    border-bottom: 1px solid var(--gray-150);
  }
}
</style>
