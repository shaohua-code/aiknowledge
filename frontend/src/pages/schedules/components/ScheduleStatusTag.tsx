import { Tag } from 'antd'

interface ScheduleStatusTagProps {
  /** 定时任务启用状态：true=启用，false=暂停 */
  enabled: boolean
}

/**
 * 定时任务状态标签
 * - 启用：绿色
 * - 暂停：橙色
 */
export default function ScheduleStatusTag({ enabled }: ScheduleStatusTagProps) {
  if (enabled) {
    return <Tag color="green">启用</Tag>
  }
  return <Tag color="orange">暂停</Tag>
}
