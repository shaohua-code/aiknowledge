import type { DocumentProcessingStatus } from '@/api/documents'

/** 判断文档是否仍在后台处理，用于轮询与操作禁用。 */
export function isProcessingStatus(status: DocumentProcessingStatus): boolean {
  return status === 'PARSING' || status === 'CHUNKING' || status === 'EMBEDDING' || status === 'PENDING'
}
