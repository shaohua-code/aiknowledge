import { useQuery } from '@tanstack/react-query'
import { Progress, Table } from 'antd'
import { ErrorState, LoadingState, PageHeader, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { knowledgeApi } from '@/api/platform'
import { useApplicationContext } from '@/hooks/useApplicationContext'

export default function AutomationPage() {
  const { applicationId, environmentId } = useApplicationContext()
  const runs = useQuery({
    queryKey: ['ingestion-runs', applicationId, environmentId],
    queryFn: () => knowledgeApi.runs(applicationId, environmentId),
    refetchInterval: 5000
  })
  if (runs.isLoading) return <LoadingState rows={8} />
  if (runs.isError) {
    const error = asPlatformError(runs.error)
    return (
      <ErrorState
        message={error.message}
        requestId={error.requestId}
        suggestion={error.suggestion ?? undefined}
        onRetry={() => runs.refetch()}
      />
    )
  }

  return (
    <div className="workspace-page">
      <PageHeader
        eyebrow="数据自动化"
        title="所有后台处理都有状态、有阶段、有错误"
        description="当前展示知识入库运行；定时数据源同步将在连接器能力启用后使用同一运行模型。"
      />
      <Table
        rowKey="id"
        dataSource={runs.data ?? []}
        locale={{ emptyText: '还没有自动化运行记录' }}
        columns={[
          {
            title: '状态',
            dataIndex: 'status',
            render: (status: string) => <StatusPill status={status} />
          },
          { title: '阶段', dataIndex: 'stage' },
          {
            title: '进度',
            dataIndex: 'progress',
            render: (value: number) => <Progress percent={value} size="small" />
          },
          { title: '重试', dataIndex: 'retryCount' },
          { title: '错误码', dataIndex: 'errorCode', render: (value) => value || '-' },
          { title: '错误说明', dataIndex: 'errorMessage', ellipsis: true, render: (value) => value || '-' },
          { title: '创建时间', dataIndex: 'createdAt', render: (value) => new Date(value).toLocaleString() }
        ]}
      />
    </div>
  )
}

