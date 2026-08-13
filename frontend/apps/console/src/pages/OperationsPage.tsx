import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Descriptions, Drawer, Input, List, Select, Table } from 'antd'
import { ErrorState, LoadingState, MetricCard, PageHeader, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { operationApi } from '@/api/platform'
import { useApplicationContext } from '@/hooks/useApplicationContext'

export default function OperationsPage() {
  const { applicationId, environmentId } = useApplicationContext()
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState('')
  const [selectedRequestId, setSelectedRequestId] = useState('')
  const summary = useQuery({
    queryKey: ['operation-summary', applicationId, environmentId],
    queryFn: () => operationApi.summary(applicationId, environmentId)
  })
  const traces = useQuery({
    queryKey: ['operation-traces', applicationId, environmentId],
    queryFn: () => operationApi.traces(applicationId, environmentId),
    refetchInterval: 10_000
  })
  const detail = useQuery({
    queryKey: ['operation-trace', applicationId, environmentId, selectedRequestId],
    queryFn: () => operationApi.trace(applicationId, environmentId, selectedRequestId),
    enabled: Boolean(selectedRequestId)
  })
  const filtered = useMemo(
    () =>
      (traces.data ?? []).filter((item) => {
        const matchesStatus = !status || item.status === status
        const normalized = keyword.trim().toLowerCase()
        const matchesKeyword =
          !normalized ||
          item.requestId.toLowerCase().includes(normalized) ||
          item.profileCode?.toLowerCase().includes(normalized) ||
          item.errorCode?.toLowerCase().includes(normalized)
        return matchesStatus && matchesKeyword
      }),
    [keyword, status, traces.data]
  )

  if (summary.isLoading || traces.isLoading) return <LoadingState rows={8} />
  const firstError = summary.error || traces.error
  if (firstError) {
    const error = asPlatformError(firstError)
    return (
      <ErrorState
        message={error.message}
        requestId={error.requestId}
        suggestion={error.suggestion ?? undefined}
        onRetry={() => Promise.all([summary.refetch(), traces.refetch()])}
      />
    )
  }
  const metrics = summary.data!

  return (
    <div className="workspace-page">
      <PageHeader
        eyebrow="运行与质量"
        title="每次回答都能解释发生了什么"
        description="按请求 ID 串联检索、证据、回答模式、模型用量、错误和降级原因。"
      />
      <section className="metric-grid">
        <MetricCard label="总请求" value={metrics.totalRequests} />
        <MetricCard label="成功率" value={`${Math.round(metrics.successRate * 100)}%`} />
        <MetricCard label="平均耗时" value={`${metrics.averageDurationMs} ms`} />
        <MetricCard label="模型兜底率" value={`${Math.round(metrics.modelFallbackRate * 100)}%`} />
      </section>
      <section className="table-toolbar">
        <Input.Search
          allowClear
          placeholder="搜索请求 ID、策略或错误码"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
        <Select
          allowClear
          placeholder="全部状态"
          value={status || undefined}
          onChange={(value) => setStatus(value || '')}
          options={[
            { value: 'succeeded', label: '成功' },
            { value: 'failed', label: '失败' },
            { value: 'running', label: '运行中' }
          ]}
        />
      </section>
      <Table
        rowKey="id"
        dataSource={filtered}
        onRow={(row) => ({ onClick: () => setSelectedRequestId(row.requestId) })}
        rowClassName="clickable-row"
        locale={{ emptyText: traces.data?.length ? '没有符合筛选条件的请求' : '还没有运行记录' }}
        columns={[
          { title: '请求 ID', dataIndex: 'requestId', render: (value) => <code>{value}</code> },
          { title: '能力', dataIndex: 'profileCode', render: (value) => value || '-' },
          { title: '操作', dataIndex: 'operation' },
          { title: '回答模式', dataIndex: 'answerMode', render: (value) => value || '-' },
          { title: '证据', dataIndex: 'evidenceCount' },
          { title: '耗时', dataIndex: 'totalMs', render: (value) => value == null ? '-' : `${value} ms` },
          { title: '状态', dataIndex: 'status', render: (value) => <StatusPill status={value} /> },
          { title: '错误码', dataIndex: 'errorCode', render: (value) => value || '-' },
          { title: '时间', dataIndex: 'createdAt', render: (value) => new Date(value).toLocaleString() }
        ]}
      />
      <Drawer
        title="运行轨迹详情"
        width={640}
        open={Boolean(selectedRequestId)}
        onClose={() => setSelectedRequestId('')}
      >
        {detail.isLoading ? (
          <LoadingState />
        ) : detail.isError ? (
          <ErrorState
            message={asPlatformError(detail.error).message}
            requestId={asPlatformError(detail.error).requestId}
            onRetry={() => detail.refetch()}
          />
        ) : detail.data ? (
          <>
            <Descriptions
              column={1}
              items={[
                { key: 'request', label: '请求 ID', children: <code>{detail.data.requestId}</code> },
                { key: 'status', label: '状态', children: <StatusPill status={detail.data.status} /> },
                { key: 'mode', label: '回答模式', children: detail.data.answerMode || '-' },
                { key: 'error', label: '错误码', children: detail.data.errorCode || '-' },
                { key: 'duration', label: '耗时', children: `${detail.data.totalMs ?? 0} ms` },
                { key: 'degraded', label: '降级原因', children: detail.data.degradedReasons.join('；') || '-' }
              ]}
            />
            <h3>证据轨迹</h3>
            <List
              dataSource={detail.data.evidence}
              locale={{ emptyText: '本次请求没有使用证据' }}
              renderItem={(item) => (
                <List.Item>
                  <List.Item.Meta
                    title={`${item.title} · ${Math.round(item.score * 100)}%`}
                    description={item.excerpt}
                  />
                </List.Item>
              )}
            />
          </>
        ) : null}
      </Drawer>
    </div>
  )
}
