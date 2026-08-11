import { Tag } from 'antd'
import type { CrawlRunStatus } from '@/api/crawl-sources'

interface RunStatusTagProps {
  /** 采集运行状态 */
  status: CrawlRunStatus
}

// 状态映射（颜色 + 文案）
const STATUS_META: Record<CrawlRunStatus, { color: string; label: string }> = {
  PENDING: { color: 'default', label: '待执行' },
  RUNNING: { color: 'processing', label: '运行中' },
  SUCCESS: { color: 'success', label: '成功' },
  PARTIAL: { color: 'warning', label: '部分成功' },
  FAILED: { color: 'error', label: '失败' },
  CANCELED: { color: 'default', label: '已取消' }
}

/**
 * 采集运行状态标签
 */
export default function RunStatusTag({ status }: RunStatusTagProps) {
  const meta = STATUS_META[status] || STATUS_META.PENDING
  return <Tag color={meta.color}>{meta.label}</Tag>
}
