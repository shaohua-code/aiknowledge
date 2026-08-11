import request from './request'

/** 知识库实体 */
export interface KnowledgeBase {
  id: string
  code: string
  name: string
  description?: string
  embeddingModel?: string
  embeddingDimension?: number
  documentCount?: number
  status: 'active' | 'disabled'
  createdAt: string
  updatedAt?: string
}

/** 创建知识库入参 */
export interface CreateKnowledgeBasePayload {
  code: string
  name: string
  description?: string
  embeddingModel?: string
  embeddingDimension?: number
}

/** 更新知识库入参 */
export interface UpdateKnowledgeBasePayload {
  name?: string
  description?: string
  status?: 'active' | 'disabled'
}

/** 查询知识库列表参数 */
export interface ListKnowledgeBasesParams {
  status?: string
}

// === 知识库管理接口（项目 API Key + X-Project-Code 保护） ===

/** 获取知识库列表 */
export function listKnowledgeBases(params?: ListKnowledgeBasesParams) {
  return request.get<KnowledgeBase[]>('/v1/knowledge-bases', { params })
}

/** 获取知识库详情 */
export function getKnowledgeBase(code: string) {
  return request.get<KnowledgeBase>(`/v1/knowledge-bases/${code}`)
}

/** 创建知识库 */
export function createKnowledgeBase(payload: CreateKnowledgeBasePayload) {
  return request.post<KnowledgeBase>('/v1/knowledge-bases', payload)
}

/** 更新知识库 */
export function updateKnowledgeBase(code: string, payload: UpdateKnowledgeBasePayload) {
  return request.patch<KnowledgeBase>(`/v1/knowledge-bases/${code}`, payload)
}

/** 删除知识库（仅空知识库可删除） */
export function deleteKnowledgeBase(code: string) {
  return request.delete<void>(`/v1/knowledge-bases/${code}`)
}

/** 停用知识库 */
export function disableKnowledgeBase(code: string) {
  return request.post<void>(`/v1/knowledge-bases/${code}/disable`)
}

/** 启用知识库 */
export function enableKnowledgeBase(code: string) {
  return request.post<void>(`/v1/knowledge-bases/${code}/enable`)
}
