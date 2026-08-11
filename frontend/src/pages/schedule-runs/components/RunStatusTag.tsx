import { Tag } from 'antd'
import type { ScheduleRunStatus } from '@/api/schedules'

interface RunStatusTagProps {
  /** 定时任务运行记录状态 */
  status: ScheduleRunStatus
}

// 状态映射（颜色 + 文案）
const STATUS_META: Record<ScheduleRunStatus, { color: string; label: string }> = {
  PENDING: { color: 'default', label: '待执行' },
  RUNNING: { color: 'processing', label: '运行中' },
  SUCCESS: { color: 'success', label: '成功' },
  FAILED: { color: 'error', label: '失败' },
  TIMEOUT: { color: 'error', label: '超时' },
  SKIPPED: { color: 'warning', label: '跳过' }
}

/**
 * 定时任务运行记录状态标签
 */
export default function RunStatusTag({ status }: RunStatusTagProps) {
  const meta = STATUS_META[status] || STATUS_META.PENDING
  return <Tag color={meta.color}>{meta.label}</Tag>
}
