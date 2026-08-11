import request from './request'

// === 文档相关类型定义 ===

/** 文档处理状态 */
export type DocumentProcessingStatus =
  | 'PENDING'
  | 'PARSING'
  | 'CHUNKING'
  | 'EMBEDDING'
  | 'READY'
  | 'FAILED'

/** 文档来源类型：文件 / 文本 / URL */
export type DocumentSourceType = 'FILE' | 'TEXT' | 'URL'

/** 文档实体 */
export interface Document {
  documentId: string
  title: string
  sourceType: DocumentSourceType
  processingStatus: DocumentProcessingStatus
  ingestionTaskId?: string
  knowledgeBaseCode: string
  chunkCount?: number
  enabled: boolean
  createdAt: string
  updatedAt?: string
}

/** 创建文本/URL 文档入参 */
export interface CreateDocumentPayload {
  type: 'TEXT' | 'URL'
  title: string
  content?: string
  url?: string
  externalId?: string
  tags?: string[]
  metadata?: Record<string, unknown>
}

/** 上传文件文档入参（multipart 字段） */
export interface UploadDocumentFilePayload {
  file: File
  title?: string
  tags?: string[]
  externalId?: string
  metadata?: Record<string, unknown>
}

/** 文档列表查询参数 */
export interface ListDocumentsParams {
  keyword?: string
  status?: DocumentProcessingStatus
}

// === 文档管理接口 ===

/**
 * 上传文件形式文档
 * - multipart/form-data：file、title?、tags?、externalId?、metadata?
 */
export function uploadDocumentFile(knowledgeBaseCode: string, payload: UploadDocumentFilePayload) {
  const formData = new FormData()
  formData.append('file', payload.file)
  if (payload.title) formData.append('title', payload.title)
  if (payload.tags?.length) {
    // tags 数组按字段重复 append
    payload.tags.forEach((t) => formData.append('tags', t))
  }
  if (payload.externalId) formData.append('externalId', payload.externalId)
  if (payload.metadata) formData.append('metadata', JSON.stringify(payload.metadata))

  return request.post<Document>(
    `/v1/knowledge-bases/${knowledgeBaseCode}/documents/file`,
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } }
  )
}

/**
 * 创建文本/URL 文档
 * - JSON：type=TEXT|URL、title、content|url、externalId?、tags?、metadata?
 */
export function createDocument(knowledgeBaseCode: string, payload: CreateDocumentPayload) {
  return request.post<Document>(`/v1/knowledge-bases/${knowledgeBaseCode}/documents`, payload)
}

/** 查询文档详情 */
export function getDocument(documentId: string) {
  return request.get<Document>(`/v1/documents/${documentId}`)
}

/**
 * 查询文档列表
 * - 后端无统一列表接口时，前端基于知识库维度做查询/过滤
 */
export function listDocuments(knowledgeBaseCode: string, params?: ListDocumentsParams) {
  return request.get<Document[]>(`/v1/knowledge-bases/${knowledgeBaseCode}/documents`, { params })
}
