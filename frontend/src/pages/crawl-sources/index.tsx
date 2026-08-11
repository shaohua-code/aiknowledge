import { useState } from 'react'
import { Button, Input, Select, Space, Table, Popconfirm, Tag } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import type { CrawlSource, CreateCrawlSourcePayload, CrawlSourceType } from '@/api/crawl-sources'
import {
  useCrawlSources,
  useCreateCrawlSource,
  useUpdateCrawlSource,
  useDeleteCrawlSource,
  useToggleCrawlSource,
  useRunCrawlSource
} from './hooks/useCrawlSources'
import CrawlSourceCreateModal from './components/CrawlSourceCreateModal'
import CrawlSourceStatusTag from './components/CrawlSourceStatusTag'
import { useKnowledgeBases } from '@/pages/knowledge-bases/hooks/useKnowledgeBases'
import { useCurrentProject } from '@/stores/project'

// 采集类型筛选选项
const TYPE_OPTIONS: { label: string; value: CrawlSourceType }[] = [
  { label: '网页', value: 'WEB' },
  { label: '站点地图', value: 'SITEMAP' },
  { label: 'RSS', value: 'RSS' },
  { label: 'API', value: 'API' }
]

// 采集类型文案映射
const TYPE_LABEL: Record<CrawlSourceType, string> = {
  WEB: '网页',
  SITEMAP: '站点地图',
  RSS: 'RSS',
  API: 'API'
}

/**
 * 采集源管理页
 * - 表格：code、name、type、startUrls、allowedDomains、importPolicy、destinationKnowledgeBaseId、enabled、lastRunAt
 * - 行操作：编辑、暂停/恢复、手动运行、查看采集记录
 * - 查询条件集中在 useState 对象（keyword 本地过滤 + type/enabled 后端过滤）
 */
