import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { message } from 'antd'
import {
  listDocuments,
  uploadDocumentFile,
  createDocument,
  getDocument,
  type ListDocumentsParams,
  type CreateDocumentPayload,
  type UploadDocumentFilePayload,
  type Document,
  type DocumentProcessingStatus
} from '@/api/documents'
import { isProcessingStatus } from '../documentStatus'

// TanStack Query 缓存键
const DOCUMENTS_KEY = ['documents'] as const

/** 是否存在处理中的文档（决定是否开启轮询） */
function hasProcessingDoc(docs: Document[] | undefined): boolean {
  return !!docs?.some((d) => isProcessingStatus(d.processingStatus))
}

/**
 * 文档列表查询
 * - 处理中状态时每 3s 自动轮询刷新
 * @param knowledgeBaseCode 知识库编码
 * @param params 查询参数（keyword、status）
 */
export function useDocuments(knowledgeBaseCode: string, params?: ListDocumentsParams) {
  return useQuery<Document[], Error>({
    queryKey: [...DOCUMENTS_KEY, knowledgeBaseCode, params],
    queryFn: () => listDocuments(knowledgeBaseCode, params),
    enabled: !!knowledgeBaseCode,
    // 存在处理中文档时每 3s 轮询，否则关闭轮询
    refetchInterval: (query) => (hasProcessingDoc(query.state.data) ? 3000 : false)
  })
}

/** 单文档详情查询（轮询处理中状态） */
export function useDocument(documentId: string | null) {
  return useQuery<Document, Error>({
    queryKey: [...DOCUMENTS_KEY, 'detail', documentId],
    queryFn: () => getDocument(documentId as string),
    enabled: !!documentId,
    refetchInterval: (query) => {
      const doc = query.state.data
      return doc && isProcessingStatus(doc.processingStatus) ? 3000 : false
    }
  })
}

/** 上传文件文档 mutation */
export function useUploadDocumentFile() {
  const queryClient = useQueryClient()
  return useMutation<
    Document,
    Error,
    { knowledgeBaseCode: string; payload: UploadDocumentFilePayload }
  >({
    mutationFn: ({ knowledgeBaseCode, payload }) =>
      uploadDocumentFile(knowledgeBaseCode, payload),
    onSuccess: () => {
      message.success('文档上传成功，正在后台处理')
      queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY })
    }
  })
}

/** 创建文本/URL 文档 mutation */
export function useCreateDocument() {
  const queryClient = useQueryClient()
  return useMutation<
    Document,
    Error,
    { knowledgeBaseCode: string; payload: CreateDocumentPayload }
  >({
    mutationFn: ({ knowledgeBaseCode, payload }) =>
      createDocument(knowledgeBaseCode, payload),
    onSuccess: (_d, variables) => {
      const msg =
        variables.payload.type === 'URL' ? 'URL 文档创建成功' : '文本文档创建成功'
      message.success(msg)
      queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY })
    }
  })
}

/** 文档状态过滤选项 */
export const DOCUMENT_STATUS_OPTIONS: Array<{ label: string; value: DocumentProcessingStatus }> = [
  { label: '待处理', value: 'PENDING' },
  { label: '解析中', value: 'PARSING' },
  { label: '切分中', value: 'CHUNKING' },
  { label: '向量化中', value: 'EMBEDDING' },
  { label: '就绪', value: 'READY' },
  { label: '失败', value: 'FAILED' }
]
