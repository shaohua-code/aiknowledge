import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Alert, Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip } from 'antd'
import type { TableProps } from 'antd'
import type {
  ModelCapability,
  ModelBindingUpdate,
  ModelCatalogEntry,
  ModelCatalogEntryCreate,
  ModelCatalogEntryUpdate
} from '@aik/contracts'
import { EmptyState, ErrorState, LoadingState, PageHeader } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { modelCatalogApi, platformModelDefaultApi } from '@/api/platform'

const CAPABILITY_ORDER: ModelCapability[] = ['chat', 'embedding', 'web_search']

const CAPABILITY_TITLES: Record<ModelCapability, string> = {
  chat: '对话模型',
  embedding: '向量模型',
  web_search: '联网搜索'
}

const PROVIDER_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  openai_compatible: 'OpenAI 兼容服务',
  local_hash: '本地测试向量（仅开发）',
  serper: 'Serper'
}

// 各能力可选 Provider 与后端 CAPABILITY_PROVIDERS 保持一致，避免提交后被服务端拒绝
const CAPABILITY_PROVIDERS: Record<ModelCapability, string[]> = {
  chat: ['openai', 'openai_compatible'],
  embedding: ['local_hash', 'openai', 'openai_compatible'],
  web_search: ['serper']
}

// 按 Provider 给出接口地址示例，降低填写成本
const PROVIDER_BASE_URL_PLACEHOLDERS: Record<string, string> = {
  openai: 'https://api.openai.com/v1',
  openai_compatible: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  serper: 'https://google.serper.dev'
}

interface ModelCatalogFormValues {
  code: string
  name: string
  capability: ModelCapability
  provider: string
  model?: string
  base_url: string
  api_key_env: string
  note?: string
}

