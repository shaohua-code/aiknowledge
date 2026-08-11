import { useState } from 'react'
import { Table, Drawer, Input, Descriptions, Empty, Tag } from 'antd'
import { useSearchParams } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import type { ScheduleRun } from '@/api/schedules'
import { useScheduleRuns } from './hooks/useScheduleRuns'
import RunStatusTag from './components/RunStatusTag'
import { useCurrentProject } from '@/stores/project'

/**
 * 定时任务运行记录页
 * - 从 URL 查询参数读取 scheduleId（由定时任务页"运行记录"跳转携带）
 * - 表格：plannedAt、startedAt、completedAt、status、attempt、duration
 * - 点击行查看详情 Drawer（result_summary、error）
 */
export default function ScheduleRunsPage() {
  const currentProject = useCurrentProject()
  // 从 URL 读取 scheduleId
  const [searchParams] = useSearchParams()
  const scheduleId = searchParams.get('scheduleId') || ''

  // 关键词本地过滤（按状态/错误信息）
  const [keyword, setKeyword] = useState('')
  // 选中的运行记录（用于详情 Drawer）
  const [selected, setSelected] = useState<ScheduleRun | null>(null)

  const { data = [], isLoading } = useScheduleRuns(scheduleId || undefined)

  // 本地关键词过滤
  const filteredData = (data || []).filter((item) => {
    if (!keyword) return true
    const kw = keyword.toLowerCase()
    return (
      item.status.toLowerCase().includes(kw) ||
      (item.error || '').toLowerCase().includes(kw) ||
      (item.resultSummary || '').toLowerCase().includes(kw)
    )
  })

  // 表格列定义
  const columns: ColumnsType<ScheduleRun> = [
    {
      title: '计划时间',
      dataIndex: 'plannedAt',
      key: 'plannedAt',
      width: 170,
      render: (v: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-')
    },
    {
      title: '开始时间',
      dataIndex: 'startedAt',
      key: 'startedAt',
      width: 170,
      render: (v?: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-')
    },
    {
      title: '完成时间',
      dataIndex: 'completedAt',
      key: 'completedAt',
      width: 170,
      render: (v?: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-')
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: ScheduleRun['status']) => <RunStatusTag status={status} />
    },
    {
      title: '尝试次数',
      dataIndex: 'attempt',
      key: 'attempt',
      width: 90,
      render: (v?: number) => v ?? 0
    },
    {
      title: '耗时(秒)',
      dataIndex: 'duration',
      key: 'duration',
      width: 100,
      render: (v?: number) => v ?? '-'
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_v, record) => (
        <a onClick={() => setSelected(record)}>查看详情</a>
      )
    }
  ]

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部标题 */}
      <div className="mb-4 flex flex-col leading-tight">
        <span className="text-xl font-semibold text-gray-800">定时任务运行记录</span>
        {currentProject && (
          <span className="text-sm text-gray-400">
            当前项目：{currentProject.name}（{currentProject.code}）
          </span>
        )}
      </div>

      {/* 未指定 scheduleId 时的提示 */}
      {!scheduleId && (
        <div className="mb-4 rounded border border-yellow-300 bg-yellow-50 p-3 text-sm text-yellow-700">
          请从「定时任务」页点击「运行记录」进入，以查看指定任务的运行历史。
        </div>
      )}

      {/* 查询条件区 */}
      <div className="mb-4 flex items-center gap-4">
        <Input
          placeholder="按状态/错误/结果搜索"
          allowClear
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 260 }}
          disabled={!scheduleId}
        />
        {scheduleId && (
          <Tag color="blue" className="!m-0">scheduleId: {scheduleId}</Tag>
        )}
      </div>

      {/* 运行记录表格 */}
      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={filteredData}
        pagination={{ pageSize: 10, showSizeChanger: true }}
        locale={{ emptyText: scheduleId ? <Empty description="暂无运行记录" /> : <Empty description="请先选择定时任务" /> }}
      />

      {/* 详情 Drawer */}
      <Drawer
        title="运行记录详情"
        open={!!selected}
        onClose={() => setSelected(null)}
        width={520}
      >
        {selected && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="记录 ID">{selected.id}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <RunStatusTag status={selected.status} />
            </Descriptions.Item>
            <Descriptions.Item label="计划时间">
              {selected.plannedAt ? dayjs(selected.plannedAt).format('YYYY-MM-DD HH:mm:ss') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="开始时间">
              {selected.startedAt ? dayjs(selected.startedAt).format('YYYY-MM-DD HH:mm:ss') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="完成时间">
              {selected.completedAt ? dayjs(selected.completedAt).format('YYYY-MM-DD HH:mm:ss') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="尝试次数">{selected.attempt ?? 0}</Descriptions.Item>
            <Descriptions.Item label="耗时(秒)">{selected.duration ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="结果摘要">
              {selected.resultSummary || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="错误信息">
              {selected.error ? <span className="text-red-500">{selected.error}</span> : '-'}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </div>
  )
}