export default function CrawlSourcesPage() {
  const currentProject = useCurrentProject()
  const navigate = useNavigate()
  const { projectId } = useParams()

  // 查询条件集中管理
  const [formData, setFormData] = useState<{
    keyword: string
    type: string
    enabled: string
  }>({ keyword: '', type: '', enabled: '' })

  // 创建/编辑弹窗状态
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<CrawlSource | null>(null)

  // 目标知识库列表（用于下拉选择）
  const { data: knowledgeBases = [] } = useKnowledgeBases({ status: 'active' })

  const { data, isLoading } = useCrawlSources({
    type: (formData.type as CrawlSourceType) || undefined,
    enabled: formData.enabled === '' ? undefined : formData.enabled === 'enabled'
  })
  const createMutation = useCreateCrawlSource()
  const updateMutation = useUpdateCrawlSource()
  const deleteMutation = useDeleteCrawlSource()
  const toggleMutation = useToggleCrawlSource()
  const runMutation = useRunCrawlSource()

  // 前端本地 keyword 过滤（按 code/name 模糊匹配）
  const filteredData = (data || []).filter((item) => {
    if (!formData.keyword) return true
    const kw = formData.keyword.toLowerCase()
    return (
      item.code.toLowerCase().includes(kw) ||
      item.name.toLowerCase().includes(kw)
    )
  })

  /** 打开新建弹窗 */
  function handleOpenCreate() {
    setEditing(null)
    setCreateOpen(true)
  }

  /** 打开编辑弹窗 */
  function handleEdit(record: CrawlSource) {
    setEditing(record)
    setCreateOpen(true)
  }

  /** 弹窗提交统一入口（区分创建/更新） */
  async function handleSubmit(payload: CreateCrawlSourcePayload) {
    if (editing) {
      await updateMutation.mutateAsync({ id: editing.id, payload })
    } else {
      await createMutation.mutateAsync(payload)
    }
    setCreateOpen(false)
    setEditing(null)
  }

  /** 跳转到该采集源的运行记录页 */
  function handleViewRuns(record: CrawlSource) {
    navigate(`/projects/${projectId}/crawl-runs?sourceId=${record.id}`)
  }

  // 表格列定义
  const columns: ColumnsType<CrawlSource> = [
    { title: '编码', dataIndex: 'code', key: 'code', width: 150 },
    { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 100,
      render: (v: CrawlSourceType) => <Tag color="blue">{TYPE_LABEL[v] || v}</Tag>
    },
    {
      title: '起始 URL',
      dataIndex: 'startUrls',
      key: 'startUrls',
      width: 220,
      render: (urls: string[]) => (
        <div className="text-xs text-gray-500">
          {(urls || []).slice(0, 2).map((u, i) => <div key={i} className="truncate">{u}</div>)}
          {urls.length > 2 && <div>等 {urls.length} 个</div>}
        </div>
      )
    },
    {
      title: '允许域名',
      dataIndex: 'allowedDomains',
      key: 'allowedDomains',
      width: 160,
      render: (domains: string[]) => (domains || []).join(', ')
    },
    {
      title: '导入策略',
      dataIndex: 'importPolicy',
      key: 'importPolicy',
      width: 100,
      render: (v: CrawlSource['importPolicy']) => {
        const map: Record<string, { color: string; label: string }> = {
          AUTO: { color: 'green', label: '自动' },
          REVIEW: { color: 'orange', label: '审核' },
          DRAFT: { color: 'default', label: '草稿' }
        }
        const meta = map[v] || { color: 'default', label: v }
        return <Tag color={meta.color}>{meta.label}</Tag>
      }
    },
    {
      title: '目标知识库',
      dataIndex: 'destinationKnowledgeBaseId',
      key: 'destinationKnowledgeBaseId',
      width: 150,
      render: (v?: string) => {
        if (!v) return '-'
        const kb = knowledgeBases.find((k) => k.id === v)
        return kb ? kb.name : <span className="text-xs text-gray-400">{v}</span>
      }
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 90,
      render: (enabled: boolean) => <CrawlSourceStatusTag enabled={enabled} />
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
      fixed: 'right',
      render: (_v, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => handleEdit(record)}>编辑</Button>
          <Popconfirm
            title={record.enabled ? '确认暂停该采集源？' : '确认恢复该采集源？'}
            onConfirm={() => toggleMutation.mutate({ id: record.id, enabled: record.enabled })}
          >
            <Button type="link" size="small" danger={record.enabled}>
              {record.enabled ? '暂停' : '恢复'}
            </Button>
          </Popconfirm>
          <Popconfirm
            title="确认手动运行一次采集？"
            onConfirm={() => runMutation.mutate(record.id)}
          >
            <Button type="link" size="small">手动运行</Button>
          </Popconfirm>
          <Button type="link" size="small" onClick={() => handleViewRuns(record)}>采集记录</Button>
          <Popconfirm
            title="确认删除该采集源？"
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
          <span className="text-xl font-semibold text-gray-800">采集源管理</span>
          {currentProject && (
            <span className="text-sm text-gray-400">
              当前项目：{currentProject.name}（{currentProject.code}）
            </span>
          )}
        </div>
        <Button type="primary" onClick={handleOpenCreate}>创建采集源</Button>
      </div>

      {/* 查询条件区 */}
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Input
          placeholder="按编码/名称搜索"
          allowClear
          value={formData.keyword}
          onChange={(e) => setFormData((f) => ({ ...f, keyword: e.target.value }))}
          style={{ width: 220 }}
        />
        <Select
          placeholder="类型筛选"
          allowClear
          value={formData.type || undefined}
          onChange={(v) => setFormData((f) => ({ ...f, type: v || '' }))}
          style={{ width: 160 }}
          options={TYPE_OPTIONS}
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

      {/* 采集源表格 */}
      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={filteredData}
        pagination={{ pageSize: 10, showSizeChanger: true }}
        scroll={{ x: 1200 }}
      />

      {/* 创建/编辑弹窗 */}
      <CrawlSourceCreateModal
        open={createOpen}
        initial={editing}
        knowledgeBases={knowledgeBases}
        onCancel={() => { setCreateOpen(false); setEditing(null) }}
        onSubmit={handleSubmit}
      />
    </div>
  )
}
