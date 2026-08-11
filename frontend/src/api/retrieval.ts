import request from './request'

// === 检索测试相关类型定义 ===

/** 检索命中片段 */
export interface RetrievalHit {
  chunkId: string
  documentId: string
  content: string
  pageNumber?: number
  score: number
  metadata?: Record<string, unknown>
}

/** 检索响应结果 */
export interface RetrievalResult {
  query: string
  hits: RetrievalHit[]
  totalHits: number
  elapsedMs: number
}

/** 检索请求入参 */
export interface RetrievalSearchParams {
  query: string
  knowledgeBaseIds: string[]
  topK?: number
}

// === 检索接口 ===

/**
 * 执行向量检索
 * - 返回命中片段列表、总命中数、耗时
 */
export function retrievalSearch(payload: RetrievalSearchParams) {
  return request.post<RetrievalResult>('/v1/retrieval/search', payload)
}
