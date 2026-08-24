import { apiAdminDelete, apiAdminGet, apiAdminPost } from './base'
import { useUserStore } from '@/stores/user'

const BASE_URL = '/api/material-libraries'

/** 上传一份资料并持续报告浏览器上传进度。unitMode: model_confirm=模型切分单元, whole_file=整文件直接入库 */
export const uploadMaterialSourceFile = (libraryId, file, relativePath, onProgress = () => {}, unitMode = 'model_confirm') =>
  new Promise((resolve, reject) => {
    const formData = new FormData()
    formData.append('file', file, file.name)
    formData.append('relative_path', relativePath)
    formData.append('unit_mode', unitMode)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${BASE_URL}/${encodeURIComponent(libraryId)}/files`)
    Object.entries(useUserStore().getAuthHeaders()).forEach(([key, value]) => {
      xhr.setRequestHeader(key, value)
    })
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    xhr.onload = () => {
      let payload
      try {
        payload = xhr.responseText ? JSON.parse(xhr.responseText) : null
      } catch {
        reject(new Error('资料上传响应无法读取'))
        return
      }

      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload)
        return
      }
      reject(new Error(payload?.detail || `资料上传失败: HTTP ${xhr.status}`))
    }
    xhr.onerror = () => reject(new Error('资料上传失败，请检查网络连接'))
    xhr.send(formData)
  })

export const materialLibraryApi = {
  list: () => apiAdminGet(BASE_URL),
  create: (name) => apiAdminPost(BASE_URL, { name }),
  get: (libraryId) => apiAdminGet(`${BASE_URL}/${encodeURIComponent(libraryId)}`),
  createDirectory: (libraryId, name, parentId = null) =>
    apiAdminPost(`${BASE_URL}/${encodeURIComponent(libraryId)}/directories`, {
      name,
      parent_id: parentId
    }),
  deleteDirectory: (libraryId, directoryId) =>
    apiAdminDelete(
      `${BASE_URL}/${encodeURIComponent(libraryId)}/directories/${encodeURIComponent(directoryId)}`
    ),
  upload: uploadMaterialSourceFile,
  listUnits: (libraryId, fileId = '') => {
    const query = fileId ? `?file_id=${encodeURIComponent(fileId)}` : ''
    return apiAdminGet(`${BASE_URL}/${encodeURIComponent(libraryId)}/units${query}`)
  },
  getSourceText: (libraryId, fileId) =>
    apiAdminGet(
      `${BASE_URL}/${encodeURIComponent(libraryId)}/files/${encodeURIComponent(fileId)}/text`
    ),
  getUnitText: (libraryId, unitId) =>
    apiAdminGet(
      `${BASE_URL}/${encodeURIComponent(libraryId)}/units/${encodeURIComponent(unitId)}/text`
    ),
  retrySource: (libraryId, fileId) =>
    apiAdminPost(
      `${BASE_URL}/${encodeURIComponent(libraryId)}/files/${encodeURIComponent(fileId)}/retry`,
      {}
    ),
  retryUnits: (libraryId, fileId) =>
    apiAdminPost(
      `${BASE_URL}/${encodeURIComponent(libraryId)}/files/${encodeURIComponent(fileId)}/retry-units`,
      {}
    ),
  deleteSource: (libraryId, fileId) =>
    apiAdminDelete(
      `${BASE_URL}/${encodeURIComponent(libraryId)}/files/${encodeURIComponent(fileId)}`
    )
}

export default materialLibraryApi
