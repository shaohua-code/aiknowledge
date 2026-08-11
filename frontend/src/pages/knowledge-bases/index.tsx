import { useState } from 'react'
import { Button, Input, Select, Space, Table, Popconfirm, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { KnowledgeBase, CreateKnowledgeBasePayload } from '@/api/knowledge-bases'
import {
  useKnowledgeBases,
  useCreateKnowledgeBase,
  useToggleKnowledgeBase,
  useDeleteKnowledgeBase
} from './hooks/useKnowledgeBases'
import KnowledgeBaseCreateModal from './components/KnowledgeBaseCreateModal'
import KnowledgeBaseStatusTag from './components/KnowledgeBaseStatusTag'
import { useCurrentProject } from '@/stores/project'

/**
 * 知识库列表页
 * - 表格展示知识库（code、name、embeddingModel、embeddingDimension、documentCount、status）
 * - 创建、停用/启用、删除（仅空知识库可删除）
 * - 查询条件集中：keyword、status
 * - 切换项目时由 ProjectLayout 清空 TanStack Query 缓存
 */
export default function KnowledgeBasesPage() {
  const currentProject = useCurrentProject()

  // 查询条件集中在一个对象
  const [formData, setFormData] = useState<{ keyword: string; status: string }>({
    keyword: '',
    status: ''
  })

  // 创建弹窗状态
  const [createOpen, setCreateOpen] = useState(false)

  // 调用 listKnowledgeBases，仅以 status 作为后端过滤参数；keyword 在前端本地过滤
  const { data, isLoading } = useKnowledgeBases({ status: formData.status || undefined })
  const createMutation = useCreateKnowledgeBase()
  const toggleMutation = useToggleKnowledgeBase()
  const deleteMutation = useDeleteKnowledgeBase()

  // 前端本地 keyword 过滤
  const filteredData = (data || []).filter((item) => {
    if (!formData.keyword) return true
    const kw = formData.keyword.toLowerCase()
    return (
      item.code.toLowerCase().includes(kw) ||
      item.name.toLowerCase().includes(kw) ||
      (item.description || '').toLowerCase().includes(kw)
    )
  })

  // 表格列定义
  const columns: ColumnsType<KnowledgeBase> = [
    { title: '知识库编码', dataIndex: 'code', key: 'code', width: 180 },
    { title: '名称', dataIndex: 'name', key: 'name' },
    {
      title: 'Embedding 模型',
      dataIndex: 'embeddingModel',
      key: 'embeddingModel',
      width: 200,
      render: (v: string) => v || '-'
    },
    {
      title: '向量维度',
      dataIndex: 'embeddingDimension',
      key: 'embeddingDimension',
      width: 100,
      render: (v: number) => v || '-'
    },
    {
      title: '文档数',
      dataIndex: 'documentCount',
      key: 'documentCount',
      width: 90,
      render: (v: number) => v ?? 0
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: KnowledgeBase['status']) => <KnowledgeBaseStatusTag status={status} />
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_v, record) => (
        <Space>
          <Popconfirm
            title={record.status === 'active' ? '确认停用该知识库？' : '确认启用该知识库？'}
            onConfirm={() => handleToggle(record)}
          >
            <Button type="link" size="small" danger={record.status === 'active'}>
              {record.status === 'active' ? '停用' : '启用'}
            </Button>
          </Popconfirm>
          <Popconfirm
            title="确认删除该知识库？"
            description="仅空知识库可删除，删除后不可恢复。"
            onConfirm={() => handleDelete(record)}
          >
            <Button type="link" size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      )
    }
  ]

  /** 停用/启用知识库 */
  function handleToggle(kb: KnowledgeBase) {
    const targetStatus = kb.status === 'active' ? 'disabled' : 'active'
    toggleMutation.mutate({ code: kb.code, targetStatus })
  }

  /** 删除知识库（仅空知识库可删除，由后端校验） */
  function handleDelete(kb: KnowledgeBase) {
    // 文档数为 0 才允许删除，前端预校验避免无效请求
    if ((kb.documentCount ?? 0) > 0) {
      message.warning('仅空知识库可删除，请先清空文档')
      return
    }
    deleteMutation.mutate(kb.code)
  }

  /** 创建知识库提交 */
  async function handleCreate(payload: CreateKnowledgeBasePayload) {
    await createMutation.mutateAsync(payload)
    setCreateOpen(false)
  }

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部标题区：显示当前项目上下文 */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex flex-col leading-tight">
          <span className="text-xl font-semibold text-gray-800">知识库管理</span>
          {currentProject && (
            <span className="text-sm text-gray-400">
              当前项目：{currentProject.name}（{currentProject.code}）
            </span>
          )}
        </div>
        <Button type="primary" onClick={() => setCreateOpen(true)}>创建知识库</Button>
      </div>

      {/* 查询条件区 */}
      <div className="mb-4 flex items-center gap-4">
        <Input
          placeholder="按编码/名称/描述搜索"
          allowClear
          value={formData.keyword}
          onChange={(e) => setFormData((f) => ({ ...f, keyword: e.target.value }))}
          style={{ width: 240 }}
        />
        <Select
          placeholder="状态筛选"
          allowClear
          value={formData.status || undefined}
          onChange={(v) => setFormData((f) => ({ ...f, status: v || '' }))}
          style={{ width: 160 }}
          options={[
            { label: '启用', value: 'active' },
            { label: '停用', value: 'disabled' }
          ]}
        />
      </div>

      {/* 知识库表格 */}
      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={filteredData}
        pagination={{ pageSize: 10, showSizeChanger: true }}
      />

      {/* 创建知识库弹窗 */}
      <KnowledgeBaseCreateModal
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onSubmit={handleCreate}
      />
    </div>
  )
}
