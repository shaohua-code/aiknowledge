import { useState } from 'react'
import { Table, Drawer, Input, Statistic, Row, Col, Empty, Tag } from 'antd'
import { useSearchParams } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import type { CrawlRun } from '@/api/crawl-sources'
import {
  useCrawlRunsBySource,
  useCrawlPages,
  useApproveCrawlPage,
  useRejectCrawlPage
} from './hooks/useCrawlRuns'
import RunStatusTag from './components/RunStatusTag'
import CrawlPageTable from './components/CrawlPageTable'
import { useCurrentProject } from '@/stores/project'

/**
 * 采集记录页
 * - 从 URL 查询参数读取 sourceId（由采集源页"采集记录"跳转携带）
 * - 上方：采集运行列表（startedAt、completedAt、status + 各计数）
 * - 点击行：右侧 Drawer 展示运行详情 + 页面列表 + 审核操作
 */
export default function CrawlRunsPage() {
  const currentProject = useCurrentProject()
  // 从 URL 读取 sourceId
  const [searchParams] = useSearchParams()
  const sourceId = searchParams.get('sourceId') || ''

  // 关键词本地过滤（按状态）
  const [keyword, setKeyword] = useState('')
  // 选中的运行记录（用于详情 Drawer）
  const [selected, setSelected] = useState<CrawlRun | null>(null)

  const { data: runs = [], isLoading } = useCrawlRunsBySource(sourceId || undefined)
  // 选中运行的页面列表查询
  const { data: pages = [], isLoading: pagesLoading } = useCrawlPages(selected?.id)
  const approveMutation = useApproveCrawlPage(selected?.id)
  const rejectMutation = useRejectCrawlPage(selected?.id)

  // 本地关键词过滤
  const filteredRuns = (runs || []).filter((r) => {
    if (!keyword) return true
    return r.status.toLowerCase().includes(keyword.toLowerCase())
  })

  // 表格列定义
  const columns: ColumnsType<CrawlRun> = [
    {
      title: '运行 ID',
      dataIndex: 'id',
      key: 'id',
      width: 160,
      ellipsis: true
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
      width: 110,
      render: (status: CrawlRun['status']) => <RunStatusTag status={status} />
    },
    {
      title: '发现',
      dataIndex: 'discoveredCount',
      key: 'discoveredCount',
      width: 80,
      render: (v: number) => v ?? 0
    },
    {
      title: '成功',
      dataIndex: 'successCount',
      key: 'successCount',
      width: 80,
      render: (v: number) => <span className="text-green-600">{v ?? 0}</span>
    },
    {
      title: '重复',
      dataIndex: 'duplicateCount',
      key: 'duplicateCount',
      width: 80,
      render: (v: number) => <span className="text-orange-500">{v ?? 0}</span>
    },
    {
      title: '失败',
      dataIndex: 'failedCount',
      key: 'failedCount',
      width: 80,
      render: (v: number) => <span className="text-red-500">{v ?? 0}</span>
    },
    {
      title: '已入库',
      dataIndex: 'importedCount',
      key: 'importedCount',
      width: 90,
      render: (v: number) => <span className="text-green-700">{v ?? 0}</span>
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_v, record) => (
        <a onClick={() => setSelected(record)}>查看页面</a>
      )
    }
  ]

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部标题 */}
      <div className="mb-4 flex flex-col leading-tight">
        <span className="text-xl font-semibold text-gray-800">采集记录</span>
        {currentProject && (
          <span className="text-sm text-gray-400">
            当前项目：{currentProject.name}（{currentProject.code}）
          </span>
        )}
      </div>

      {/* 未指定 sourceId 时的提示 */}
      {!sourceId && (
        <div className="mb-4 rounded border border-yellow-300 bg-yellow-50 p-3 text-sm text-yellow-700">
          请从「采集源」页点击「采集记录」进入，以查看指定采集源的运行历史。
        </div>
      )}

      {/* 查询条件区 */}
      <div className="mb-4 flex items-center gap-4">
        <Input
          placeholder="按状态搜索"
          allowClear
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 240 }}
          disabled={!sourceId}
        />
        {sourceId && (
          <Tag color="blue" className="!m-0">sourceId: {sourceId}</Tag>
        )}
      </div>

      {/* 采集运行列表 */}
      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={filteredRuns}
        pagination={{ pageSize: 10, showSizeChanger: true }}
        scroll={{ x: 1200 }}
        locale={{ emptyText: sourceId ? <Empty description="暂无采集记录" /> : <Empty description="请先选择采集源" /> }}
      />

      {/* 运行详情 + 页面列表 Drawer */}
      <Drawer
        title="采集运行详情"
        open={!!selected}
        onClose={() => setSelected(null)}
        width={760}
      >
        {selected && (
          <div className="flex flex-col gap-4">
            {/* 计数统计区 */}
            <Row gutter={16}>
              <Col span={5}>
                <Statistic title="发现" value={selected.discoveredCount ?? 0} />
              </Col>
              <Col span={5}>
                <Statistic title="成功" value={selected.successCount ?? 0} valueStyle={{ color: '#52c41a' }} />
              </Col>
              <Col span={4}>
                <Statistic title="重复" value={selected.duplicateCount ?? 0} valueStyle={{ color: '#fa8c16' }} />
              </Col>
              <Col span={5}>
                <Statistic title="失败" value={selected.failedCount ?? 0} valueStyle={{ color: '#f5222d' }} />
              </Col>
              <Col span={5}>
                <Statistic title="已入库" value={selected.importedCount ?? 0} valueStyle={{ color: '#389e0d' }} />
              </Col>
            </Row>
            <div className="text-sm text-gray-500">
              运行状态：<RunStatusTag status={selected.status} />
              {selected.error && (
                <div className="mt-2 text-red-500">错误：{selected.error}</div>
              )}
            </div>

            {/* 页面列表 + 审核操作 */}
            <div className="border-t border-gray-200 pt-3">
              <div className="mb-2 text-sm font-medium text-gray-700">页面列表与审核</div>
              <CrawlPageTable
                pages={pages}
                loading={pagesLoading}
                onApprove={(pageId) => approveMutation.mutate(pageId)}
                onReject={(pageId) => rejectMutation.mutate(pageId)}
                actionLoadingId={approveMutation.isPending || rejectMutation.isPending ? null : null}
              />
            </div>
          </div>
        )}
      </Drawer>
    </div>
  )
}
