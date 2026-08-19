<template>
  <a-modal
    :open="open"
    title="导入资料"
    width="760px"
    :footer="null"
    :mask-closable="!hasPending"
    :keyboard="!hasPending"
    @cancel="closeModal"
  >
    <div class="upload-modal-content">
      <div class="upload-location">
        <FolderOpen :size="16" />
        <span>保存到</span>
        <strong :title="directoryPath || '根目录'">{{ directoryPath || '根目录' }}</strong>
      </div>

      <a-segmented v-model:value="uploadMode" :options="uploadModeOptions" />

      <a-upload-dragger
        :multiple="true"
        :directory="uploadMode === 'folder'"
        :accept="acceptedMaterialTypes"
        :show-upload-list="false"
        :custom-request="enqueueUpload"
        :disabled="hasPending"
      >
        <div class="upload-prompt">
          <FolderUp v-if="uploadMode === 'folder'" :size="24" />
          <FileUp v-else :size="24" />
          <strong>{{ uploadMode === 'folder' ? '选择资料文件夹' : '选择或拖入资料文件' }}</strong>
          <span>每份文件独立上传和处理，文件夹会保留原有相对路径。</span>
        </div>
      </a-upload-dragger>

      <section v-if="uploadItems.length" class="upload-queue" aria-live="polite">
        <header>
          <strong>上传队列</strong>
          <span>{{ uploadSummary }}</span>
        </header>
        <div class="upload-list">
          <div v-for="item in uploadItems" :key="item.id" class="upload-row">
            <FileText :size="16" />
            <div class="upload-copy">
              <strong :title="item.relativePath">{{ item.relativePath }}</strong>
              <a-progress
                v-if="item.status === 'queued' || item.status === 'uploading'"
                :percent="item.progress"
                :show-info="false"
                :stroke-width="4"
              />
              <span v-if="item.error" class="upload-error">{{ item.error }}</span>
            </div>
            <span class="queue-status" :class="`status-${item.status}`">
              {{ uploadStatusLabel(item.status) }}
            </span>
            <a-button
              v-if="item.status === 'failed'"
              size="small"
              class="lucide-icon-btn"
              @click="retryUpload(item.id)"
            >
              <RotateCcw :size="14" />
              重试
            </a-button>
          </div>
        </div>
      </section>

      <div class="modal-actions">
        <span v-if="hasPending">上传完成前请保持窗口打开。</span>
        <a-button :disabled="hasPending" @click="closeModal">完成</a-button>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, h, ref, watch } from 'vue'
import { FileText, FileUp, FolderOpen, FolderUp, RotateCcw } from 'lucide-vue-next'

import { materialLibraryApi } from '@/apis/material_library_api'
import { createMaterialUploadQueue } from '@/utils/materialLibrary'

const props = defineProps({
  open: { type: Boolean, default: false },
  libraryId: { type: String, required: true },
  directoryPath: { type: String, default: '' },
  initialMode: { type: String, default: 'file' }
})

const emit = defineEmits(['update:open', 'complete'])
const uploadMode = ref('file')
const uploadItems = ref([])
let queue = null
let completionEmitted = false

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
  '.tif'
].join(',')

const uploadModeOptions = [
  {
    value: 'file',
    label: h('span', { class: 'mode-option' }, [h(FileUp, { size: 15 }), '上传文件'])
  },
  {
    value: 'folder',
    label: h('span', { class: 'mode-option' }, [h(FolderUp, { size: 15 }), '上传文件夹'])
  }
]

const hasPending = computed(() =>
  uploadItems.value.some((item) => item.status === 'queued' || item.status === 'uploading')
)
const uploadSummary = computed(() => {
  const total = uploadItems.value.length
  const success = uploadItems.value.filter((item) => item.status === 'success').length
  const failed = uploadItems.value.filter((item) => item.status === 'failed').length
  return `共 ${total} 份 · 成功 ${success}${failed ? ` · 失败 ${failed}` : ''}`
})

const uploadStatusLabel = (status) =>
  ({ queued: '排队中', uploading: '上传中', success: '已上传', failed: '失败' })[status] || status

/** 为当前弹窗会话创建只处理单文件的上传队列。 */
const resetQueue = () => {
  uploadItems.value = []
  completionEmitted = false
  queue = createMaterialUploadQueue({
    upload: (file, relativePath, onProgress) => {
      const targetPath = [props.directoryPath, relativePath].filter(Boolean).join('/')
      return materialLibraryApi.upload(props.libraryId, file, targetPath, onProgress)
    },
    onChange: (items) => {
      uploadItems.value = items
      const finished = items.length > 0 && items.every((item) => ['success', 'failed'].includes(item.status))
      if (finished && items.some((item) => item.status === 'success') && !completionEmitted) {
        completionEmitted = true
        emit('complete')
      }
    }
  })
}

const enqueueUpload = ({ file }) => {
  completionEmitted = false
  queue.enqueue([file])
}

const retryUpload = (itemId) => {
  completionEmitted = false
  queue.retry(itemId)
}

const closeModal = () => {
  if (!hasPending.value) emit('update:open', false)
}

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return
    uploadMode.value = props.initialMode
    resetQueue()
  },
  { immediate: true }
)
</script>

<style scoped lang="less">
.upload-modal-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.upload-location {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--gray-600);
  font-size: 13px;

  strong {
    min-width: 0;
    overflow: hidden;
    color: var(--gray-900);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

:deep(.mode-option) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.upload-prompt {
  display: flex;
  min-height: 116px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--gray-600);

  strong {
    color: var(--gray-900);
  }

  span {
    font-size: 12px;
  }
}

.upload-queue {
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;

  > header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-height: 40px;
    padding: 0 14px;
    border-bottom: 1px solid var(--gray-100);
    background: var(--gray-25);
    font-size: 13px;

    span {
      color: var(--gray-500);
    }
  }
}

.upload-list {
  max-height: 280px;
  overflow-y: auto;
}

.upload-row {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  min-height: 52px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--gray-100);

  &:last-child {
    border-bottom: 0;
  }
}

.upload-copy {
  min-width: 0;

  strong {
    display: block;
    overflow: hidden;
    font-size: 13px;
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  :deep(.ant-progress) {
    margin: 3px 0 0;
  }
}

.upload-error {
  display: block;
  margin-top: 2px;
  color: var(--color-error-700);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.queue-status {
  color: var(--gray-500);
  font-size: 12px;
  white-space: nowrap;

  &.status-uploading {
    color: var(--color-info-700);
  }

  &.status-success {
    color: var(--color-success-700);
  }

  &.status-failed {
    color: var(--color-error-700);
  }
}

.modal-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;

  span {
    color: var(--gray-500);
    font-size: 12px;
  }
}

@media (max-width: 600px) {
  .upload-row {
    grid-template-columns: 18px minmax(0, 1fr) auto;
  }

  .upload-row :deep(.ant-btn) {
    grid-column: 2 / -1;
    justify-self: start;
  }
}
</style>
