import { Outlet, Link, useLocation } from 'react-router-dom'
import { Layout, Menu, Space } from 'antd'
import type { MenuProps } from 'antd'

const { Header, Content } = Layout

// 全局层只承载平台导航；具体能力收敛在项目工作区，避免用户在两层菜单间迷失。
const menuItems: MenuProps['items'] = [
  { key: 'overview', label: <Link to="/overview">平台总览</Link> },
  { key: 'projects', label: <Link to="/projects">项目空间</Link> }
]

/**
 * 主布局：将平台治理与项目执行明确分层，项目内能力由 ProjectLayout 继续承载。
 */
function MainLayout() {
  const location = useLocation()

  // 当前选中的全局导航项（从 URL 路径解析）
  const selectedKey = (() => {
    const seg = location.pathname.split('/')[1]
    return seg || 'overview'
  })()

  return (
    <Layout className="platform-shell h-full w-full">
      <Header className="flex h-16 items-center border-b border-slate-200/80 bg-white/90 px-6 backdrop-blur">
        <Link to="/overview" className="mr-8 platform-brand">
          <span className="platform-brand-mark">AI</span>
          <span className="platform-brand-copy">
            <strong>AI 能力平台</strong>
            <span>Knowledge · Intelligence · Automation</span>
          </span>
        </Link>
        <Menu
          mode="horizontal"
          selectedKeys={[selectedKey]}
          items={menuItems}
          className="flex-1 !border-b-0 !bg-transparent"
        />
        <Space size="middle">
          <span className="platform-status">平台服务已连接</span>
        </Space>
      </Header>
      <Content className="overflow-auto">
        <Outlet />
      </Content>
    </Layout>
  )
}

export default MainLayout
