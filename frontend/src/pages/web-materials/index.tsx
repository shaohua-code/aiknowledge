import { useState } from 'react'
import { Table, Select, Input, Space, Button, Popconfirm, Drawer, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import type { WebMaterial, WebMaterialStatus } from '@/api/crawl-sources'
import {
  useWebMaterials,
  useApproveWebMaterial,
  useRejectWebMaterial
} from './hooks/useWebMaterials'
import { useCurrentProject } from '@/stores/project'

// 状态映射（颜色 + 文案）
const STATUS_META: Record<WebMaterialStatus, { color: string; label: string }> = {
  PENDING_REVIEW: { color: 'processing', label: '待审核' },
  APPROVED: { color: 'success', label: '已采用' },
  REJECTED: { color: 'default', label: '已拒绝' },
  IMPORTED: { color: 'green', label: '已入库' }
}

// 状态筛选选项
const STATUS_OPTIONS: { label: string; value: WebMaterialStatus }[] = [
  { label: '待审核', value: 'PENDING_REVIEW' },
  { label: '已采用', value: 'APPROVED' },
  { label: '已拒绝', value: 'REJECTED' },
  { label: '已入库', value: 'IMPORTED' }
]

/**
 * 网络资料池页（P1）
 * - 表格：title、sourceUrl、status、reviewedAt
 * - 行操作：采用（触发入库）、拒绝、查看内容
 * - 查询条件集中在 useState 对象（status 后端过滤 + keyword 本地过滤）
 */
export default function WebMaterialsPage() {
  const currentProject = useCurrentProject()

  // 查询条件集中管理
  const [formData, setFormData] = useState<{
    keyword: string
    status: string
  }>({ keyword: '', status: '' })

  // 选中的资料（用于内容查看 Drawer）
  const [selected, setSelected] = useState<WebMaterial | null>(null)

  const { data, isLoading } = useWebMaterials({
    status: (formData.status as WebMaterialStatus) || undefined
  })
  const approveMutation = useApproveWebMaterial()
  const rejectMutation = useRejectWebMaterial()

  // 前端本地 keyword 过滤（按 title/sourceUrl）
  const filteredData = (data || []).filter((item) => {
    if (!formData.keyword) return true
    const kw = formData.keyword.toLowerCase()
    return (
      item.title.toLowerCase().includes(kw) ||
      item.sourceUrl.toLowerCase().includes(kw)
    )
  })

  // 表格列定义
  const columns: ColumnsType<WebMaterial> = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true
    },
    {
      title: '来源 URL',
      dataIndex: 'sourceUrl',
      key: 'sourceUrl',
      ellipsis: true,
      render: (url: string) => (
        <a href={url} target="_blank" rel="noreferrer" className="text-blue-500">{url}</a>
      )
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (status: WebMaterialStatus) => {
        const meta = STATUS_META[status] || STATUS_META.PENDING_REVIEW
        return <Tag color={meta.color}>{meta.label}</Tag>
      }
    },
    {
      title: '审核时间',
      dataIndex: 'reviewedAt',
      key: 'reviewedAt',
      width: 170,
      render: (v?: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-')
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_v, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => setSelected(record)}>查看内容</Button>
          {record.status === 'PENDING_REVIEW' && (
            <>
              <Popconfirm
                title="确认采用该资料？将触发入库。"
                onConfirm={() => approveMutation.mutate(record.id)}
              >
                <Button type="link" size="small">采用</Button>
              </Popconfirm>
              <Popconfirm
                title="确认拒绝该资料？"
                onConfirm={() => rejectMutation.mutate(record.id)}
              >
                <Button type="link" size="small" danger>拒绝</Button>
              </Popconfirm>
            </>
          )}
        </Space>
      )
    }
  ]

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部标题 */}
      <div className="mb-4 flex flex-col leading-tight">
        <span className="text-xl font-semibold text-gray-800">网络资料池</span>
        {currentProject && (
          <span className="text-sm text-gray-400">
            当前项目：{currentProject.name}（{currentProject.code}）
          </span>
        )}
        <span className="text-xs text-gray-400">采集过程中发现的待审核资料，采用后触发入库到目标知识库</span>
      </div>

      {/* 查询条件区 */}
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Input
          placeholder="按标题/来源 URL 搜索"
          allowClear
          value={formData.keyword}
          onChange={(e) => setFormData((f) => ({ ...f, keyword: e.target.value }))}
          style={{ width: 260 }}
        />
        <Select
          placeholder="状态筛选"
          allowClear
          value={formData.status || undefined}
          onChange={(v) => setFormData((f) => ({ ...f, status: v || '' }))}
          style={{ width: 160 }}
          options={STATUS_OPTIONS}
        />
      </div>

      {/* 资料表格 */}
      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={filteredData}
        pagination={{ pageSize: 10, showSizeChanger: true }}
      />

      {/* 内容查看 Drawer */}
      <Drawer
        title="资料内容"
        open={!!selected}
        onClose={() => setSelected(null)}
        width={560}
      >
        {selected && (
          <div className="flex flex-col gap-3">
            <div>
              <div className="mb-1 text-xs text-gray-400">标题</div>
              <div className="text-base font-medium text-gray-800">{selected.title}</div>
            </div>
            <div>
              <div className="mb-1 text-xs text-gray-400">来源 URL</div>
              <a href={selected.sourceUrl} target="_blank" rel="noreferrer" className="text-blue-500 break-all">
                {selected.sourceUrl}
              </a>
            </div>
            <div>
              <div className="mb-1 text-xs text-gray-400">状态</div>
              {(() => {
                const meta = STATUS_META[selected.status] || STATUS_META.PENDING_REVIEW
                return <Tag color={meta.color}>{meta.label}</Tag>
              })()}
            </div>
            <div>
              <div className="mb-1 text-xs text-gray-400">内容摘要</div>
              <div className="max-h-96 overflow-auto rounded border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700">
                {selected.contentSnippet || '暂无内容摘要'}
              </div>
            </div>
            {selected.reviewedAt && (
              <div className="text-xs text-gray-400">
                审核时间：{dayjs(selected.reviewedAt).format('YYYY-MM-DD HH:mm:ss')}
              </div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  )
}
