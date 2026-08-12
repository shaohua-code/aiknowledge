import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from 'antd'
import { Link } from 'react-router-dom'
import { ErrorState, LoadingState, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { applicationApi, getHealth } from '@/api/platform'
import LineIcon from '@/components/LineIcon'

const applicationTypeNames: Record<string, string> = {
  resume: 'AI 简历',
  fund: 'AI 基金',
  general: '通用知识',
  custom: '自定义应用'
}

const healthCheckNames: Record<string, string> = {
  database: 'PostgreSQL 数据库',
  redis: 'Redis 任务队列',
  chat_provider: '大模型服务',
  embedding_provider: '向量模型服务',
  web_search_provider: '联网检索服务'
}

function OverviewMetric({
  icon,
  label,
  value,
  helper,
  tone = 'violet'
}: {
  icon: 'apps' | 'layers' | 'pulse' | 'alert'
  label: string
  value: ReactNode
  helper: string
  tone?: 'violet' | 'blue' | 'green' | 'amber'
}) {
  return (
    <article className={`overview-metric tone-${tone}`}>
      <div className="overview-metric-top">
        <span className="overview-metric-icon"><LineIcon name={icon} /></span>
        <span>{label}</span>
      </div>
      <strong>{value}</strong>
      <small>{helper}</small>
    </article>
  )
}

export default function DashboardPage() {
  const applications = useQuery({ queryKey: ['applications'], queryFn: applicationApi.list })
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth, retry: false })

  if (applications.isLoading) return <LoadingState rows={7} />
  if (applications.isError) {
    const error = asPlatformError(applications.error)
    return (
      <ErrorState
        message={error.message}
        requestId={error.requestId}
        suggestion={error.suggestion ?? undefined}
        onRetry={() => applications.refetch()}
      />
    )
  }

  const rows = applications.data ?? []
  const environments = rows.flatMap((item) => item.environments)
  const active = rows.filter((item) => item.status === 'active').length
  const healthState = health.data?.status ?? (health.isError ? 'error' : 'degraded')
  const checks = Object.entries(health.data?.checks ?? {})
  const attentionCount = checks.filter(([, item]) => !['ok', 'ready'].includes(item.status)).length
  const visibleChecks: Array<[string, { status: string }]> =
    checks.length > 0
      ? checks
      : [['api', { status: health.isLoading ? 'checking' : healthState }]]

  return (
    <div className="page-container dashboard-page">
      <section className="dashboard-hero">
        <div className="dashboard-hero-copy">
          <span className="dashboard-hero-kicker">
            <i /> AI KNOWLEDGE INFRASTRUCTURE
          </span>
          <h1>
            让知识成为每个 AI 应用
            <em>稳定、可信的能力层</em>
          </h1>
          <p>
            集中管理专属知识、检索策略与模型兜底，让 AI 简历、AI 基金以及未来项目复用同一套可靠底座。
          </p>
          <div className="dashboard-hero-actions">
            <Link to="/applications?create=true">
              <Button className="hero-primary-action" size="large">
                {rows.length === 0 ? '创建第一个 AI 应用' : '创建 AI 应用'}
                <LineIcon name="arrow" size={18} />
              </Button>
            </Link>
            <Link to="/applications">
              <Button className="hero-secondary-action" size="large">查看应用空间</Button>
            </Link>
          </div>
          <div className="dashboard-hero-trust">
            <span><LineIcon name="shield" size={15} /> 应用与环境强隔离</span>
            <span><LineIcon name="search" size={15} /> 回答证据可追溯</span>
          </div>
        </div>

        <div className="knowledge-orbit" aria-hidden="true">
          <div className="orbit-grid" />
          <div className="orbit-ring ring-one" />
          <div className="orbit-ring ring-two" />
          <div className="orbit-core">
            <LineIcon name="layers" size={27} />
            <strong>KNOWLEDGE</strong>
            <span>CORE</span>
          </div>
          <div className="orbit-node node-resume"><i /> AI 简历</div>
          <div className="orbit-node node-fund"><i /> AI 基金</div>
          <div className="orbit-node node-agent"><i /> AI Agent</div>
          <span className="orbit-signal signal-one" />
          <span className="orbit-signal signal-two" />
          <span className="orbit-signal signal-three" />
        </div>
      </section>

      <section className="dashboard-section dashboard-overview-section">
        <div className="dashboard-section-heading">
          <div>
            <span className="section-kicker">PLATFORM PULSE</span>
            <h2>平台运行概览</h2>
          </div>
          <span className="data-freshness"><i /> 每 30 秒自动更新</span>
        </div>
        <div className="overview-metric-grid">
          <OverviewMetric
            icon="apps"
            label="AI 应用"
            value={rows.length}
            helper={rows.length ? `${active} 个应用正在运行` : '等待创建第一个应用'}
          />
          <OverviewMetric
            icon="layers"
            label="隔离环境"
            value={environments.length}
            helper="开发、测试与生产独立治理"
            tone="blue"
          />
          <OverviewMetric
            icon="pulse"
            label="平台状态"
            value={
              <span className={`metric-status metric-status-${healthState}`}>
                <i />{healthState === 'ok' ? '运行正常' : healthState === 'degraded' ? '部分降级' : '需要处理'}
              </span>
            }
            helper={health.isError ? '暂时无法取得健康检查' : '基于全部核心依赖检查'}
            tone="green"
          />
          <OverviewMetric
            icon="alert"
            label="需要关注"
            value={attentionCount}
            helper={attentionCount ? '存在未就绪的底座能力' : '当前没有待处理故障'}
            tone="amber"
          />
        </div>
      </section>

      <div className="dashboard-content-grid">
        <section className="dashboard-panel recent-applications-panel">
          <div className="dashboard-section-heading compact">
            <div>
              <span className="section-kicker">APPLICATION SPACES</span>
              <h2>最近使用的 AI 应用</h2>
            </div>
            {rows.length > 0 && <Link to="/applications" className="section-link">查看全部 <LineIcon name="arrow" size={15} /></Link>}
          </div>

          {rows.length === 0 ? (
            <div className="dashboard-onboarding">
              <div className="onboarding-visual" aria-hidden="true">
                <span className="onboarding-layer layer-back" />
                <span className="onboarding-layer layer-middle" />
                <span className="onboarding-layer layer-front"><LineIcon name="sparkle" size={26} /></span>
              </div>
              <div className="onboarding-copy">
                <span className="onboarding-label">从这里开始</span>
                <h3>创建你的第一个 AI 应用空间</h3>
                <p>每个应用拥有独立知识、策略、密钥和运行记录，业务之间互不干扰。</p>
                <div className="onboarding-steps">
                  <span><b>1</b> 创建应用</span>
                  <i />
                  <span><b>2</b> 导入知识</span>
                  <i />
                  <span><b>3</b> 接入业务</span>
                </div>
                <Link to="/applications?create=true">
                  <Button type="primary">开始创建 <LineIcon name="arrow" size={16} /></Button>
                </Link>
              </div>
            </div>
          ) : (
            <div className="dashboard-application-list">
              {rows.slice(0, 4).map((application) => {
                const environment =
                  application.environments.find((item) => item.code === 'development') ??
                  application.environments[0]
                return (
                  <Link
                    className="dashboard-application-row"
                    key={application.id}
                    to={`/applications/${application.id}/${environment.id}/overview`}
                  >
                    <span className={`application-type-icon type-${application.applicationType}`}>
                      {application.name.slice(0, 1)}
                    </span>
                    <span className="dashboard-application-copy">
                      <strong>{application.name}</strong>
                      <small>{application.description || application.code}</small>
                    </span>
                    <span className="application-type-name">
                      {applicationTypeNames[application.applicationType] ?? 'AI 应用'}
                    </span>
                    <StatusPill status={application.status}>
                      {application.status === 'active' ? '运行中' : application.status}
                    </StatusPill>
                    <LineIcon name="arrow" size={17} />
                  </Link>
                )
              })}
            </div>
          )}
        </section>

        <aside className="dashboard-side-column">
          <section className="dashboard-panel readiness-panel">
            <div className="dashboard-section-heading compact">
              <div>
                <span className="section-kicker">READINESS</span>
                <h2>底座能力状态</h2>
              </div>
              <StatusPill status={healthState}>
                {healthState === 'ok' ? '全部就绪' : healthState === 'degraded' ? '部分就绪' : '连接异常'}
              </StatusPill>
            </div>
            <div className="readiness-list">
              {visibleChecks.map(([name, item]) => (
                  <div className="readiness-item" key={name}>
                    <span className="readiness-icon"><LineIcon name={name.includes('database') ? 'database' : name.includes('redis') ? 'pulse' : 'sparkle'} size={17} /></span>
                    <span>
                      <strong>{healthCheckNames[name] ?? (name === 'api' ? '平台 API' : name)}</strong>
                      <small>{['ok', 'ready'].includes(item.status) ? '连接正常' : item.status === 'checking' ? '正在检测' : '需要配置或检查'}</small>
                    </span>
                    <i className={`readiness-dot state-${item.status}`} />
                  </div>
                ))}
            </div>
          </section>

          <section className="foundation-flow-card">
            <div>
              <span className="section-kicker">ANSWER PIPELINE</span>
              <h2>统一回答链路</h2>
              <p>业务只调用一个接口，底座自动完成知识检索、模型补充和证据返回。</p>
            </div>
            <div className="foundation-flow" aria-label="知识回答链路">
              <span><LineIcon name="database" size={17} /> 专属知识</span>
              <i />
              <span><LineIcon name="search" size={17} /> 混合检索</span>
              <i />
              <span><LineIcon name="sparkle" size={17} /> 可信回答</span>
            </div>
          </section>
        </aside>
      </div>
    </div>
  )
}
