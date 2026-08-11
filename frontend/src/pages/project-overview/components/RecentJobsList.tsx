import { Card, Empty, Spin, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { ExecutionJobSummary, ExecutionJobStatus } from '@/api/execution-logs'

interface RecentJobsListProps {
  /** 最近研究任务列表（最多 5 条） */
  jobs: ExecutionJobSummary[] | undefined
  /** 加载中状态 */
  loading?: boolean
}

/**
 * 研究任务状态标签
 * - succeeded：绿色
 * - failed/timeout：红色
 * - running：蓝色
 * - pending：默认
 */
function renderStatusTag(status: ExecutionJobStatus) {
  const map: Record<ExecutionJobStatus, { color: string; text: string }> = {
    succeeded: { color: 'green', text: '成功' },
    failed: { color: 'red', text: '失败' },
    timeout: { color: 'red', text: '超时' },
    running: { color: 'blue', text: '运行中' },
    pending: { color: 'default', text: '等待中' }
  }
  const cfg = map[status] || { color: 'default', text: status }
  return <Tag color={cfg.color}>{cfg.text}</Tag>
}

/**
 * 最近研究任务列表
 * - 展示最近 5 条研究任务（question、status、degraded、totalDurationMs、createdAt）
 */
export default function RecentJobsList({ jobs, loading }: RecentJobsListProps) {
  // 表格列定义
  const columns: ColumnsType<ExecutionJobSummary> = [
    {
      title: '问题',
      dataIndex: 'question',
      key: 'question',
      ellipsis: true,
      render: (v: string) => v || '-'
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: ExecutionJobStatus) => renderStatusTag(status)
    },
    {
      title: '降级',
      dataIndex: 'degraded',
      key: 'degraded',
      width: 80,
      render: (v?: boolean) => (v ? <Tag color="orange">降级</Tag> : '-')
    },
    {
      title: '耗时',
      dataIndex: 'totalDurationMs',
      key: 'totalDurationMs',
      width: 120,
      render: (v?: number) => (v !== undefined && v !== null ? `${v} ms` : '-')
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 180,
      render: (v?: string) => (v ? new Date(v).toLocaleString() : '-')
    }
  ]

  return (
    <Card
      title="最近研究任务"
      className="!rounded-2xl !border-slate-200/80 !bg-white/90 !shadow-[0_12px_32px_rgba(23,32,51,0.06)]"
    >
      <Spin spinning={loading}>
        {!jobs || jobs.length === 0 ? (
          <Empty description="暂无研究任务" />
        ) : (
          <Table
            rowKey="jobId"
            columns={columns}
            dataSource={jobs}
            pagination={false}
            size="small"
          />
        )}
      </Spin>
    </Card>
  )
}