export default function ModelCatalogPage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<ModelCatalogEntry | null>(null)
  const [capability, setCapability] = useState<ModelCapability>('chat')
  const [provider, setProvider] = useState<string>('openai')
  const [guideOpen, setGuideOpen] = useState(true)
  const [form] = Form.useForm<ModelCatalogFormValues>()
  const catalog = useQuery({
    queryKey: ['model-catalog'],
    queryFn: () => modelCatalogApi.list()
  })
  const platformDefaults = useQuery({
    queryKey: ['platform-model-defaults'],
    queryFn: platformModelDefaultApi.get
  })
  const refreshCatalog = () => queryClient.invalidateQueries({ queryKey: ['model-catalog'] })

  const closeModal = () => {
    setModalOpen(false)
    setEditing(null)
    form.resetFields()
  }
  // 打开创建弹窗，可从分组标题的快捷按钮进入并预选能力
  const openCreate = (target?: ModelCapability) => {
    const nextCapability = target ?? 'chat'
    const nextProvider = CAPABILITY_PROVIDERS[nextCapability][0]
    setEditing(null)
    setCapability(nextCapability)
    setProvider(nextProvider)
    form.resetFields()
    form.setFieldsValue({ capability: nextCapability, provider: nextProvider })
    setModalOpen(true)
  }
  const openEdit = (entry: ModelCatalogEntry) => {
    setEditing(entry)
    setCapability(entry.capability)
    setProvider(entry.provider)
    form.setFieldsValue({
      code: entry.code,
      name: entry.name,
      capability: entry.capability,
      provider: entry.provider,
      model: entry.model,
      base_url: entry.base_url,
      api_key_env: entry.api_key_env,
      note: entry.note ?? undefined
    })
    setModalOpen(true)
  }

  const create = useMutation({
    mutationFn: (payload: ModelCatalogEntryCreate) => modelCatalogApi.create(payload),
    onSuccess: async () => {
      await refreshCatalog()
      closeModal()
      message.success('模型已创建，可到「设置」为应用绑定')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const update = useMutation({
    mutationFn: (input: { code: string; payload: ModelCatalogEntryUpdate }) =>
      modelCatalogApi.update(input.code, input.payload),
    onSuccess: async () => {
      await refreshCatalog()
      closeModal()
      message.success('模型已保存')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const toggleEnabled = useMutation({
    mutationFn: (input: { code: string; enabled: boolean }) =>
      modelCatalogApi.update(input.code, { enabled: input.enabled }),
    onSuccess: async (_data, input) => {
      await refreshCatalog()
      message.success(input.enabled ? '模型已启用' : '模型已停用')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const remove = useMutation({
    mutationFn: (code: string) => modelCatalogApi.remove(code),
    onSuccess: async () => {
      await refreshCatalog()
      message.success('模型已删除')
    },
    // 被应用绑定引用时后端返回中文冲突错误，原样展示
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const test = useMutation({
    mutationFn: (code: string) => modelCatalogApi.test(code),
    onSuccess: (result) => {
      if (result.ok) {
        message.success(`${result.message}，延迟 ${result.latency_ms}ms`)
      } else {
        message.error(result.message)
      }
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const updatePlatformDefaults = useMutation({
    mutationFn: (payload: ModelBindingUpdate) => platformModelDefaultApi.update(payload),
    onSuccess: (data) => {
      queryClient.setQueryData(['platform-model-defaults'], data)
      message.success('平台默认模型已更新')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  // 平台默认只在应用和环境都没有显式绑定时被运行时采用，因此在目录页单独管理。
  const bindPlatformDefault = (capability: ModelCapability, code: string | null) => {
    updatePlatformDefaults.mutate({
      chat: capability === 'chat' ? code : (platformDefaults.data?.chat ?? null),
      embedding: capability === 'embedding' ? code : (platformDefaults.data?.embedding ?? null),
      webSearch: capability === 'web_search' ? code : (platformDefaults.data?.web_search ?? null)
    })
  }

  // 切换能力时把 Provider 重置到该能力允许的首选项
  const handleCapabilityChange = (value: ModelCapability) => {
    const nextProvider = CAPABILITY_PROVIDERS[value][0]
    setCapability(value)
    setProvider(nextProvider)
    form.setFieldValue('provider', nextProvider)
  }
  const handleProviderChange = (value: string) => setProvider(value)
  const handleSubmit = (values: ModelCatalogFormValues) => {
    // 表单保留 snake_case 以承接历史目录数据；提交时转换为生成 Schema 的 camelCase 请求体。
    // note 留空时提交 null，便于编辑时清空备注。
    const payload = {
      name: values.name,
      capability: values.capability,
      provider: values.provider,
      model: values.model ?? '',
      baseUrl: values.base_url,
      apiKeyEnv: values.api_key_env,
      note: values.note?.trim() || null
    }
    if (editing) {
      update.mutate({ code: editing.code, payload })
    } else {
      create.mutate({ ...payload, code: values.code, enabled: true })
    }
  }

  if (catalog.isLoading) return <LoadingState rows={8} />
  if (catalog.isError) {
    const error = asPlatformError(catalog.error)
    return (
      <ErrorState
        message={error.message}
        requestId={error.requestId}
        suggestion={error.suggestion ?? undefined}
        onRetry={() => catalog.refetch()}
      />
    )
  }
  const entries = catalog.data?.items ?? []

  const columns: TableProps<ModelCatalogEntry>['columns'] = [
    {
      title: '名称',
      key: 'name',
      render: (_, entry) => (
        <div className="model-catalog-name-cell">
          <strong>{entry.name}</strong>
          <code className="muted">{entry.code}</code>
          {entry.note ? <small className="muted">{entry.note}</small> : null}
        </div>
      )
    },
    {
      title: 'Provider',
      dataIndex: 'provider',
      key: 'provider',
      width: 150,
      render: (value: string) => <Tag>{PROVIDER_LABELS[value] ?? value}</Tag>
    },
    {
      title: '模型 / 接口地址',
      key: 'model',
      render: (_, entry) => (
        <div className="model-catalog-name-cell">
          <span>{entry.model || <span className="muted">—</span>}</span>
          <small className="muted model-catalog-url" title={entry.base_url}>{entry.base_url}</small>
        </div>
      )
    },
    {
      title: '密钥状态',
      key: 'api_key_configured',
      width: 170,
      render: (_, entry) =>
        entry.api_key_configured ? (
          <Tag color="green">已配置</Tag>
        ) : (
          <Tooltip title={`在 .env 添加 ${entry.api_key_env}=真实密钥，重启容器后变为「已配置」`}>
            <Tag color="red">未配置 {entry.api_key_env}</Tag>
          </Tooltip>
        )
    },
    {
      title: '启用',
      key: 'enabled',
      width: 80,
      render: (_, entry) => (
        <Switch
          checked={entry.enabled}
          loading={toggleEnabled.isPending && toggleEnabled.variables?.code === entry.code}
          onChange={(enabled) => toggleEnabled.mutate({ code: entry.code, enabled })}
        />
      )
    },
    {
      title: '操作',
      key: 'actions',
      width: 220,
      render: (_, entry) => (
        <Space size="small" wrap>
          <Button type="link" size="small" onClick={() => openEdit(entry)}>编辑</Button>
          <Button
            type="link"
            size="small"
            loading={test.isPending && test.variables === entry.code}
            onClick={() => test.mutate(entry.code)}
          >
            测试连接
          </Button>
          <Popconfirm
            title={`确认删除模型「${entry.name}」？`}
            description="被应用绑定的模型无法删除"
            okText="删除"
            cancelText="取消"
            onConfirm={() => remove.mutate(entry.code)}
          >
            <Button type="link" size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      )
    }
  ]

  return (
    <div className="page-container">
      <PageHeader
        eyebrow="模型管理"
        title="集中管理可用模型，密钥只登记环境变量名"
        description="模型 API Key 统一写在 .env 的 MODEL_KEY_<用途名>，这里只填写变量名引用，密钥永不入库。"
        actions={<Button type="primary" onClick={() => openCreate()}>新增模型</Button>}
      />

      {/* 顶部使用引导：四步完成模型接入，可关闭，默认展开 */}
      {guideOpen && (
        <Alert
          className="model-catalog-guide"
          type="info"
          showIcon
          closable
          onClose={() => setGuideOpen(false)}
          message="四步接入一个可用模型"
          description={
            <div>
              <p>① 在 .env「模型密钥注册区」登记 MODEL_KEY_&lt;用途大写名&gt;=真实密钥，改完重启容器生效。</p>
              <p>② 点「新增模型」，密钥变量名只填变量名引用（如 MODEL_KEY_ALIYUN），密钥永不入库。</p>
              <p>③ 点「测试连接」确认地址与密钥可用；向量模型必须支持输出 1536 维。</p>
              <p>④ 到「AI 应用 → 设置 → 模型与联网能力」为应用选择绑定，保存立即生效。</p>
            </div>
          }
        />
      )}

      <section className="section-block model-catalog-section">
        <div className="section-heading compact">
          <div>
            <h2>平台默认模型</h2>
            <p className="muted">仅当应用默认和环境覆盖均未设置时使用。</p>
          </div>
        </div>
        {CAPABILITY_ORDER.map((item) => {
          const code = platformDefaults.data?.[item] ?? null
          const options = entries
            .filter((entry) => entry.capability === item && entry.enabled)
            .map((entry) => ({ value: entry.code, label: `${entry.name} · ${entry.model || entry.provider}` }))
          return (
            <div className="model-binding-row" key={item}>
              <span className="model-binding-label">{CAPABILITY_TITLES[item]}</span>
              <Select
                className="model-binding-select"
                allowClear
                placeholder="不设置数据库平台默认"
                options={options}
                value={code}
                loading={catalog.isLoading || platformDefaults.isLoading}
                disabled={updatePlatformDefaults.isPending}
                onChange={(value: string | undefined) => bindPlatformDefault(item, value ?? null)}
              />
            </div>
          )
        })}
      </section>

      {CAPABILITY_ORDER.map((item) => {
        const rows = entries.filter((entry) => entry.capability === item)
        return (
          <section className="section-block model-catalog-section" key={item}>
            <div className="section-heading compact">
              <div className="model-catalog-section-title">
                <h2>{CAPABILITY_TITLES[item]}</h2>
                <Tag>{rows.length}</Tag>
              </div>
              <Button size="small" onClick={() => openCreate(item)}>新增{CAPABILITY_TITLES[item]}</Button>
            </div>
            {rows.length === 0 ? (
              <EmptyState
                title={`暂无${CAPABILITY_TITLES[item]}`}
                description="登记一个该能力的可用模型，再到「设置」为应用绑定。"
                action={<Button onClick={() => openCreate(item)}>新增{CAPABILITY_TITLES[item]}</Button>}
              />
            ) : (
              <Table rowKey="code" columns={columns} dataSource={rows} pagination={false} />
            )}
          </section>
        )
      })}

      <Modal
        title={editing ? `编辑模型「${editing.name}」` : '新增模型'}
        open={modalOpen}
        onCancel={closeModal}
        onOk={() => form.submit()}
        confirmLoading={create.isPending || update.isPending}
        okText={editing ? '保存' : '创建'}
        cancelText="取消"
        width={620}
      >
        {/* 向量模型维度提示：选 embedding 时展示，避免重蹈维度不匹配 */}
        {capability === 'embedding' && (
          <Alert
            className="model-catalog-modal-alert"
            type="warning"
            showIcon
            message="向量维度固定为 1536"
            description="所选模型必须支持输出 1536 维向量（请求会携带 dimensions=1536），否则入库会失败。"
          />
        )}
        <Form<ModelCatalogFormValues>
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={handleSubmit}
          initialValues={{ capability: 'chat', provider: 'openai' }}
        >
          <Form.Item
            label="模型编码"
            name="code"
            extra="创建后不可修改，只允许小写字母、数字和连字符"
            rules={[
              { required: true, message: '请填写模型编码' },
              { pattern: /^[a-z0-9][a-z0-9-]*$/, message: '只允许小写字母、数字和连字符' },
              { max: 80, message: '编码最长 80 个字符' }
            ]}
          >
            <Input placeholder="例如：aliyun-qwen-plus" disabled={Boolean(editing)} />
          </Form.Item>
          <Form.Item
            label="模型名称"
            name="name"
            rules={[
              { required: true, message: '请填写模型名称' },
              { max: 120, message: '名称最长 120 个字符' }
            ]}
          >
            <Input placeholder="例如：阿里云通义千问 Plus" />
          </Form.Item>
          <Form.Item label="能力类型" name="capability" rules={[{ required: true }]}>
            <Select
              disabled={Boolean(editing)}
              options={CAPABILITY_ORDER.map((value) => ({ value, label: CAPABILITY_TITLES[value] }))}
              onChange={handleCapabilityChange}
            />
          </Form.Item>
          <Form.Item label="Provider" name="provider" rules={[{ required: true, message: '请选择 Provider' }]}>
            <Select
              options={CAPABILITY_PROVIDERS[capability].map((value) => ({
                value,
                label: PROVIDER_LABELS[value] ?? value
              }))}
              onChange={handleProviderChange}
            />
          </Form.Item>
          <Form.Item label="模型" name="model" rules={[{ max: 160, message: '模型名最长 160 个字符' }]}>
            <Input placeholder="例如：qwen-plus / text-embedding-3-small" />
          </Form.Item>
          <Form.Item
            label="接口地址"
            name="base_url"
            rules={[
              { required: true, message: '请填写接口地址' },
              { type: 'url', message: '请输入合法的 URL' }
            ]}
          >
            <Input placeholder={PROVIDER_BASE_URL_PLACEHOLDERS[provider] ?? 'https://'} />
          </Form.Item>
          <Form.Item
            label="密钥变量名"
            name="api_key_env"
            extra="对应 .env 中的变量名，如 MODEL_KEY_ALIYUN；改 .env 后需重启容器生效"
            rules={[
              { required: true, message: '请填写密钥变量名' },
              { pattern: /^[A-Za-z_][A-Za-z0-9_]*$/, message: '只允许字母、数字和下划线，且不能以数字开头' },
              { max: 120, message: '变量名最长 120 个字符' }
            ]}
          >
            <Input placeholder="MODEL_KEY_ALIYUN" />
          </Form.Item>
          <Form.Item label="备注" name="note" rules={[{ max: 500, message: '备注最长 500 个字符' }]}>
            <Input.TextArea rows={2} placeholder="可选，记录用途或计费信息" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
