import { apiAdminDelete, apiAdminGet, apiAdminPost } from './base'

const BASE_URL = '/api/experts'

export const expertApi = {
  list: () => apiAdminGet(BASE_URL),

  get: (packageId) => apiAdminGet(`${BASE_URL}/${encodeURIComponent(packageId)}`),

  create: (name, description, file) => {
    const formData = new FormData()
    formData.append('name', name)
    formData.append('description', description || '')
    formData.append('file', file)
    return apiAdminPost(BASE_URL, formData)
  },

  compile: (packageId) =>
    apiAdminPost(`${BASE_URL}/${encodeURIComponent(packageId)}/compile`, {}),

  activate: (packageId) =>
    apiAdminPost(`${BASE_URL}/${encodeURIComponent(packageId)}/activate`, {}),

  deactivate: (packageId) =>
    apiAdminPost(`${BASE_URL}/${encodeURIComponent(packageId)}/deactivate`, {}),

  delete: (packageId) => apiAdminDelete(`${BASE_URL}/${encodeURIComponent(packageId)}`)
}

export default expertApi
