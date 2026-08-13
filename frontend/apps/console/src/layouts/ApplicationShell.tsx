import { useState } from 'react'
import { Button, Select } from 'antd'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { ErrorState, LoadingState, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { useApplicationContext } from '@/hooks/useApplicationContext'

const navigation = [
  ['overview', '概览', '了解应用健康和下一步动作'],
  ['knowledge', '知识中心', '集合、文档和版本'],
  ['intelligence', 'AI 能力', '检索、回答策略和测试'],
  ['automation', '数据自动化', '入库与同步运行'],
  ['developer', '开发者接入', 'API Key 和接口示例'],
  ['operations', '运行与质量', '请求、错误和成本'],
  ['settings', '设置', '应用边界与环境']
] as const

export default function ApplicationShell() {
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const { application, environment, applicationId, applicationsQuery } = useApplicationContext()

  if (applicationsQuery.isLoading) return <LoadingState rows={7} />
  if (applicationsQuery.isError) {
    const error = asPlatformError(applicationsQuery.error)
    return (
      <ErrorState
        message={error.message}
        requestId={error.requestId}
        suggestion={error.suggestion ?? undefined}
        onRetry={() => applicationsQuery.refetch()}
      />
    )
  }
  if (!application || !environment) {
    return <ErrorState title="应用环境不存在" message="该应用环境不存在或已经被移除。" />
  }

  const section = location.pathname.split('/').at(-1) || 'overview'
  const base = `/applications/${application.id}/${environment.id}`

  return (
    <div className="application-workspace">
      <aside className={`application-sidebar ${mobileNavigationOpen ? 'navigation-open' : ''}`}>
        <div className="application-sidebar-header">
          <div className="application-identity">
            <span className="application-avatar">{application.name.slice(0, 1)}</span>
            <div>
              <strong>{application.name}</strong>
              <span>{application.code}</span>
            </div>
          </div>
          <Button
            className="mobile-app-nav-toggle"
            type="text"
            onClick={() => setMobileNavigationOpen((open) => !open)}
          >
            {mobileNavigationOpen ? '收起' : '功能导航'}
          </Button>
        </div>
        <Select
          aria-label="切换环境"
          value={environment.id}
          options={application.environments.map((item) => ({ label: item.name, value: item.id }))}
          onChange={(nextEnvironmentId) =>
            navigate(`/applications/${applicationId}/${nextEnvironmentId}/${section}`)
          }
        />
        <StatusPill status={environment.status}>{environment.name}</StatusPill>
        <nav className="application-nav">
          {navigation.map(([path, label, description]) => (
            <NavLink key={path} to={`${base}/${path}`} onClick={() => setMobileNavigationOpen(false)}>
              <strong>{label}</strong>
              <span>{description}</span>
            </NavLink>
          ))}
        </nav>
      </aside>
      <section className="application-content">
        <Outlet />
      </section>
    </div>
  )
}
