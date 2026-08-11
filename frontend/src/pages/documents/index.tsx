import { useState } from 'react'
import { Button, Select, Table, Space, Popconfirm, Input, message, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { useCurrentProject } from '@/stores/project'
import { useKnowledgeBases } from '@/pages/knowledge-bases/hooks/useKnowledgeBases'
import {
  useDocuments,
  useUploadDocumentFile,
  useCreateDocument
} from './hooks/useDocuments'
import { DOCUMENT_STATUS_OPTIONS } from './hooks/useDocuments'
import DocumentStatusTag from './components/DocumentStatusTag'
import { isProcessingStatus } from './documentStatus'
import DocumentUploadModal from './components/DocumentUploadModal'
import DocumentChunkPreview from './components/DocumentChunkPreview'
import type { Document } from '@/api/documents'

/**
 * 文档管理页
 * - 顶部：知识库选择 + 状态过滤 + 关键词搜索 + 导入按钮
 * - 表格：title、sourceType、processingStatus（轮询）、chunkCount、enabled、createdAt
 * - 行操作：查看片段、重新处理、停用/启用、删除
 * - 查询条件集中在 useState 对象
 */
export default function DocumentsPage() {
  const currentProject = useCurrentProject()
  const { data: knowledgeBases = [] } = useKnowledgeBases({ status: 'active' })

  // 查询条件集中管理
  const [formData, setFormData] = useState<{
    keyword: string
    knowledgeBaseCode: string
    status: string
  }>({ keyword: '', knowledgeBaseCode: '', status: '' })

  const [uploadOpen, setUploadOpen] = useState(false)
  // 当前预览片段的文档
  const [previewDoc, setPreviewDoc] = useState<Document | null>(null)

  // 文档列表查询（仅选定知识库时启用）
  const { data: documents = [], isLoading } = useDocuments(formData.knowledgeBaseCode, {
    keyword: formData.keyword || undefined,
    status: (formData.status as Document['processingStatus']) || undefined
  })
  const uploadMutation = useUploadDocumentFile()
  const createMutation = useCreateDocument()

  /** 切换知识库 */
  function handleKbChange(code: string) {
    setFormData((f) => ({ ...f, knowledgeBaseCode: code }))
  }

  /** 文件上传提交 */
  async function handleUploadFile(payload: Parameters<typeof uploadMutation.mutateAsync>[0]['payload']) {
    if (!formData.knowledgeBaseCode) return
    await uploadMutation.mutateAsync({ knowledgeBaseCode: formData.knowledgeBaseCode, payload })
  }

  /** 文本/URL 创建提交 */
  async function handleCreateDocument(payload: Parameters<typeof createMutation.mutateAsync>[0]['payload']) {
    if (!formData.knowledgeBaseCode) return
    await createMutation.mutateAsync({ knowledgeBaseCode: formData.knowledgeBaseCode, payload })
  }

  /** 重新处理文档（暂无独立接口，提示） */
  function handleReprocess(doc: Document) {
    // 后端暂未提供重新处理接口，给出友好提示
    message.info(`文档 ${doc.documentId} 重新处理接口待后端提供`)
  }

  /** 停用/启用文档（暂无独立接口，提示） */
  function handleToggleEnabled(doc: Document) {
    message.info(`文档 ${doc.documentId} ${doc.enabled ? '停用' : '启用'} 接口待后端提供`)
  }

  /** 删除文档（暂无独立接口，提示） */
  function handleDelete(doc: Document) {
    message.info(`文档 ${doc.documentId} 删除接口待后端提供`)
  }

  // 表格列定义
  const columns: ColumnsType<Document> = [
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    {
      title: '来源类型',
      dataIndex: 'sourceType',
      key: 'sourceType',
      width: 110,
      render: (v: Document['sourceType']) => <Tag color="blue">{v}</Tag>
    },
    {
      title: '处理状态',
      dataIndex: 'processingStatus',
      key: 'processingStatus',
      width: 120,
      render: (status: Document['processingStatus']) => <DocumentStatusTag status={status} />
    },
    {
      title: '片段数',
      dataIndex: 'chunkCount',
      key: 'chunkCount',
      width: 90,
      render: (v?: number) => v ?? 0
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 80,
      render: (v: boolean) => (v ? <Tag color="green">启用</Tag> : <Tag color="default">停用</Tag>)
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 170,
      render: (v: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-')
    },
    {
      title: '操作',
      key: 'action',
      width: 260,
      render: (_v, record) => (
        <Space>
          <Button
            type="link"
            size="small"
            onClick={() => setPreviewDoc(record)}
            disabled={isProcessingStatus(record.processingStatus)}
          >
            查看片段
          </Button>
          <Button type="link" size="small" onClick={() => handleReprocess(record)}>
            重新处理
          </Button>
          <Popconfirm
            title={record.enabled ? '确认停用该文档？' : '确认启用该文档？'}
            onConfirm={() => handleToggleEnabled(record)}
          >
            <Button type="link" size="small" danger={record.enabled}>
              {record.enabled ? '停用' : '启用'}
            </Button>
          </Popconfirm>
          <Popconfirm title="确认删除该文档？" onConfirm={() => handleDelete(record)}>
            <Button type="link" size="small" danger>
              删除
            </Button>
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
          <span className="text-xl font-semibold text-gray-800">文档管理</span>
          {currentProject && (
            <span className="text-sm text-gray-400">
              当前项目：{currentProject.name}（{currentProject.code}）
            </span>
          )}
        </div>
        <Button
          type="primary"
          disabled={!formData.knowledgeBaseCode}
          onClick={() => setUploadOpen(true)}
        >
          导入文档
        </Button>
      </div>

      {/* 查询条件区 */}
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Select
          placeholder="选择知识库"
          value={formData.knowledgeBaseCode || undefined}
          onChange={handleKbChange}
          style={{ width: 240 }}
          options={knowledgeBases.map((kb) => ({ label: kb.name, value: kb.code }))}
          optionFilterProp="label"
          showSearch
        />
        <Select
          placeholder="状态筛选"
          allowClear
          value={formData.status || undefined}
          onChange={(v) => setFormData((f) => ({ ...f, status: v || '' }))}
          style={{ width: 160 }}
          options={DOCUMENT_STATUS_OPTIONS}
        />
        <Input
          placeholder="按标题搜索"
          allowClear
          value={formData.keyword}
          onChange={(e) => setFormData((f) => ({ ...f, keyword: e.target.value }))}
          style={{ width: 220 }}
        />
      </div>

      {/* 提示：未选知识库 */}
      {!formData.knowledgeBaseCode && (
        <div className="mb-4 rounded border border-yellow-300 bg-yellow-50 p-3 text-sm text-yellow-700">
          请先选择知识库后查看文档列表。
        </div>
      )}

      {/* 文档表格 */}
      <Table
        rowKey="documentId"
        loading={isLoading}
        columns={columns}
        dataSource={documents}
        pagination={{ pageSize: 10, showSizeChanger: true }}
      />

      {/* 导入弹窗 */}
      <DocumentUploadModal
        open={uploadOpen}
        knowledgeBaseCode={formData.knowledgeBaseCode}
        onCancel={() => setUploadOpen(false)}
        onUploadFile={handleUploadFile}
        onCreateDocument={handleCreateDocument}
      />

      {/* 片段预览 Drawer */}
      <DocumentChunkPreview
        open={!!previewDoc}
        document={previewDoc}
        onClose={() => setPreviewDoc(null)}
        // 后端暂未提供独立 chunks 接口，此处留空
        chunks={[]}
      />
    </div>
  )
}
