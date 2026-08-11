import { Tag } from 'antd'

interface ProjectStatusTagProps {
  /** 项目状态：active=启用，disabled=停用 */
  status: 'active' | 'disabled'
}

/**
 * 项目状态标签组件
 * - active：绿色
 * - disabled：红色
 */
export default function ProjectStatusTag({ status }: ProjectStatusTagProps) {
  if (status === 'active') {
    return <Tag color="green">启用</Tag>
  }
  return <Tag color="red">停用</Tag>
}
