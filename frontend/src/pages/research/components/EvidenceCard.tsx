import { Card, Tag, Typography, Tooltip } from 'antd'
import Link from 'antd/es/typography/Link'
import type { ResearchEvidence, EvidenceType } from '@/api/research'

const { Paragraph, Text } = Typography

interface EvidenceCardProps {
  /** 单条证据 */
  evidence: ResearchEvidence
  /** 索引（用于展示序号） */
  index: number
}

// 证据类型标签映射（颜色 + 文案）
const EVIDENCE_TYPE_META: Record<EvidenceType, { color: string; label: string }> = {
  internal: { color: 'blue', label: '内部知识' },
  web: { color: 'purple', label: 'Web' },
  tool: { color: 'orange', label: '工具' }
}

/**
 * 单条证据卡片
 * - 类型标签、标题、摘要、来源链接、发布时间、评分
 */
export default function EvidenceCard({ evidence, index }: EvidenceCardProps) {
  const meta = EVIDENCE_TYPE_META[evidence.type] || EVIDENCE_TYPE_META.internal
  // 评分百分比展示（保留两位）
  const scorePct = typeof evidence.score === 'number' ? Math.round(evidence.score * 100) : null

  return (
    <Card
      size="small"
      className="!border-gray-200"
      title={
        <div className="flex items-center gap-2">
          <Text type="secondary" className="!text-xs">#{index + 1}</Text>
          <Tag color={meta.color} className="!m-0">{meta.label}</Tag>
          <Text strong className="!text-sm">{evidence.title || '未命名证据'}</Text>
        </div>
      }
    >
      <Paragraph className="!mb-2 !text-sm !text-gray-700">{evidence.snippet || '-'}</Paragraph>
      <div className="flex flex-wrap items-center gap-3 text-xs text-gray-500">
        {evidence.sourceUrl && (
          <Tooltip title={evidence.sourceUrl}>
            <Link href={evidence.sourceUrl} target="_blank" rel="noreferrer" className="!text-xs">
              来源链接
            </Link>
          </Tooltip>
        )}
        {evidence.publishedAt && <span>发布时间：{evidence.publishedAt}</span>}
        {evidence.dataAsOf && <span>数据截至：{evidence.dataAsOf}</span>}
        {scorePct !== null && (
          <span className="font-medium text-blue-600">评分：{scorePct}%</span>
        )}
      </div>
    </Card>
  )
}
