import { Drawer, Empty, Spin, Tag, Typography, Divider } from 'antd'
import type { Document } from '@/api/documents'

const { Title, Paragraph, Text } = Typography

interface DocumentChunkPreviewProps {
  /** Drawer 是否可见 */
  open: boolean
  /** 当前文档 */
  document: Document | null
  /** 取消回调 */
  onClose: () => void
  /** 片段列表（基于后端能力，若有专门 chunks 接口可扩展） */
  chunks?: Array<{ chunkId: string; content: string; pageNumber?: number }>
  /** 片段加载中 */
  loading?: boolean
}

/**
 * 文档片段预览 Drawer
 * - 展示文档基础信息
 * - 列表展示片段（chunkId、内容、页码）
 * - 片段能力暂未对接独立接口时，给出提示
 */
export default function DocumentChunkPreview({
  open,
  document,
  onClose,
  chunks = [],
  loading = false
}: DocumentChunkPreviewProps) {
  return (
    <Drawer
      title="片段预览"
      open={open}
      onClose={onClose}
      width={560}
      destroyOnClose
    >
      {!document ? (
        <Empty description="未选择文档" />
      ) : (
        <div className="flex flex-col gap-3">
          {/* 文档基础信息 */}
          <div className="rounded border border-gray-200 bg-gray-50 p-3">
            <Title level={5} className="!mb-2">{document.title}</Title>
            <div className="flex flex-wrap gap-2 text-xs text-gray-600">
              <span>documentId: {document.documentId}</span>
              <Tag color="blue">{document.sourceType}</Tag>
              <span>chunkCount: {document.chunkCount ?? 0}</span>
            </div>
          </div>
          <Divider className="!my-2" />
          {/* 片段列表 */}
          <Title level={5} className="!mb-2">片段列表</Title>
          {loading ? (
            <div className="flex items-center justify-center py-6">
              <Spin />
            </div>
          ) : chunks.length === 0 ? (
            <Empty description="暂无片段数据" />
          ) : (
            <div className="flex flex-col gap-3">
              {chunks.map((c, i) => (
                <div key={c.chunkId} className="rounded border border-gray-200 p-3">
                  <div className="mb-1 flex items-center justify-between">
                    <Text type="secondary" className="!text-xs">#{i + 1} {c.chunkId}</Text>
                    {c.pageNumber && <Tag className="!m-0">第 {c.pageNumber} 页</Tag>}
                  </div>
                  <Paragraph className="!mb-0 !text-sm !text-gray-700">{c.content}</Paragraph>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Drawer>
  )
}
