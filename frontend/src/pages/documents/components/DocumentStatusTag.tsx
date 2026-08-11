import { Tag } from 'antd'
import type { DocumentProcessingStatus } from '@/api/documents'

interface DocumentStatusTagProps {
  /** 文档处理状态 */
  status: DocumentProcessingStatus
}

// 处理状态映射（颜色 + 文案）
const STATUS_META: Record<DocumentProcessingStatus, { color: string; label: string }> = {
  PENDING: { color: 'default', label: '待处理' },
  PARSING: { color: 'processing', label: '解析中' },
  CHUNKING: { color: 'processing', label: '切分中' },
  EMBEDDING: { color: 'processing', label: '向量化中' },
  READY: { color: 'success', label: '就绪' },
  FAILED: { color: 'error', label: '失败' }
}

/**
 * 文档处理状态标签
 */
export default function DocumentStatusTag({ status }: DocumentStatusTagProps) {
  const meta = STATUS_META[status] || STATUS_META.PENDING
  return <Tag color={meta.color}>{meta.label}</Tag>
}
