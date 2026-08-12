import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Dropdown, Space, Spin } from 'antd'
import {
  Link,
  Navigate,
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
  useNavigation
} from 'react-router-dom'
import { getHealth, sessionApi } from '@/api/platform'
import { StatusPill } from '@aik/ui'
import BrandMark from '@/components/BrandMark'

export default function ConsoleShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const navigation = useNavigation()
  const queryClient = useQueryClient()
  const session = useQuery({
    queryKey: ['session'],
    queryFn: sessionApi.me,
    retry: false
  })
  const health = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    refetchInterval: 30_000,
    retry: false
  })
  const logout = useMutation({
    mutationFn: sessionApi.logout,
    onSuccess: () => {
      queryClient.clear()
      navigate('/login', { replace: true })
    }
  })

  if (session.isLoading) {
    return (
      <main className="auth-gate" aria-live="polite" aria-busy="true">
        <BrandMark />
        <Spin size="small" />
        <strong>正在连接知识平台</strong>
        <p>正在确认登录状态，服务不可用时会自动进入诊断登录页。</p>
      </main>
    )
  }
  if (session.isError) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: `${location.pathname}${location.search}`, sessionError: true }}
      />
    )
  }

  const healthState = health.data?.status ?? (health.isError ? 'error' : 'degraded')

  return (
    <div className="console-shell">
      <div
        className={`route-progress ${navigation.state !== 'idle' ? 'active' : ''}`}
        aria-hidden="true"
      />
      <header className="console-topbar">
        <div className="console-topbar-inner">
          <Link to="/" className="brand-lockup">
            <BrandMark />
            <span>
              <strong>AI 知识能力底座</strong>
              <small>Knowledge Infrastructure</small>
            </span>
          </Link>
          <nav className="global-nav" aria-label="全局导航">
            <NavLink to="/" end>
              工作台
            </NavLink>
            <NavLink to="/applications">AI 应用</NavLink>
          </nav>
          <Space size="small" className="console-actions">
          <Dropdown
            menu={{
              items: [
                {
                  key: 'state',
                  label:
                    healthState === 'ok'
                      ? '所有核心服务可用'
                      : healthState === 'degraded'
                        ? '部分能力暂时不可用'
                        : '平台服务连接失败',
                  disabled: true
                },
                { type: 'divider' },
                { key: 'refresh', label: '重新检查', onClick: () => health.refetch() }
              ]
            }}
          >
            <Button type="text" className="health-button" loading={health.isFetching}>
              <StatusPill status={healthState}>
                {healthState === 'ok' ? '平台正常' : healthState === 'degraded' ? '部分降级' : '平台故障'}
              </StatusPill>
            </Button>
          </Dropdown>
          <Dropdown
            className="mobile-global-menu"
            menu={{
              items: [
                { key: 'home', label: <Link to="/">工作台</Link> },
                { key: 'applications', label: <Link to="/applications">AI 应用</Link> }
              ]
            }}
          >
            <Button type="text">导航</Button>
          </Dropdown>
            <Dropdown
              menu={{
                items: [
                  { key: 'email', label: session.data?.email, disabled: true },
                  { type: 'divider' },
                  { key: 'logout', label: '退出登录', onClick: () => logout.mutate() }
                ]
              }}
            >
              <Button type="text" className="user-menu-button" loading={logout.isPending}>
                <span className="user-avatar">管</span>
                <span className="user-menu-label">管理员</span>
              </Button>
            </Dropdown>
          </Space>
        </div>
      </header>
      <main className="console-main">
        <Outlet />
      </main>
    </div>
  )
}
