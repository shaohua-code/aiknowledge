import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App,
  Alert,
  Button,
  Dropdown,
  Form,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tabs,
  Upload
} from 'antd'
import type { MenuProps } from 'antd'
import { EmptyState, ErrorState, LoadingState, PageHeader, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { knowledgeApi } from '@/api/platform'
import { useApplicationContext } from '@/hooks/useApplicationContext'

function MutationErrorAlert({ error }: { error: unknown }) {
  if (!error) return null
  const platformError = asPlatformError(error)
  return (
    <Alert
      className="mutation-error-alert"
      type="error"
      showIcon
      message={platformError.title}
      description={
        <div className="mutation-error-detail">
          <span>{platformError.message}</span>
          {platformError.suggestion && <span>{platformError.suggestion}</span>}
          {platformError.requestId && <code>请求 ID：{platformError.requestId}</code>}
        </div>
      }
    />
  )
}

export default function KnowledgePage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const { applicationId, environmentId } = useApplicationContext()
  const [collectionOpen, setCollectionOpen] = useState(false)
  const [documentOpen, setDocumentOpen] = useState(false)
  const [textOpen, setTextOpen] = useState(false)
  const [remoteOpen, setRemoteOpen] = useState(false)
  const [activeTab, setActiveTab] = useState('collections')
  const [selectedCollectionId, setSelectedCollectionId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [collectionForm] = Form.useForm()
  const [textForm] = Form.useForm()
  const [remoteForm] = Form.useForm()

  const collections = useQuery({
    queryKey: ['collections', applicationId, environmentId],
    queryFn: () => knowledgeApi.collections(applicationId, environmentId)
  })
  useEffect(() => {
    if (!selectedCollectionId && collections.data?.[0]) {
      setSelectedCollectionId(collections.data[0].id)
    }
  }, [collections.data, selectedCollectionId])
  const documents = useQuery({
    queryKey: ['documents', applicationId, environmentId, selectedCollectionId],
    queryFn: () => knowledgeApi.documents(applicationId, environmentId, selectedCollectionId),
    enabled: Boolean(selectedCollectionId)
  })
  const runs = useQuery({
    queryKey: ['ingestion-runs', applicationId, environmentId],
    queryFn: () => knowledgeApi.runs(applicationId, environmentId),
    refetchInterval: (query) =>
      query.state.data?.some((item) => ['queued', 'running'].includes(item.status)) ? 3000 : false
  })

  const refreshKnowledge = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['collections', applicationId, environmentId] }),
      queryClient.invalidateQueries({ queryKey: ['documents', applicationId, environmentId] }),
      queryClient.invalidateQueries({ queryKey: ['ingestion-runs', applicationId, environmentId] })
    ])
  }
  const createCollection = useMutation({
    mutationFn: (values: { code: string; name: string; description?: string }) =>
      knowledgeApi.createCollection(applicationId, environmentId, values),
    onSuccess: async (row) => {
      await refreshKnowledge()
      setSelectedCollectionId(row.id)
      setActiveTab('collections')
      setCollectionOpen(false)
      collectionForm.resetFields()
      message.success('知识集合已创建')
    }
  })
  const upload = useMutation({
    mutationFn: () => {
      if (!file || !selectedCollectionId) throw new Error('请选择文件和知识集合')
      return knowledgeApi.upload(applicationId, environmentId, selectedCollectionId, file)
    },
    onSuccess: async () => {
      await refreshKnowledge()
      setDocumentOpen(false)
      setFile(null)
      setActiveTab('runs')
      message.success('文档已提交，正在后台入库')
    }
  })
  const createText = useMutation({
    mutationFn: (values: { title: string; content: string }) =>
      knowledgeApi.createText(applicationId, environmentId, selectedCollectionId, values),
    onSuccess: async () => {
      await refreshKnowledge()
      setTextOpen(false)
      textForm.resetFields()
      setActiveTab('runs')
      message.success('文本已提交，正在后台入库')
    }
  })
  const createRemote = useMutation({
    mutationFn: (values: { title: string; url: string; sourceType: 'web' | 'api' }) =>
      knowledgeApi.createRemote(
        applicationId,
        environmentId,
        selectedCollectionId,
        values
      ),
    onSuccess: async () => {
      await refreshKnowledge()
      setRemoteOpen(false)
      remoteForm.resetFields()
      setActiveTab('runs')
      message.success('远程资料已提交，正在后台抓取')
    }
  })
  const retryRun = useMutation({
    mutationFn: (runId: string) => knowledgeApi.retryRun(applicationId, environmentId, runId),
    onSuccess: async () => {
      await refreshKnowledge()
      setActiveTab('runs')
      message.success('入库任务已重新提交')
    }
  })
  const archiveDocument = useMutation({
    mutationFn: (documentId: string) =>
      knowledgeApi.archiveDocument(applicationId, environmentId, documentId),
    onSuccess: async () => {
      await refreshKnowledge()
      message.success('文档已归档，不再参与检索')
    }
  })

  if (collections.isLoading) return <LoadingState rows={8} />
  if (collections.isError) {
    const error = asPlatformError(collections.error)
    return (
      <ErrorState
        message={error.message}
        requestId={error.requestId}
        suggestion={error.suggestion ?? undefined}
        onRetry={() => collections.refetch()}
      />
    )
  }
  const rows = collections.data ?? []
  const selectedCollection = rows.find((item) => item.id === selectedCollectionId)
  const sourceMenu: MenuProps = {
    items: [
      { key: 'upload', label: '上传 PDF / DOCX / 文本文件' },
      { key: 'text', label: '直接录入文本' },
      { type: 'divider' },
      { key: 'web', label: '抓取公开网页' },
      { key: 'api', label: '接入公开 JSON API' }
    ],
    onClick: ({ key }) => {
      if (key === 'upload') {
        upload.reset()
        setDocumentOpen(true)
        return
      }
      if (key === 'text') {
        createText.reset()
        setTextOpen(true)
        return
      }
      createRemote.reset()
      remoteForm.setFieldValue('sourceType', key)
      setRemoteOpen(true)
    }
  }

  return (
    <div className="workspace-page">
      <PageHeader
        eyebrow="知识中心"
        title="把原始资料变成真正可用的知识"
        description="上传成功只是开始；只有完成解析、切割、Embedding 和发布后，知识才会参与回答。"
        actions={
          <Button onClick={() => setCollectionOpen(true)}>新建知识集合</Button>
        }
      />

      {rows.length === 0 ? (
        <EmptyState
          title="还没有知识集合"
          description="建议 AI 简历先创建“简历规范”和“岗位能力模型”两个独立集合。"
          action={<Button onClick={() => setCollectionOpen(true)}>创建知识集合</Button>}
        />
      ) : (
        <>
          <div className="knowledge-context-bar">
            <div className="knowledge-context-copy">
              <span>当前知识集合</span>
              <strong>{selectedCollection?.name ?? '请选择集合'}</strong>
              <small>{selectedCollection?.description || '新增内容会进入当前集合，并自动创建入库任务。'}</small>
            </div>
            <Space wrap>
              <Select
                aria-label="选择当前知识集合"
                value={selectedCollectionId}
                onChange={setSelectedCollectionId}
                options={rows.map((item) => ({ label: item.name, value: item.id }))}
                className="knowledge-collection-select"
              />
              <Dropdown menu={sourceMenu} trigger={['click']}>
                <Button type="primary" disabled={!selectedCollectionId}>添加知识 ▾</Button>
              </Dropdown>
            </Space>
          </div>
          <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'collections',
              label: `知识集合 ${rows.length}`,
              children: (
                <div className="collection-grid">
                  {rows.map((item) => (
                    <button
                      type="button"
                      key={item.id}
                      aria-pressed={selectedCollectionId === item.id}
                      className={`collection-card ${selectedCollectionId === item.id ? 'selected' : ''}`}
                      onClick={() => setSelectedCollectionId(item.id)}
                    >
                      <span className="collection-icon">K</span>
                      <span>
                        <strong>{item.name}</strong>
                        <small>{item.description || item.code}</small>
                      </span>
                      <span className="collection-counts">
                        {item.documentCount} 文档 · {item.chunkCount} 片段
                      </span>
                      <StatusPill status={item.status} />
                    </button>
                  ))}
                </div>
              )
            },
            {
              key: 'documents',
              label: `文档与版本 ${documents.data?.length ?? 0}`,
              children: documents.isLoading ? (
                <LoadingState />
              ) : documents.isError ? (
                <ErrorState
                  message={asPlatformError(documents.error).message}
                  requestId={asPlatformError(documents.error).requestId}
                  onRetry={() => documents.refetch()}
                />
              ) : (
                <Table
                  rowKey="id"
                  dataSource={documents.data ?? []}
                  scroll={{ x: 760 }}
                  locale={{ emptyText: '该知识集合还没有文档' }}
                  columns={[
                    { title: '文档', dataIndex: 'title' },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      render: (status: string) => <StatusPill status={status} />
                    },
                    { title: '当前版本', dataIndex: 'currentVersion', render: (value) => value ?? '-' },
                    { title: '类型', dataIndex: 'mimeType', render: (value) => value || '-' },
                    { title: '更新时间', dataIndex: 'updatedAt', render: (value) => new Date(value).toLocaleString() },
                    {
                      title: '操作',
                      render: (_, row) => (
                        <Popconfirm
                          title="归档这个文档？"
                          description="归档后该文档不会再参与检索，审计记录会保留。"
                          onConfirm={() => archiveDocument.mutate(row.id)}
                        >
                          <Button
                            danger
                            type="link"
                            loading={archiveDocument.isPending && archiveDocument.variables === row.id}
                          >
                            归档
                          </Button>
                        </Popconfirm>
                      )
                    }
                  ]}
                />
              )
            },
            {
              key: 'runs',
              label: `入库运行 ${runs.data?.length ?? 0}`,
              children: runs.isLoading ? (
                <LoadingState />
              ) : runs.isError ? (
                <ErrorState message={asPlatformError(runs.error).message} onRetry={() => runs.refetch()} />
              ) : (
                <Table
                  rowKey="id"
                  dataSource={runs.data ?? []}
                  scroll={{ x: 980 }}
                  columns={[
                    {
                      title: '状态',
                      dataIndex: 'status',
                      render: (status: string) => <StatusPill status={status} />
                    },
                    { title: '阶段', dataIndex: 'stage' },
                    {
                      title: '进度',
                      dataIndex: 'progress',
                      render: (value: number) => <Progress percent={value} size="small" />
                    },
                    { title: '错误码', dataIndex: 'errorCode', render: (value) => value || '-' },
                    { title: '错误说明', dataIndex: 'errorMessage', ellipsis: true, render: (value) => value || '-' },
                    { title: '请求 ID', dataIndex: 'requestId', render: (value) => <code>{value}</code> },
                    {
                      title: '操作',
                      render: (_, row) =>
                        ['failed', 'queued'].includes(row.status) ? (
                          <Button
                            type="link"
                            loading={retryRun.isPending && retryRun.variables === row.id}
                            onClick={() => retryRun.mutate(row.id)}
                          >
                            重试
                          </Button>
                        ) : '-'
                    },
                    { title: '创建时间', dataIndex: 'createdAt', render: (value) => new Date(value).toLocaleString() }
                  ]}
                />
              )
            }
          ]}
          />
        </>
      )}

      <Modal
        title="新建知识集合"
        open={collectionOpen}
        onCancel={() => {
          setCollectionOpen(false)
          createCollection.reset()
          collectionForm.resetFields()
        }}
        onOk={() => collectionForm.submit()}
        confirmLoading={createCollection.isPending}
        okText="创建集合"
      >
        <MutationErrorAlert error={createCollection.error} />
        <Form form={collectionForm} layout="vertical" onFinish={(values) => createCollection.mutate(values)}>
          <Form.Item label="集合名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="简历写作规范" />
          </Form.Item>
          <Form.Item
            label="集合编码"
            name="code"
            rules={[{ required: true }, { pattern: /^[a-z][a-z0-9-]+$/ }]}
          >
            <Input placeholder="resume-guidelines" />
          </Form.Item>
          <Form.Item label="说明" name="description">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="上传知识文档"
        open={documentOpen}
        onCancel={() => {
          setDocumentOpen(false)
          setFile(null)
          upload.reset()
        }}
        onOk={() => upload.mutate()}
        confirmLoading={upload.isPending}
        okButtonProps={{ disabled: !file }}
        okText="提交入库"
        width={620}
      >
        <MutationErrorAlert error={upload.error} />
        <span className="modal-field-label">目标知识集合</span>
        <Select
          value={selectedCollectionId}
          onChange={setSelectedCollectionId}
          options={rows.map((item) => ({ label: item.name, value: item.id }))}
          className="full-width"
        />
        <Upload.Dragger
          className="upload-dragger"
          maxCount={1}
          beforeUpload={(nextFile) => {
            setFile(nextFile)
            return false
          }}
          onRemove={() => setFile(null)}
          accept=".pdf,.docx,.txt,.md"
        >
          <p className="upload-glyph">⇧</p>
          <p>拖入 PDF、DOCX、TXT 或 Markdown</p>
          <p className="muted">单个文件不超过 20MB</p>
        </Upload.Dragger>
      </Modal>

      <Modal
        title="录入文本知识"
        open={textOpen}
        onCancel={() => {
          setTextOpen(false)
          createText.reset()
          textForm.resetFields()
        }}
        onOk={() => textForm.submit()}
        confirmLoading={createText.isPending}
        okText="提交入库"
        width={680}
      >
        <MutationErrorAlert error={createText.error} />
        <Form form={textForm} layout="vertical" onFinish={(values) => createText.mutate(values)}>
          <Form.Item label="标题" name="title" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="正文" name="content" rules={[{ required: true }]}>
            <Input.TextArea rows={10} showCount maxLength={2_000_000} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="接入公开网页或 JSON API"
        open={remoteOpen}
        onCancel={() => {
          setRemoteOpen(false)
          createRemote.reset()
          remoteForm.resetFields()
        }}
        onOk={() => remoteForm.submit()}
        confirmLoading={createRemote.isPending}
        okText="开始抓取"
        width={620}
      >
        <Alert
          type="info"
          showIcon
          message="仅允许公开 HTTP(S) 地址"
          description="内网、回环、带账号密码和重定向地址会被安全策略拒绝。"
        />
        <MutationErrorAlert error={createRemote.error} />
        <Form
          form={remoteForm}
          layout="vertical"
          initialValues={{ sourceType: 'web' }}
          onFinish={(values) => createRemote.mutate(values)}
        >
          <Form.Item label="资料标题" name="title" rules={[{ required: true }]}>
            <Input placeholder="公司招聘规范" />
          </Form.Item>
          <Form.Item label="类型" name="sourceType" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'web', label: '公开网页' },
                { value: 'api', label: '公开 JSON API' }
              ]}
            />
          </Form.Item>
          <Form.Item
            label="最终 URL"
            name="url"
            rules={[{ required: true }, { type: 'url' }]}
          >
            <Input placeholder="https://example.com/knowledge" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
