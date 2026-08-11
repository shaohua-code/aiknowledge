import request from './request'

// === 项目设置相关类型定义 ===

/** 模型设置 */
export interface ModelSettings {
  /** 对话模型标识 */
  chatModel?: string
  /** 嵌入模型标识 */
  embeddingModel?: string
}

/** Web 搜索设置 */
export interface WebSearchSettings {
  /** 是否启用 Web 搜索 */
  webSearchEnabled?: boolean
  /** 允许域名白名单 */
  allowedDomains?: string[]
  /** 屏蔽域名黑名单 */
  blockedDomains?: string[]
}

/** 性能设置 */
export interface PerformanceSettings {
  /** 最大证据条数 */
  maxEvidence?: number
  /** 最大 token 数 */
  maxTokens?: number
  /** 超时秒数 */
  timeoutSeconds?: number
}

/** 项目 settings 字段（嵌入在项目详情中） */
export interface ProjectSettings {
  models?: ModelSettings
  webSearch?: WebSearchSettings
  performance?: PerformanceSettings
}

/** 项目详情（含 settings），与 projects.ts 中 Project 互补 */
export interface ProjectWithSettings {
  id: string
  code: string
  name: string
  description?: string
  status: 'active' | 'disabled'
  settings?: ProjectSettings
  createdAt: string
  updatedAt?: string
}

/** 更新项目设置入参（仅 settings 部分） */
export interface UpdateProjectSettingsPayload {
  settings?: ProjectSettings
}

// === 项目设置接口 ===

/** 获取项目详情（含 settings） */
export function getProjectSettings(projectId: string) {
  return request.get<ProjectWithSettings>(`/v1/projects/${projectId}`)
}

/** 更新项目设置（PATCH 整体 settings） */
export function updateProjectSettings(projectId: string, payload: UpdateProjectSettingsPayload) {
  return request.patch<ProjectWithSettings>(`/v1/projects/${projectId}`, payload)
}
