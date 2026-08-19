export const TASK_STATUS_CONFIG = {
  pending: { label: '等待中', active: true, terminal: false, cancelable: true, progress: 'active' },
  queued: { label: '已排队', active: true, terminal: false, cancelable: true, progress: 'active' },
  running: { label: '进行中', active: true, terminal: false, cancelable: true, progress: 'active' },
  waiting: { label: '等待操作', active: false, terminal: false, cancelable: false, progress: 'normal' },
  success: { label: '已完成', active: false, terminal: true, cancelable: false, progress: 'success' },
  failed: { label: '失败', active: false, terminal: true, cancelable: false, progress: 'exception' },
  cancelled: { label: '已取消', active: false, terminal: true, cancelable: false, progress: 'normal' }
}

export const ACTIVE_TASK_STATUSES = new Set(
  Object.entries(TASK_STATUS_CONFIG)
    .filter(([, config]) => config.active)
    .map(([status]) => status)
)
export const FAILED_TASK_STATUSES = new Set(['failed', 'cancelled'])

export const isActiveTaskStatus = (status) => Boolean(TASK_STATUS_CONFIG[status]?.active)

/** 等待操作独立于进行中和已完成筛选。 */
export const filterTasksByStatus = (tasks, filter) => {
  if (filter === 'active') return tasks.filter((task) => isActiveTaskStatus(task.status))
  if (filter === 'waiting') return tasks.filter((task) => task.status === 'waiting')
  if (filter === 'success') return tasks.filter((task) => task.status === 'success')
  if (filter === 'failed') return tasks.filter((task) => FAILED_TASK_STATUSES.has(task.status))
  return tasks
}
