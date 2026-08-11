import { Card, Progress, Tag, Typography } from 'antd'
import type { RetrievalHit } from '@/api/retrieval'

const { Paragraph, Text } = Typography

interface RetrievalHitCardProps {
  /** 单条命中片段 */
  hit: RetrievalHit
  /** 索引 */
  index: number
}

/**
 * 检索命中片段卡片
 * - 内容、评分进度条、documentId、pageNumber
 */
export default function RetrievalHitCard({ hit, index }: RetrievalHitCardProps) {
  // 评分百分比（0-100）
  const scorePct = Math.round(Math.min(Math.max(hit.score, 0), 1) * 100)
  // 评分对应进度条状态
  const status: 'success' | 'normal' | 'exception' =
    scorePct >= 70 ? 'success' : scorePct >= 40 ? 'normal' : 'exception'

  return (
    <Card size="small" className="!border-gray-200">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Text type="secondary" className="!text-xs">#{index + 1}</Text>
          <Tag color="blue" className="!m-0">chunkId: {hit.chunkId}</Tag>
        </div>
        {typeof hit.pageNumber === 'number' && <Tag>第 {hit.pageNumber} 页</Tag>}
      </div>
      <Paragraph className="!mb-2 !whitespace-pre-wrap !text-sm !text-gray-700">
        {hit.content}
      </Paragraph>
      <div className="mb-1 flex items-center justify-between">
        <Text type="secondary" className="!text-xs">documentId: {hit.documentId}</Text>
        <Text strong className="!text-xs">评分：{scorePct}%</Text>
      </div>
      <Progress percent={scorePct} status={status} />
    </Card>
  )
}
