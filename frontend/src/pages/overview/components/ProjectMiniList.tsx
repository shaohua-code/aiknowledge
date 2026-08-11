import { Card, Empty, Spin, Tag } from 'antd'
import { useNavigate } from 'react-router-dom'
import type { Project } from '@/api/projects'

interface ProjectMiniListProps {
  /** 最近活跃项目列表（最多 5 个） */
  projects: Project[] | undefined
  /** 加载中状态 */
  loading?: boolean
}

/**
 * 项目迷你列表
 * - 显示最近活跃的 5 个项目（code、name、status）
 * - 点击行跳转到项目内概览页
 */
export default function ProjectMiniList({ projects, loading }: ProjectMiniListProps) {
  const navigate = useNavigate()

  /** 点击项目行：跳转项目内概览页 */
  function handleClick(projectId: string) {
    navigate(`/projects/${projectId}/overview`)
  }

  return (
    <Card
      title="最近活跃项目"
      className="!rounded-2xl !border-slate-200/80 !bg-white/90 !shadow-[0_12px_32px_rgba(23,32,51,0.06)]"
    >
      <Spin spinning={loading}>
        {!projects || projects.length === 0 ? (
          <Empty description="暂无项目" />
        ) : (
          <ul className="divide-y divide-gray-100">
            {projects.map((project) => (
              <li
                key={project.id}
                className="flex cursor-pointer items-center justify-between rounded-xl px-3 py-3 transition-colors hover:bg-indigo-50/70"
                onClick={() => handleClick(project.id)}
              >
                <div className="flex flex-col leading-tight">
                  <span className="text-sm font-medium text-slate-800">{project.name}</span>
                  <span className="text-xs text-slate-400">项目编码 · {project.code}</span>
                </div>
                <Tag color={project.status === 'active' ? 'green' : 'red'}>
                  {project.status === 'active' ? '启用' : '停用'}
                </Tag>
              </li>
            ))}
          </ul>
        )}
      </Spin>
    </Card>
  )
}
