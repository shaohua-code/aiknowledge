import { Tag } from 'antd'

interface CrawlSourceStatusTagProps {
  /** 采集源启用状态 */
  enabled: boolean
}

/**
 * 采集源状态标签
 * - 启用：绿色
 * - 暂停：橙色
 */
export default function CrawlSourceStatusTag({ enabled }: CrawlSourceStatusTagProps) {
  if (enabled) {
    return <Tag color="green">启用</Tag>
  }
  return <Tag color="orange">暂停</Tag>
}
