import { useQuery } from '@tanstack/react-query'
import { Button, Progress } from 'antd'
import { Link } from 'react-router-dom'
import { ErrorState, LoadingState, MetricCard, PageHeader, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { intelligenceApi, knowledgeApi, operationApi } from '@/api/platform'
import { buildApplicationActions } from '@/features/applications/applicationActions'
import { useApplicationContext } from '@/hooks/useApplicationContext'

export default function ApplicationOverviewPage() {
  const { applicationId, environmentId, application, environment } = useApplicationContext()
  const summary = useQuery({
    queryKey: ['operation-summary', applicationId, environmentId],
    queryFn: () => operationApi.summary(applicationId, environmentId)
  })
  const collections = useQuery({
    queryKey: ['collections', applicationId, environmentId],
    queryFn: () => knowledgeApi.collections(applicationId, environmentId)
  })
  const runs = useQuery({
    queryKey: ['ingestion-runs', applicationId, environmentId],
    queryFn: () => knowledgeApi.runs(applicationId, environmentId)
  })
  const answerProfiles = useQuery({
    queryKey: ['answer-profiles', applicationId, environmentId],
    queryFn: () => intelligenceApi.answerProfiles(applicationId, environmentId)
  })

  if (summary.isLoading || collections.isLoading || runs.isLoading) return <LoadingState rows={8} />
  const firstError = summary.error || collections.error || runs.error
  if (firstError) {
    const error = asPlatformError(firstError)
    return (
      <ErrorState
        message={error.message}
        requestId={error.requestId}
        suggestion={error.suggestion ?? undefined}
        onRetry={() => Promise.all([summary.refetch(), collections.refetch(), runs.refetch()])}
      />
    )
  }

  const metrics = summary.data!
  const knowledge = collections.data ?? []
  const ingestionRuns = runs.data ?? []
  const failures = ingestionRuns.filter((item) => item.status === 'failed')
  const base = `/applications/${applicationId}/${environmentId}`
  // 行动项只来自真实接口返回的资源状态，避免用“健康分数”掩盖尚未接入的能力。
  const nextActions = buildApplicationActions({
    collectionCount: knowledge.length,
    failedRunCount: failures.length,
    answerProfileCount: answerProfiles.data?.length,
    totalRequestCount: metrics.totalRequests
  })

  return (
    <div className="workspace-page">
      <PageHeader
        eyebrow={`${environment?.name ?? ''} · ${application?.code ?? ''}`}
        title={`${application?.name ?? 'AI 应用'} 概览`}
        description="从知识可用性、回答质量和运行故障开始，而不是从数据库表开始。"
        actions={
          <Link to={`${base}/intelligence`}>
            <Button type="primary">测试 AI 回答</Button>
          </Link>
        }
      />
      <section className="metric-grid">
        <MetricCard
          label="可用知识集合"
          value={knowledge.filter((item) => item.status === 'active').length}
          helper={`${knowledge.reduce((sum, item) => sum + item.documentCount, 0)} 份文档`}
        />
        <MetricCard
          label="调用成功率"
          // 没有完成请求时服务端返回 null，概览不能把未观测状态伪装为 100%。
          value={metrics.successRate == null ? '无数据' : `${Math.round(metrics.successRate * 100)}%`}
          helper={`共 ${metrics.totalRequests} 次请求，其中已完成 ${metrics.completedRequests} 次`}
        />
        <MetricCard
          label="平均响应"
          value={metrics.averageDurationMs == null ? '无数据' : `${metrics.averageDurationMs} ms`}
          helper="检索与回答综合耗时"
        />
        <MetricCard
          label="模型兜底率"
          value={metrics.modelFallbackRate == null ? '无数据' : `${Math.round(metrics.modelFallbackRate * 100)}%`}
          helper="知识未命中后使用通用能力"
        />
      </section>

      <section className="panel-card action-workbench" aria-labelledby="action-workbench-title">
        <div className="section-heading compact">
          <div>
            <span className="aik-eyebrow">NEXT ACTIONS</span>
            <h2 id="action-workbench-title">下一步工作台</h2>
          </div>
          <span className="action-workbench-count">{nextActions.length} 项待处理</span>
        </div>
        {nextActions.length === 0 ? (
          <div className="inline-success">
            <span>✓</span>
            <div>
              <strong>当前没有需要立即处理的事项</strong>
              <p>继续关注运行记录和知识变更，新的失败或接入缺口会显示在这里。</p>
            </div>
          </div>
        ) : (
          <div className="action-workbench-list">
            {nextActions.map((action, index) => (
              <Link className="action-workbench-item" key={action.key} to={`${base}/${action.path}`}>
                <span className="action-workbench-index">{String(index + 1).padStart(2, '0')}</span>
                <span>
                  <strong>{action.title}</strong>
                  <small>{action.description}</small>
                </span>
                <span aria-hidden="true">→</span>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="overview-grid">
        <article className="panel-card">
          <div className="section-heading compact">
            <div>
              <span className="aik-eyebrow">KNOWLEDGE HEALTH</span>
              <h2>知识健康</h2>
            </div>
            <Link to={`${base}/knowledge`}>管理知识</Link>
          </div>
          {knowledge.length === 0 ? (
            <div className="inline-empty">
              <strong>还没有知识集合</strong>
              <p>先建立简历规则、岗位知识或业务规范集合。</p>
              <Link to={`${base}/knowledge`}>立即创建 →</Link>
            </div>
          ) : (
            <div className="health-list">
              {knowledge.slice(0, 5).map((item) => (
                <div key={item.id}>
                  <span>
                    <strong>{item.name}</strong>
                    <small>{item.documentCount} 文档 · {item.chunkCount} 片段</small>
                  </span>
                  <StatusPill status={item.status} />
                </div>
              ))}
            </div>
          )}
        </article>

        <article className="panel-card">
          <div className="section-heading compact">
            <div>
              <span className="aik-eyebrow">ACTION REQUIRED</span>
              <h2>需要处理</h2>
            </div>
            <Link to={`${base}/automation`}>查看运行</Link>
          </div>
          {failures.length === 0 ? (
            <div className="inline-success">
              <span>✓</span>
              <div>
                <strong>当前没有失败的入库任务</strong>
                <p>系统会把解析、切割和向量化问题集中到这里。</p>
              </div>
            </div>
          ) : (
            <div className="health-list danger">
              {failures.slice(0, 5).map((item) => (
                <div key={item.id}>
                  <span>
                    <strong>{item.errorCode || 'INGESTION_FAILED'}</strong>
                    <small>{item.errorMessage || `失败阶段：${item.stage}`}</small>
                  </span>
                  <StatusPill status="failed" />
                </div>
              ))}
            </div>
          )}
        </article>
      </section>

      {ingestionRuns.some((item) => ['queued', 'running'].includes(item.status)) && (
        <section className="panel-card">
          <div className="section-heading compact">
            <h2>正在处理知识</h2>
          </div>
          {ingestionRuns
            .filter((item) => ['queued', 'running'].includes(item.status))
            .map((item) => (
              <div className="run-progress" key={item.id}>
                <span>{item.stage}</span>
                <Progress percent={item.progress} size="small" />
              </div>
            ))}
        </section>
      )}
    </div>
  )
}
