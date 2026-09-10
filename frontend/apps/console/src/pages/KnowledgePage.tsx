import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App,
  Alert,
  Button,
  Collapse,
  Descriptions,
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
import type { IngestionRun, RemoteSourcePreview } from '@aik/contracts'
import { EmptyState, ErrorState, LoadingState, PageHeader, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { knowledgeApi, type RemoteSourcePayload } from '@/api/platform'
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

interface RemoteSourceForm {
  title: string
  url: string
  sourceType: RemoteSourcePayload['sourceType']
  method: RemoteSourcePayload['method']
  headersText?: string
  queryText?: string
  jsonBodyText?: string
  jsonPath?: string
}

const ingestionStageNames: Record<string, string> = {
  received: '等待处理',
  fetching: '抓取远程数据',
  parsing: '解析内容',
  chunking: '切割知识片段',
  embedding: '生成向量',
  indexing: '写入知识索引',
  published: '发布完成'
}

function parseKeyValueLines(value: string | undefined, separator: ':' | '=') {
  const result: Record<string, string> = {}
  for (const [index, rawLine] of (value ?? '').split(/\r?\n/).entries()) {
    const line = rawLine.trim()
    if (!line) continue
    const position = line.indexOf(separator)
    if (position <= 0) {
      throw new Error(`第 ${index + 1} 行缺少 ${separator}，请按“名称${separator}内容”填写`)
    }
    const key = line.slice(0, position).trim()
    const item = line.slice(position + 1).trim()
    if (!key || !item) throw new Error(`第 ${index + 1} 行的名称或内容为空`)
    result[key] = item
  }
  return result
}

function toRemotePayload(values: RemoteSourceForm): RemoteSourcePayload {
  let jsonBody: unknown
  if (values.jsonBodyText?.trim()) jsonBody = JSON.parse(values.jsonBodyText)
  return {
    title: values.title,
    url: values.url,
    sourceType: values.sourceType,
    method: values.method,
    headers: parseKeyValueLines(values.headersText, ':'),
    queryParams: parseKeyValueLines(values.queryText, '='),
    ...(jsonBody === undefined ? {} : { jsonBody }),
    ...(values.jsonPath?.trim() ? { jsonPath: values.jsonPath.trim() } : {})
  }
}

export default function KnowledgePage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const { applicationId, environmentId } = useApplicationContext()
  const [collectionOpen, setCollectionOpen] = useState(false)
  const [documentOpen, setDocumentOpen] = useState(false)
  const [textOpen, setTextOpen] = useState(false)
  const [remoteOpen, setRemoteOpen] = useState(false)
  const [runError, setRunError] = useState<IngestionRun | null>(null)
  const [activeTab, setActiveTab] = useState('collections')
  // 顶部使用引导是否展开（可关闭）
  const [guideOpen, setGuideOpen] = useState(true)
  const [selectedCollectionId, setSelectedCollectionId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [collectionForm] = Form.useForm()
  const [textForm] = Form.useForm()
  const [remoteForm] = Form.useForm()
  const remoteMethod = Form.useWatch('method', remoteForm)

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
    mutationFn: (values: RemoteSourceForm) =>
      knowledgeApi.createRemote(
        applicationId,
        environmentId,
        selectedCollectionId,
        toRemotePayload(values)
      ),
    onSuccess: async () => {
      await refreshKnowledge()
      setRemoteOpen(false)
      remoteForm.resetFields()
      setActiveTab('runs')
      message.success('远程资料已提交，正在后台抓取')
    }
  })
  const previewRemote = useMutation<RemoteSourcePreview, unknown, RemoteSourceForm>({
    mutationFn: (values) =>
      knowledgeApi.previewRemote(
        applicationId,
        environmentId,
        selectedCollectionId,
        toRemotePayload(values)
      )
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
  const refreshRemote = useMutation({
    mutationFn: (documentId: string) =>
      knowledgeApi.refreshRemote(applicationId, environmentId, documentId),
    onSuccess: async () => {
      await refreshKnowledge()
      setActiveTab('runs')
      message.success('已创建新版本，正在重新抓取远程数据')
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
      previewRemote.reset()
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

      {/* 顶部使用引导：四步流程，可关闭，默认展开 */}
      {guideOpen && (
        <Alert
          className="knowledge-guide-alert"
          type="info"
          showIcon
          closable
          onClose={() => setGuideOpen(false)}
          message="四步让知识真正参与回答"
          description={
            <div>
              <p>① 创建或选择知识集合：点击上方「新建知识集合」，或用下拉框切换到已有集合。</p>
              <p>② 点击「添加知识」上传文件、录入文本或抓取互联网资料。</p>
              <p>③ 提交后自动跳到「入库运行」查看进度，状态变为「ready / 发布完成」后知识才会参与回答。</p>
              <p>④ 到左侧「AI 能力」配置策略并实测回答效果。</p>
            </div>
          }
        />
      )}

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
                <>
                  {/* Tab 说明：集合的定位与卡片交互 */}
                  <Alert
                    className="knowledge-guide-alert"
                    type="info"
                    showIcon
                    message="知识集合是知识的分组边界，建议按主题拆分（如「简历规范」「岗位能力模型」）。卡片显示文档数与片段数，点击卡片即可切换当前集合。"
                  />
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
                </>
              )
            },
            {
              key: 'documents',
              label: `文档与版本 ${documents.data?.length ?? 0}`,
              children: (
                <>
                  {/* Tab 说明：文档状态与版本行为 */}
                  <Alert
                    className="knowledge-guide-alert"
                    type="info"
                    showIcon
                    message="只有状态为 ready 的文档才参与检索。「重新抓取」会为远程文档创建新版本并重新入库（旧版本在新版本发布前仍可检索）；「归档」后文档不再参与检索，审计记录保留。"
                  />
                  {documents.isLoading ? (
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
                        <Space size={2}>
                          {row.sourceUrl && (
                            <Button
                              type="link"
                              loading={refreshRemote.isPending && refreshRemote.variables === row.id}
                              onClick={() => refreshRemote.mutate(row.id)}
                            >
                              重新抓取
                            </Button>
                          )}
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
                        </Space>
                      )
                    }
                  ]}
                />
                  )}
                </>
              )
            },
            {
              key: 'runs',
              label: `入库运行 ${runs.data?.length ?? 0}`,
              children: (
                <>
                  {/* Tab 说明：入库流水线与失败处理 */}
                  <Alert
                    className="knowledge-guide-alert"
                    type="info"
                    showIcon
                    message="入库是异步流水线：抓取 → 解析 → 切割 → 生成向量 → 写入索引 → 发布。失败时点「查看失败原因」按错误码处理，修复后点「重试」；处理中的任务每 3 秒自动刷新。"
                  />
                  {runs.isLoading ? (
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
                    {
                      title: '当前步骤',
                      dataIndex: 'stage',
                      render: (value: string) => ingestionStageNames[value] ?? value
                    },
                    {
                      title: '进度',
                      dataIndex: 'progress',
                      render: (value: number) => <Progress percent={value} size="small" />
                    },
                    {
                      title: '运行结果',
                      render: (_, row) =>
                        row.status === 'failed' ? (
                          <Button danger type="link" onClick={() => setRunError(row)}>
                            查看失败原因
                          </Button>
                        ) : row.status === 'succeeded' ? '知识已发布' : '处理中'
                    },
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
                  )}
                </>
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
        {/* 上传说明：适用文件类型、大小限制与后续去向 */}
        <Alert
          className="knowledge-guide-alert"
          type="info"
          showIcon
          message="适合 PDF 报告、Word 规范、TXT / Markdown 笔记；单个文件不超过 20MB。提交后会自动跳到「入库运行」查看处理进度。"
        />
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
        {/* 录入说明：适用内容与后续去向 */}
        <Alert
          className="knowledge-guide-alert"
          type="info"
          showIcon
          message="适合直接粘贴规章制度、FAQ、产品说明等纯文本内容。提交后会自动跳到「入库运行」查看处理进度。"
        />
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
        title="接入互联网数据"
        open={remoteOpen}
        onCancel={() => {
          setRemoteOpen(false)
          createRemote.reset()
          previewRemote.reset()
          remoteForm.resetFields()
        }}
        onOk={() => remoteForm.submit()}
        confirmLoading={createRemote.isPending}
        okText="开始抓取"
        width={760}
      >
        <div className="remote-source-capabilities">
          <strong>可直接处理</strong>
          <span>普通网页</span><span>JSON / NDJSON API</span><span>RSS / Atom / XML</span>
          <span>CSV / TSV</span><span>纯文本 / Markdown</span><span>常见中文编码</span>
        </div>
        {/* 接入说明：地址要求、建议先测试与后续去向 */}
        <Alert
          className="knowledge-guide-alert"
          type="info"
          showIcon
          message="仅支持公网可访问、无需登录的地址。建议先点「测试连接并预览」确认抓到正文再提交；提交后会自动跳到「入库运行」查看抓取进度。"
        />
        <MutationErrorAlert error={createRemote.error} />
        <MutationErrorAlert error={previewRemote.error} />
        <Form
          form={remoteForm}
          layout="vertical"
          initialValues={{ sourceType: 'auto', method: 'GET' }}
          onValuesChange={() => previewRemote.reset()}
          onFinish={(values) => createRemote.mutate(values)}
        >
          <div className="remote-source-form-grid">
            <Form.Item label="资料标题" name="title" rules={[{ required: true, message: '请填写这份资料在知识库中的名称' }]}>
              <Input placeholder="例如：公司招聘规范" />
            </Form.Item>
            <Form.Item label="内容类型" name="sourceType" rules={[{ required: true }]}>
              <Select
                options={[
                  { value: 'auto', label: '自动识别（推荐）' },
                  { value: 'web', label: '网页 HTML' },
                  { value: 'api', label: 'JSON API' },
                  { value: 'feed', label: 'RSS / XML 订阅' },
                  { value: 'text', label: 'CSV / Markdown / 纯文本' }
                ]}
              />
            </Form.Item>
          </div>
          <Form.Item
            label="公开数据地址"
            name="url"
            extra="支持安全重定向；默认拒绝内网、localhost、URL 账号密码和云元数据地址。本地联调可在非生产环境开启 REMOTE_ALLOW_PRIVATE_ADDRESSES。"
            rules={[
              { required: true, message: '请输入数据地址' },
              { type: 'url', message: '请输入以 http:// 或 https:// 开头的完整地址' }
            ]}
          >
            <Input placeholder="https://example.com/knowledge 或 https://api.example.com/data" />
          </Form.Item>
          <Collapse
            className="remote-advanced-options"
            ghost
            items={[
              {
                key: 'advanced',
                label: '高级请求与内容提取（API 接入时使用）',
                children: (
                  <>
                    <div className="remote-source-form-grid">
                      <Form.Item label="请求方式" name="method">
                        <Select options={[{ value: 'GET', label: 'GET' }, { value: 'POST', label: 'POST JSON' }]} />
                      </Form.Item>
                      <Form.Item
                        label="JSON 数据路径"
                        name="jsonPath"
                        extra="只提取响应中的某个字段，例如 data.items 或 results[0]。"
                      >
                        <Input placeholder="data.items" />
                      </Form.Item>
                    </div>
                    <div className="remote-source-form-grid">
                      <Form.Item
                        label="查询参数"
                        name="queryText"
                        extra="每行一个，格式：名称=内容"
                        rules={[{
                          validator: (_, value) => {
                            try { parseKeyValueLines(value, '='); return Promise.resolve() }
                            catch (error) { return Promise.reject(error) }
                          }
                        }]}
                      >
                        <Input.TextArea rows={4} placeholder={'page=1\nlimit=100'} />
                      </Form.Item>
                      <Form.Item
                        label="自定义请求头"
                        name="headersText"
                        extra="每行一个。出于安全原因，不允许 Authorization、Cookie 和 API Key。"
                        rules={[{
                          validator: (_, value) => {
                            try { parseKeyValueLines(value, ':'); return Promise.resolve() }
                            catch (error) { return Promise.reject(error) }
                          }
                        }]}
                      >
                        <Input.TextArea rows={4} placeholder={'Accept-Language: zh-CN\nX-Api-Version: 2026-01'} />
                      </Form.Item>
                    </div>
                    {remoteMethod === 'POST' && (
                      <Form.Item
                        label="POST JSON 请求体"
                        name="jsonBodyText"
                        rules={[{
                          validator: (_, value) => {
                            if (!value?.trim()) return Promise.resolve()
                            try { JSON.parse(value); return Promise.resolve() }
                            catch { return Promise.reject(new Error('JSON 请求体格式错误，请检查引号、逗号和括号')) }
                          }
                        }]}
                      >
                        <Input.TextArea rows={6} placeholder={'{\n  "page": 1,\n  "pageSize": 100\n}'} />
                      </Form.Item>
                    )}
                  </>
                )
              }
            ]}
          />
          <div className="remote-test-action">
            <Button
              loading={previewRemote.isPending}
              onClick={() => remoteForm.validateFields().then((values) => previewRemote.mutate(values))}
            >
              测试连接并预览
            </Button>
            <span>建议先测试，确认抓到的不是登录页或空壳网页。</span>
          </div>
        </Form>
        {previewRemote.data && (
          <Alert
            className="remote-preview-result"
            type="success"
            showIcon
            message={`连接成功 · HTTP ${previewRemote.data.statusCode} · ${(previewRemote.data.sizeBytes / 1024).toFixed(1)}KB`}
            description={
              <div>
                <p>{previewRemote.data.detectedTitle || previewRemote.data.contentType}</p>
                <pre>{previewRemote.data.excerpt || '已连接，但没有可展示的预览文本。'}</pre>
              </div>
            }
          />
        )}
      </Modal>

      <Modal
        title="入库失败详情"
        open={Boolean(runError)}
        onCancel={() => setRunError(null)}
        footer={
          runError && ['failed', 'queued'].includes(runError.status) ? (
            <Button
              type="primary"
              loading={retryRun.isPending}
              onClick={() => {
                retryRun.mutate(runError.id)
                setRunError(null)
              }}
            >
              修复问题后重试
            </Button>
          ) : null
        }
        width={650}
      >
        {runError && (
          <div className="run-error-detail">
            <Alert
              type="error"
              showIcon
              message={runError.errorCode || '入库任务失败'}
              description={runError.errorMessage || '没有记录到详细错误，请使用请求 ID 查询 Worker 日志。'}
            />
            {/* 处理指引：按错误码分类给出修复路径 */}
            <Alert
              type="warning"
              showIcon
              message="如何按错误码处理"
              description={
                <div>
                  <p>内容类错误（INGESTION_* / REMOTE_*）：检查文件内容或数据地址是否正常后重试。</p>
                  <p>EMBEDDING_DIMENSION_MISMATCH / PROVIDER_UNAVAILABLE：先到「设置 → 模型与联网能力」检查 Embedding 模型配置（输出维度需为 1536），再重试。</p>
                  <p>仍失败：复制请求 ID，到「运行与质量」查询诊断。</p>
                </div>
              }
            />
            <Descriptions
              size="small"
              column={1}
              items={[
                { key: 'stage', label: '失败步骤', children: ingestionStageNames[runError.stage] ?? runError.stage },
                { key: 'retry', label: '已重试次数', children: runError.retryCount },
                { key: 'request', label: '请求 ID', children: <code>{runError.requestId}</code> }
              ]}
            />
          </div>
        )}
      </Modal>
    </div>
  )
}
