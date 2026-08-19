const SOURCE_STATUS_LABELS = {
  pending: '等待处理',
  processing: '生成正文',
  ready: '正文就绪',
  needs_attention: '需要处理'
}

const UNIT_STATUS_LABELS = {
  pending: '等待确认单元',
  processing: '确认材料单元',
  ready: '单元就绪',
  needs_attention: '单元需要处理'
}

/** 创建逐文件上传队列，失败项只在用户明确重试时重新执行。 */
export const createMaterialUploadQueue = ({ upload, concurrency = 4, onChange = () => {} }) => {
  const items = []
  let activeCount = 0
  let nextId = 1

  const notify = () => onChange(items.map((item) => ({ ...item })))

  const process = () => {
    while (activeCount < concurrency) {
      const item = items.find((candidate) => candidate.status === 'queued')
      if (!item) return

      item.status = 'uploading'
      activeCount += 1
      notify()
      Promise.resolve(upload(item.file, item.relativePath, (progress) => {
        item.progress = Math.max(0, Math.min(100, Number(progress) || 0))
        notify()
      }))
        .then((response) => {
          item.status = 'success'
          item.progress = 100
          item.response = response
        })
        .catch((error) => {
          item.status = 'failed'
          item.error = error?.message || '上传失败'
        })
        .finally(() => {
          activeCount -= 1
          notify()
          process()
        })
    }
  }

  return {
    enqueue(files) {
      Array.from(files || []).forEach((file) => {
        items.push({
          id: String(file.uid || `upload-${nextId++}`),
          file,
          name: file.name,
          relativePath: file.webkitRelativePath || file.name,
          status: 'queued',
          progress: 0,
          error: '',
          response: null
        })
      })
      notify()
      process()
    },
    retry(itemId) {
      const item = items.find((candidate) => candidate.id === itemId)
      if (!item || item.status !== 'failed') return false
      item.status = 'queued'
      item.progress = 0
      item.error = ''
      notify()
      process()
      return true
    },
    getItems() {
      return items.map((item) => ({ ...item }))
    }
  }
}

/** 返回文件处理和自然单元确认合并后的用户状态。 */
export const getMaterialFileStatus = (file = {}) => {
  if (file.status !== 'ready') return file.status || 'pending'
  return file.unit_status || 'pending'
}

export const getMaterialFileStatusLabel = (file = {}) => {
  if (file.status !== 'ready') return SOURCE_STATUS_LABELS[file.status] || file.status || '未知'
  return UNIT_STATUS_LABELS[file.unit_status] || file.unit_status || '未知'
}

export const getMaterialStatusTone = (status) => {
  if (status === 'needs_attention') return 'error'
  if (status === 'ready') return 'success'
  if (status === 'pending') return 'neutral'
  return 'active'
}

/** 资料库接口只返回 ready 单元；前端仍按事实字段收紧选择范围。 */
export const getReadyMaterialUnits = (units = []) => units.filter((unit) => unit.status === 'ready')

export const formatMaterialFileSize = (size) => {
  const bytes = Number(size || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
