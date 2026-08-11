import { useState } from 'react'
import { Table, Drawer, Select, Input, Tag, Empty } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import type { ExecutionJobSummary, ExecutionJobStatus } from '@/api/execution-logs'
import { useExecutionJobs, useExecutionJobDetail } from './hooks/useExecutionLogs'
import JobDetailDrawer from './components/JobDetailDrawer'
import { useCurrentProject } from '@/stores/project'

/** 截断 question 用于表格展示 */
function truncate(text: string, len = 40): string {
  if (!text) return '-'
  return text.length > len ? text.slice(0, len) + '...' : text
}

/** 状态文案与颜色 */
const STATUS_META: Record<ExecutionJobStatus, { color: string; label: string }> = {
  pending: { color: 'default', label: '等待中' },
  running: { color: 'processing', label: '执行中' },
  succeeded: { color: 'green', label: '成功' },
  failed: { color: 'red', label: '失败' },
  timeout: { color: 'orange', label: '超时' }
}

/**
 * 执行记录页
 * - 任务列表表格：requestId、question 截断、status、degraded、totalDurationMs、createdAt
 * - 行操作：查看详情 Drawer
 * - 查询条件集中（status、keyword）
 * - 切换项目时清空缓存重新加载（hooks 内已处理）
 */
export default function ExecutionLogsPage() {
  const currentProject = useCurrentProject()
  const projectId = currentProject?.id

  // 查询条件集中管理
  const [formData, setFormData] = useState<{ status: string; keyword: string }>({
    status: '',
    keyword: ''
  })

  // 选中的任务（用于详情 Drawer）
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)

  // 列表查询（status 后端过滤，keyword 前端过滤）
  const { data: jobs = [], isLoading } = useExecutionJobs(
    projectId,
    formData.status ? { status: formData.status as ExecutionJobStatus } : undefined
  )

  // 详情查询（仅当选中任务时启用）
  const { data: detail, isLoading: detailLoading } = useExecutionJobDetail(projectId, selectedJobId || undefined)

  // 前端本地 keyword 过滤（按 question / requestId 模糊匹配）
  const filteredData = jobs.filter((item) => {
    if (!formData.keyword) return true
    const kw = formData.keyword.toLowerCase()
    return (
      item.question.toLowerCase().includes(kw) ||
      item.requestId.toLowerCase().includes(kw)
    )
  })

  // 表格列定义
  const columns: ColumnsType<ExecutionJobSummary> = [
    {
      title: '请求 ID',
      dataIndex: 'requestId',
      key: 'requestId',
      width: 200,
      render: (v: string) => <span className="font-mono text-xs">{v}</span>
    },
    {
      title: '问题',
      dataIndex: 'question',
      key: 'question',
      ellipsis: true,
      render: (v: string) => <span className="text-xs text-gray-600">{truncate(v)}</span>
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: ExecutionJobStatus) => (
        <Tag color={STATUS_META[status]?.color || 'default'} className="!m-0">
          {STATUS_META[status]?.label || status}
        </Tag>
      )
    },
    {
      title: '降级',
      dataIndex: 'degraded',
      key: 'degraded',
      width: 80,
      render: (degraded?: boolean) => (
        degraded ? <Tag color="orange" className="!m-0">降级</Tag> : <span className="text-gray-300">-</span>
      )
    },
    {
      title: '总耗时',
      dataIndex: 'totalDurationMs',
      key: 'totalDurationMs',
      width: 120,
      render: (v?: number) => (typeof v === 'number' ? `${v} ms` : '-')
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 170,
      render: (v?: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-')
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      fixed: 'right',
      render: (_v, record) => (
        <a onClick={() => setSelectedJobId(record.jobId)}>查看详情</a>
      )
    }
  ]

  // 未选择项目时提示
  if (!projectId) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <Empty description="尚未选择项目，请返回项目列表选择" />
      </div>
    )
  }

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部标题 */}
      <div className="mb-4 flex flex-col leading-tight">
        <span className="text-xl font-semibold text-gray-800">执行记录</span>
        {currentProject && (
          <span className="text-sm text-gray-400">
            当前项目：{currentProject.name}（{currentProject.code}）
          </span>
        )}
      </div>

      {/* 查询条件区 */}
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Select
          placeholder="状态筛选"
          allowClear
          value={formData.status || undefined}
          onChange={(v) => setFormData((f) => ({ ...f, status: v || '' }))}
          style={{ width: 160 }}
          options={[
            { label: '等待中', value: 'pending' },
            { label: '执行中', value: 'running' },
            { label: '成功', value: 'succeeded' },
            { label: '失败', value: 'failed' },
            { label: '超时', value: 'timeout' }
          ]}
        />
        <Input
          placeholder="按问题/请求 ID 搜索"
          allowClear
          value={formData.keyword}
          onChange={(e) => setFormData((f) => ({ ...f, keyword: e.target.value }))}
          style={{ width: 280 }}
        />
      </div>

      {/* 任务列表表格 */}
      <Table
        rowKey="jobId"
        loading={isLoading}
        columns={columns}
        dataSource={filteredData}
        pagination={{ pageSize: 10, showSizeChanger: true }}
        scroll={{ x: 1100 }}
      />

      {/* 详情 Drawer */}
      <Drawer
        title="任务详情"
        open={!!selectedJobId}
        onClose={() => setSelectedJobId(null)}
        width={640}
        destroyOnClose
      >
        <JobDetailDrawer detail={detail || null} loading={detailLoading} />
      </Drawer>
    </div>
  )
}
