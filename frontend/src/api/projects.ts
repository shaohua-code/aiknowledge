import request from './request'

/** 项目实体 */
export interface Project {
  id: string
  code: string
  name: string
  description?: string
  status: 'active' | 'disabled'
  createdAt: string
  updatedAt?: string
}

/** 创建项目入参 */
export interface CreateProjectPayload {
  code: string
  name: string
  description?: string
}

/** 更新项目入参 */
export interface UpdateProjectPayload {
  name?: string
  description?: string
  status?: 'active' | 'disabled'
}

/** 项目 API Key 实体 */
export interface ProjectApiKey {
  id: string
  name: string
  environment: string
  scopes: string[]
  expiresAt?: string
  createdAt: string
  // 创建时返回的明文密钥，仅展示一次
  plaintextKey?: string
  // 列表返回的脱敏前缀
  keyPrefix?: string
}

/** 创建 API Key 入参 */
export interface CreateApiKeyPayload {
  name: string
  environment: string
  scopes: string[]
  expiresAt?: string
}

/** 查询项目列表参数 */
export interface ListProjectsParams {
  status?: string
}

// === 项目管理接口 ===

/** 获取项目列表 */
export async function listProjects(params?: ListProjectsParams): Promise<Project[]> {
  // 后端返回 { items: Project[] }，这里剥离外层 items
  const res = await request.get<{ items: Project[] }>('/v1/projects', { params })
  return res?.items ?? []
}

/** 获取项目详情 */
export function getProject(projectId: string) {
  return request.get<Project>(`/v1/projects/${projectId}`)
}

/** 创建项目 */
export function createProject(payload: CreateProjectPayload) {
  return request.post<Project>('/v1/projects', payload)
}

/** 更新项目 */
export function updateProject(projectId: string, payload: UpdateProjectPayload) {
  return request.patch<Project>(`/v1/projects/${projectId}`, payload)
}

/** 停用项目 */
export function disableProject(projectId: string) {
  return request.post<void>(`/v1/projects/${projectId}/disable`)
}

/** 启用项目 */
export function enableProject(projectId: string) {
  return request.post<void>(`/v1/projects/${projectId}/enable`)
}

// === 项目 API Key 接口 ===

/** 获取项目下的 API Key 列表 */
export function listProjectApiKeys(projectId: string) {
  return request.get<ProjectApiKey[]>(`/v1/projects/${projectId}/api-keys`)
}

/** 创建项目 API Key，返回的 plaintextKey 仅展示一次 */
export function createProjectApiKey(projectId: string, payload: CreateApiKeyPayload) {
  return request.post<ProjectApiKey>(`/v1/projects/${projectId}/api-keys`, payload)
}

/** 删除（吊销）项目 API Key */
export function revokeProjectApiKey(projectId: string, keyId: string) {
  return request.delete<void>(`/v1/projects/${projectId}/api-keys/${keyId}`)
}
