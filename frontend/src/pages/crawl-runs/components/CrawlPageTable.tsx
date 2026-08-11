import { useState } from 'react'
import { Table, Tag, Popconfirm, Button, Input, Space } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { CrawlPage, CrawlPageStatus } from '@/api/crawl-sources'

interface CrawlPageTableProps {
  /** 采集页面列表 */
  pages: CrawlPage[]
  /** 列表加载中 */
  loading?: boolean
  /** 审核通过回调（触发入库） */
  onApprove: (pageId: string) => void
  /** 拒绝回调 */
  onReject: (pageId: string) => void
  /** 审核操作 loading 中的 pageId */
  actionLoadingId?: string | null
}

// 页面状态映射（颜色 + 文案）
const STATUS_META: Record<CrawlPageStatus, { color: string; label: string }> = {
  DISCOVERED: { color: 'default', label: '已发现' },
  SUCCESS: { color: 'blue', label: '抓取成功' },
  DUPLICATE: { color: 'warning', label: '重复' },
  FAILED: { color: 'error', label: '失败' },
  IMPORTED: { color: 'success', label: '已入库' },
  PENDING_REVIEW: { color: 'processing', label: '待审核' },
  APPROVED: { color: 'success', label: '已通过' },
  REJECTED: { color: 'default', label: '已拒绝' }
}

/**
 * 采集页面列表表格
 * - 字段：url、title、status、httpStatus、durationMs
 * - 行操作：审核通过（仅 PENDING_REVIEW 显示）、拒绝
 * - 支持本地按 url/title 状态搜索
 */
export default function CrawlPageTable({
  pages,
  loading,
  onApprove,
  onReject,
  actionLoadingId
}: CrawlPageTableProps) {
  // 本地关键词过滤
  const [keyword, setKeyword] = useState('')

  const filtered = (pages || []).filter((p) => {
    if (!keyword) return true
    const kw = keyword.toLowerCase()
    return (
      p.url.toLowerCase().includes(kw) ||
      (p.title || '').toLowerCase().includes(kw) ||
      p.status.toLowerCase().includes(kw)
    )
  })

  // 表格列定义
  const columns: ColumnsType<CrawlPage> = [
    {
      title: 'URL',
      dataIndex: 'url',
      key: 'url',
      ellipsis: true,
      render: (url: string) => (
        <a href={url} target="_blank" rel="noreferrer" className="text-blue-500">{url}</a>
      )
    },
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (v?: string) => v || '-'
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (status: CrawlPageStatus) => {
        const meta = STATUS_META[status] || STATUS_META.DISCOVERED
        return <Tag color={meta.color}>{meta.label}</Tag>
      }
    },
    {
      title: 'HTTP',
      dataIndex: 'httpStatus',
      key: 'httpStatus',
      width: 80,
      render: (v?: number) => v ?? '-'
    },
    {
      title: '耗时(ms)',
      dataIndex: 'durationMs',
      key: 'durationMs',
      width: 100,
      render: (v?: number) => v ?? '-'
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_v, record) => {
        // 仅待审核状态显示审核操作
        if (record.status !== 'PENDING_REVIEW') {
          return <span className="text-xs text-gray-400">无可执行操作</span>
        }
        const loading = actionLoadingId === record.id
        return (
          <Space>
            <Popconfirm
              title="确认通过审核？将触发入库。"
              onConfirm={() => onApprove(record.id)}
              disabled={loading}
            >
              <Button type="link" size="small" loading={loading}>通过</Button>
            </Popconfirm>
            <Popconfirm
              title="确认拒绝该页面？"
              onConfirm={() => onReject(record.id)}
              disabled={loading}
            >
              <Button type="link" size="small" danger disabled={loading}>拒绝</Button>
            </Popconfirm>
          </Space>
        )
      }
    }
  ]

  return (
    <div className="flex flex-col gap-2">
      <Input
        placeholder="按 URL/标题/状态搜索"
        allowClear
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
        style={{ width: 280 }}
      />
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={filtered}
        pagination={{ pageSize: 5, showSizeChanger: false }}
      />
    </div>
  )
}
