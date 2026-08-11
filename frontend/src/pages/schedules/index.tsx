import { useState } from 'react'
import { Button, Input, Select, Space, Table, Popconfirm } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import type { Schedule, CreateSchedulePayload, ScheduleTaskType } from '@/api/schedules'
import {
  useSchedules,
  useCreateSchedule,
  useUpdateSchedule,
  useDeleteSchedule,
  useToggleSchedule,
  useRunSchedule
} from './hooks/useSchedules'
import ScheduleCreateModal from './components/ScheduleCreateModal'
import ScheduleStatusTag from './components/ScheduleStatusTag'
import { useCurrentProject } from '@/stores/project'

// 任务类型筛选选项
const TASK_TYPE_OPTIONS: { label: string; value: ScheduleTaskType }[] = [
  { label: '知识库同步', value: 'KNOWLEDGE_SYNC' },
  { label: '采集源运行', value: 'CRAWL_SOURCE' },
  { label: '智能研究', value: 'RESEARCH' },
  { label: '向量刷新', value: 'EMBEDDING_REFRESH' },
  { label: '网络资料审核', value: 'WEB_MATERIAL_REVIEW' }
]

// 任务类型文案映射
const TASK_TYPE_LABEL: Record<ScheduleTaskType, string> = {
  KNOWLEDGE_SYNC: '知识库同步',
  CRAWL_SOURCE: '采集源运行',
  RESEARCH: '智能研究',
  EMBEDDING_REFRESH: '向量刷新',
  WEB_MATERIAL_REVIEW: '网络资料审核'
}

/**
 * 定时任务管理页
 * - 表格：name、taskType、cronExpression、timezone、enabled、nextRunAt、lastRunAt
 * - 行操作：编辑、暂停/恢复、手动运行、查看运行记录
 * - 查询条件集中在 useState 对象（keyword 本地过滤 + taskType 后端过滤 + enabled 后端过滤）
 */
export default function SchedulesPage() {
  const currentProject = useCurrentProject()
  const navigate = useNavigate()
  const { projectId } = useParams()

  // 查询条件集中管理
  const [formData, setFormData] = useState<{
    keyword: string
    taskType: string
    enabled: string
  }>({ keyword: '', taskType: '', enabled: '' })

  // 创建/编辑弹窗状态
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<Schedule | null>(null)

  const { data, isLoading } = useSchedules({
    taskType: (formData.taskType as ScheduleTaskType) || undefined,
    enabled: formData.enabled === '' ? undefined : formData.enabled === 'enabled'
  })
  const createMutation = useCreateSchedule()
  const updateMutation = useUpdateSchedule()
  const deleteMutation = useDeleteSchedule()
  const toggleMutation = useToggleSchedule()
  const runMutation = useRunSchedule()

  // 前端本地 keyword 过滤（按 name 模糊匹配）
  const filteredData = (data || []).filter((item) => {
    if (!formData.keyword) return true
    return item.name.toLowerCase().includes(formData.keyword.toLowerCase())
  })

  /** 打开新建弹窗 */
  function handleOpenCreate() {
    setEditing(null)
    setCreateOpen(true)
  }

  /** 打开编辑弹窗 */
  function handleEdit(record: Schedule) {
    setEditing(record)
    setCreateOpen(true)
  }

  /** 弹窗提交统一入口（区分创建/更新） */
  async function handleSubmit(payload: CreateSchedulePayload) {
    if (editing) {
      await updateMutation.mutateAsync({ id: editing.id, payload })
    } else {
      await createMutation.mutateAsync(payload)
    }
    setCreateOpen(false)
    setEditing(null)
  }

  /** 跳转到该任务的运行记录页 */
  function handleViewRuns(record: Schedule) {
    navigate(`/projects/${projectId}/schedule-runs?scheduleId=${record.id}`)
  }

  // 表格列定义
  const columns: ColumnsType<Schedule> = [
    { title: '任务名称', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '任务类型',
      dataIndex: 'taskType',
      key: 'taskType',
      width: 140,
      render: (v: ScheduleTaskType) => TASK_TYPE_LABEL[v] || v
    },
    {
      title: 'Cron 表达式',
      dataIndex: 'cronExpression',
      key: 'cronExpression',
      width: 160
    },
    {
      title: '时区',
      dataIndex: 'timezone',
      key: 'timezone',
      width: 150
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 90,
      render: (enabled: boolean) => <ScheduleStatusTag enabled={enabled} />
    },
    {
      title: '下次运行',
      dataIndex: 'nextRunAt',
      key: 'nextRunAt',
      width: 170,
      render: (v?: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-')
    },
    {
      title: '上次运行',
      dataIndex: 'lastRunAt',
      key: 'lastRunAt',
      width: 170,
      render: (v?: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-')
    },
    {
      title: '操作',
      key: 'action',
      width: 320,
      render: (_v, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => handleEdit(record)}>编辑</Button>
          <Popconfirm
            title={record.enabled ? '确认暂停该任务？' : '确认恢复该任务？'}
            onConfirm={() => toggleMutation.mutate({ id: record.id, enabled: record.enabled })}
          >
            <Button type="link" size="small" danger={record.enabled}>
              {record.enabled ? '暂停' : '恢复'}
            </Button>
          </Popconfirm>
          <Popconfirm
            title="确认手动运行一次？"
            onConfirm={() => runMutation.mutate(record.id)}
          >
            <Button type="link" size="small">手动运行</Button>
          </Popconfirm>
          <Button type="link" size="small" onClick={() => handleViewRuns(record)}>运行记录</Button>
          <Popconfirm
            title="确认删除该任务？"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button type="link" size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      )
    }
  ]

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部标题 */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex flex-col leading-tight">
          <span className="text-xl font-semibold text-gray-800">定时任务管理</span>
          {currentProject && (
            <span className="text-sm text-gray-400">
              当前项目：{currentProject.name}（{currentProject.code}）
            </span>
          )}
        </div>
        <Button type="primary" onClick={handleOpenCreate}>创建定时任务</Button>
      </div>

      {/* 查询条件区 */}
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Input
          placeholder="按任务名称搜索"
          allowClear
          value={formData.keyword}
          onChange={(e) => setFormData((f) => ({ ...f, keyword: e.target.value }))}
          style={{ width: 220 }}
        />
        <Select
          placeholder="任务类型筛选"
          allowClear
          value={formData.taskType || undefined}
          onChange={(v) => setFormData((f) => ({ ...f, taskType: v || '' }))}
          style={{ width: 180 }}
          options={TASK_TYPE_OPTIONS}
        />
        <Select
          placeholder="状态筛选"
          allowClear
          value={formData.enabled || undefined}
          onChange={(v) => setFormData((f) => ({ ...f, enabled: v || '' }))}
          style={{ width: 140 }}
          options={[
            { label: '启用', value: 'enabled' },
            { label: '暂停', value: 'paused' }
          ]}
        />
      </div>

      {/* 定时任务表格 */}
      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={filteredData}
        pagination={{ pageSize: 10, showSizeChanger: true }}
      />

      {/* 创建/编辑弹窗 */}
      <ScheduleCreateModal
        open={createOpen}
        initial={editing}
        onCancel={() => { setCreateOpen(false); setEditing(null) }}
        onSubmit={handleSubmit}
      />
    </div>
  )
}
