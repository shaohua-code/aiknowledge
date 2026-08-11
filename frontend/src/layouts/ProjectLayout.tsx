import { useMemo } from 'react'
import { Outlet, useNavigate, useParams, useLocation, Link } from 'react-router-dom'
import { Layout, Menu, Tooltip, Button, Space, message } from 'antd'
import { useQueryClient } from '@tanstack/react-query'
import type { MenuProps } from 'antd'
import { useProjectStore } from '@/stores/project'

const { Header, Sider, Content } = Layout

// 候选颜色池，根据项目 code 首字母 hash 选取
const PROJECT_COLORS = [
  '#1677ff',
  '#52c41a',
  '#fa8c16',
  '#eb2f96',
  '#722ed1',
  '#13c2c2',
  '#faad14',
  '#f5222d'
]

/** 根据项目 code 首字母 hash 映射颜色，保证同一项目始终同色 */
function getProjectColor(code: string): string {
  const firstChar = code?.charAt(0)?.toLowerCase() || 'a'
  const idx = firstChar.charCodeAt(0) % PROJECT_COLORS.length
  return PROJECT_COLORS[idx]
}

/**
 * 项目内布局按用户任务而非数据表组织能力，保持所有链接在同一项目作用域内。
 */
export default function ProjectLayout() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const currentProject = useProjectStore((s) => s.currentProject)
  const clearCurrentProject = useProjectStore((s) => s.clearCurrentProject)

  // 项目颜色标识（首字母 hash 映射）
  const projectColor = useMemo(
    () => getProjectColor(currentProject?.code || projectId),
    [currentProject, projectId]
  )

  // 工作区分组：降低侧栏认知负担，同时不改变现有路由与权限边界。
  const menuItems: MenuProps['items'] = useMemo(
    () => [
      { key: 'overview', label: <Link to={`/projects/${projectId}/overview`}>项目概览</Link> },
      {
        type: 'group',
        label: '知识',
        children: [
          {
            key: 'knowledge-bases',
            label: <Link to={`/projects/${projectId}/knowledge-bases`}>知识集合</Link>
          },
          {
            key: 'documents',
            label: <Link to={`/projects/${projectId}/documents`}>文档与来源</Link>
          },
          {
            key: 'crawl-sources',
            label: <Link to={`/projects/${projectId}/crawl-sources`}>采集源</Link>
          },
          {
            key: 'crawl-runs',
            label: <Link to={`/projects/${projectId}/crawl-runs`}>采集记录</Link>
          },
          {
            key: 'web-materials',
            label: <Link to={`/projects/${projectId}/web-materials`}>审核资料</Link>
          }
        ]
      },
      {
        type: 'group',
        label: '智能',
        children: [
          { key: 'research', label: <Link to={`/projects/${projectId}/research`}>研究台</Link> },
          {
            key: 'retrieval-test',
            label: <Link to={`/projects/${projectId}/retrieval-test`}>检索调试</Link>
          },
          { key: 'prompts', label: <Link to={`/projects/${projectId}/prompts`}>提示词版本</Link> }
        ]
      },
      {
        type: 'group',
        label: '自动化',
        children: [
          {
            key: 'schedules',
            label: <Link to={`/projects/${projectId}/schedules`}>定时任务</Link>
          },
          {
            key: 'schedule-runs',
            label: <Link to={`/projects/${projectId}/schedule-runs`}>运行记录</Link>
          }
        ]
      },
      {
        type: 'group',
        label: '治理',
        children: [
          { key: 'tools', label: <Link to={`/projects/${projectId}/tools`}>工具授权</Link> },
          { key: 'api-keys', label: <Link to={`/projects/${projectId}/api-keys`}>接入密钥</Link> },
          {
            key: 'execution-logs',
            label: <Link to={`/projects/${projectId}/execution-logs`}>执行审计</Link>
          },
          {
            key: 'project-settings',
            label: <Link to={`/projects/${projectId}/project-settings`}>项目设置</Link>
          }
        ]
      }
    ],
    [projectId]
  )

  // 当前选中菜单项（从 URL 路径解析），默认项目概览
  const selectedKey = useMemo(() => {
    const seg = location.pathname.split(`/projects/${projectId}/`)[1]?.split('/')[0]
    return seg || 'overview'
  }, [location.pathname, projectId])

  /** 退出项目：清空缓存与 store，返回项目列表 */
  function handleExitProject() {
    // 切换/退出项目时清空所有项目相关 TanStack Query 缓存
    queryClient.clear()
    clearCurrentProject()
    message.success('已退出当前项目')
    navigate('/projects')
  }

  // 未设置当前项目（如直接访问 URL），引导回列表页选择
  if (!currentProject) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-4 p-6">
        <div className="text-gray-500">尚未选择项目，请返回项目列表选择要进入的项目</div>
        <Button type="primary" onClick={() => navigate('/projects')}>
          返回项目列表
        </Button>
      </div>
    )
  }

  return (
    <Layout className="platform-shell h-full w-full">
      <Header className="flex h-auto min-h-16 items-center justify-between border-b border-slate-200/80 bg-white/90 px-6 py-3 backdrop-blur">
        <Space size="middle">
          <span
            className="grid h-9 w-9 place-items-center rounded-xl text-sm font-bold text-white shadow-sm"
            style={{ background: projectColor }}
          >
            {currentProject.code.charAt(0).toUpperCase()}
          </span>
          <div className="flex flex-col leading-tight">
            <span className="text-base font-semibold text-slate-800">{currentProject.name}</span>
            <span className="text-xs text-slate-400">项目空间 · {currentProject.code}</span>
          </div>
        </Space>
        <Tooltip title="退出项目空间并清理本地缓存">
          <Button onClick={handleExitProject}>切换项目</Button>
        </Tooltip>
      </Header>
      <Layout>
        <Sider
          width={224}
          theme="light"
          className="border-r border-slate-200/80 bg-white/70 px-1 py-3"
        >
          <Menu
            mode="inline"
            selectedKeys={[selectedKey]}
            items={menuItems}
            className="project-nav !border-r-0 !bg-transparent"
          />
        </Sider>
        <Content className="overflow-auto">
          <div className="platform-content">
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}
