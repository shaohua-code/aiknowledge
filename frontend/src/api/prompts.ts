import request from './request'

// === 提示词相关类型定义 ===

/** 提示词版本实体 */
export interface Prompt {
  /** 版本 ID（唯一） */
  versionId: string
  /** 版本号 */
  version: number
  /** 是否当前激活版本 */
  isActive: boolean
  /** 系统提示词 */
  systemPrompt: string
  /** 证据规则（JSON 字符串） */
  evidenceRules?: string
  /** 输出 schema（JSON 字符串） */
  outputSchema?: string
  /** 禁止事项（每行一条） */
  prohibitions?: string[]
  /** 风险提示模板 */
  riskTemplate?: string
  createdAt: string
  updatedAt?: string
}

/** 创建提示词入参 */
export interface CreatePromptPayload {
  systemPrompt: string
  evidenceRules?: string
  outputSchema?: string
  prohibitions?: string[]
  riskTemplate?: string
}

/** 更新提示词入参（非 active 版本才允许） */
export interface UpdatePromptPayload {
  systemPrompt?: string
  evidenceRules?: string
  outputSchema?: string
  prohibitions?: string[]
  riskTemplate?: string
}

// === 提示词接口 ===

/** 查询全部提示词版本列表 */
export function listPrompts() {
  return request.get<Prompt[]>('/v1/prompts')
}

/** 查询当前激活的提示词版本 */
export function getActivePrompt() {
  return request.get<Prompt>('/v1/prompts/active')
}

/** 查询提示词版本详情 */
export function getPrompt(versionId: string) {
  return request.get<Prompt>(`/v1/prompts/${versionId}`)
}

/** 创建新提示词版本 */
export function createPrompt(payload: CreatePromptPayload) {
  return request.post<Prompt>('/v1/prompts', payload)
}

/** 更新提示词版本（仅非 active 版本可改） */
export function updatePrompt(versionId: string, payload: UpdatePromptPayload) {
  return request.patch<Prompt>(`/v1/prompts/${versionId}`, payload)
}

/** 激活某个提示词版本 */
export function activatePrompt(versionId: string) {
  return request.post<Prompt>(`/v1/prompts/${versionId}/activate`)
}

/** 删除提示词版本（仅非 active 版本可删） */
export function deletePrompt(versionId: string) {
  return request.delete<void>(`/v1/prompts/${versionId}`)
}
