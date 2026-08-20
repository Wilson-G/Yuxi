import { apiAdminDelete, apiAdminPost, apiGet } from './base'

const BASE_URL = '/api/tasks'

export const taskerApi = {
  fetchTasks: async (params = {}) => {
    const query = new URLSearchParams(params).toString()
    const url = query ? `${BASE_URL}?${query}` : BASE_URL
    return apiGet(url)
  },

  fetchTaskDetail: async (taskId) => {
    return apiGet(`${BASE_URL}/${taskId}`)
  },

  cancelTask: async (taskId) => {
    return apiAdminPost(`${BASE_URL}/${taskId}/cancel`, {})
  },

  deleteTask: async (taskId) => {
    return apiAdminDelete(`${BASE_URL}/${taskId}`)
  }
}
