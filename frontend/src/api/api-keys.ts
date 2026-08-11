import request from './request'

// === 项目 API Key 相关类型定义 ===

/** API Key 状态 */
export type ApiKeyStatus = 'active' | 'revoked' | 'expired'

/** API Key 实体 */
export interface ApiKey {
  id: string
  name: string
  environment: string
  /** 脱敏前缀（列表展示用） */
  keyPrefix?: string
  scopes: string[]
  /** 上次使用时间 */
  lastUsedAt?: string
  /** 过期时间 */
  expiresAt?: string
  status: ApiKeyStatus
  createdAt: string
  /** 创建/轮换时返回的明文密钥，仅展示一次 */
  plaintextKey?: string
}

/** 创建 API Key 入参 */
export interface CreateApiKeyPayload {
  name: string
  environment: string
  scopes: string[]
  expiresAt?: string
}

/** API Key 列表查询参数 */
export interface ListApiKeysParams {
  status?: ApiKeyStatus
}

// === 项目 API Key 接口（路径包含 projectId） ===

/** 创建项目 API Key，返回 plaintextKey 仅展示一次 */
export function createApiKey(projectId: string, payload: CreateApiKeyPayload) {
  return request.post<ApiKey>(`/v1/projects/${projectId}/api-keys`, payload)
}

/** 查询项目 API Key 列表 */
export function listApiKeys(projectId: string, params?: ListApiKeysParams) {
  return request.get<ApiKey[]>(`/v1/projects/${projectId}/api-keys`, { params })
}

/** 删除（吊销）API Key */
export function revokeApiKey(projectId: string, keyId: string) {
  return request.delete<void>(`/v1/projects/${projectId}/api-keys/${keyId}`)
}

/** 轮换 API Key，返回新的明文密钥（仅展示一次） */
export function rotateApiKey(projectId: string, keyId: string) {
  return request.post<ApiKey>(`/v1/projects/${projectId}/api-keys/${keyId}/rotate`)
}
