import type { ReactNode } from 'react'
import { Alert, Button, Empty, Skeleton, Tag } from 'antd'

export function PageHeader({
  eyebrow,
  title,
  description,
  actions
}: {
  eyebrow?: string
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <header className="aik-page-header">
      <div>
        {eyebrow && <span className="aik-eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="aik-page-actions">{actions}</div>}
    </header>
  )
}

export function StatusPill({ status, children }: { status: string; children?: ReactNode }) {
  const color =
    status === 'active' || status === 'ready' || status === 'succeeded' || status === 'ok'
      ? 'success'
      : status === 'failed' || status === 'error' || status === 'revoked'
        ? 'error'
        : status === 'degraded' || status === 'processing' || status === 'running'
          ? 'warning'
          : 'default'
  return <Tag color={color}>{children ?? status}</Tag>
}

export function MetricCard({
  label,
  value,
  helper
}: {
  label: string
  value: ReactNode
  helper?: string
}) {
  return (
    <article className="aik-metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      {helper && <small>{helper}</small>}
    </article>
  )
}

export function LoadingState({ rows = 4 }: { rows?: number }) {
  return (
    <div className="aik-state-card">
      <Skeleton active paragraph={{ rows }} />
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action
}: {
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <div className="aik-state-card aik-empty-state">
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={title} />
      <p>{description}</p>
      {action}
    </div>
  )
}

export function ErrorState({
  title = '页面加载失败',
  message,
  requestId,
  suggestion,
  onRetry
}: {
  title?: string
  message: string
  requestId?: string
  suggestion?: string
  onRetry?: () => void
}) {
  return (
    <Alert
      type="error"
      showIcon
      message={title}
      description={
        <div className="aik-error-detail">
          <p>{message}</p>
          {suggestion && <p>{suggestion}</p>}
          {requestId && <code>请求 ID：{requestId}</code>}
          {onRetry && <Button onClick={onRetry}>重新加载</Button>}
        </div>
      }
    />
  )
}

