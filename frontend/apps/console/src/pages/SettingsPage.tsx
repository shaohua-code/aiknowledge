import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Alert, Button, Descriptions, Divider, Form, Input, Select, Space, Spin, Tag } from 'antd'
import type {
  ModelCapability,
  ModelBindingUpdate,
  ModelConfiguration,
  ModelConfigurationUpdate
} from '@aik/contracts'
import { PageHeader, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import {
  applicationApi,
  environmentModelBindingApi,
  modelBindingApi,
  modelCatalogApi,
  modelConfigurationApi
} from '@/api/platform'
import { useApplicationContext } from '@/hooks/useApplicationContext'

const BINDING_CAPABILITIES: Array<{ capability: ModelCapability; label: string }> = [
  { capability: 'chat', label: '对话模型' },
  { capability: 'embedding', label: '向量模型' },
  { capability: 'web_search', label: '联网搜索' }
]

function toModelConfigurationFormValues(
  configuration: ModelConfiguration
): ModelConfigurationUpdate {
  // 读取接口为兼容旧控制台保留 snake_case；写入表单沿用既有 camelCase 请求体。
  // 表单只编辑部署变量名，浏览器绝不接收、缓存或提交实际 API Key。
  return {
    chatProvider: configuration.chat_provider,
    chatModel: configuration.chat_model,
    chatBaseUrl: configuration.chat_base_url,
    chatApiKeyEnv: configuration.chat_api_key_env,
    embeddingProvider: configuration.embedding_provider,
    embeddingModel: configuration.embedding_model,
    embeddingBaseUrl: configuration.embedding_base_url,
    embeddingApiKeyEnv: configuration.embedding_api_key_env,
    webSearchProvider: configuration.web_search_provider,
    webSearchBaseUrl: configuration.web_search_base_url,
    webSearchApiKeyEnv: configuration.web_search_api_key_env
  }
}

export default function SettingsPage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const { applicationId, application, environment } = useApplicationContext()
  const modelConfiguration = useQuery({
    queryKey: ['model-configuration'],
    queryFn: modelConfigurationApi.get
  })
  const modelCatalog = useQuery({
    queryKey: ['model-catalog'],
    queryFn: () => modelCatalogApi.list()
  })
  const modelBinding = useQuery({
    queryKey: ['model-binding', applicationId],
    queryFn: () => modelBindingApi.get(applicationId),
    enabled: Boolean(applicationId)
  })
  const environmentModelBinding = useQuery({
    queryKey: ['environment-model-binding', applicationId, environment?.id],
    queryFn: () => environmentModelBindingApi.get(applicationId, environment!.id),
    enabled: Boolean(applicationId && environment?.id)
  })
  const updateBinding = useMutation({
    mutationFn: (payload: ModelBindingUpdate) => modelBindingApi.update(applicationId, payload),
    onSuccess: (data) => {
      queryClient.setQueryData(['model-binding', applicationId], data)
      message.success('模型绑定已更新')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const updateEnvironmentBinding = useMutation({
    mutationFn: (payload: ModelBindingUpdate) =>
      environmentModelBindingApi.update(applicationId, environment!.id, payload),
    onSuccess: (data) => {
      queryClient.setQueryData(['environment-model-binding', applicationId, environment?.id], data)
      message.success('环境模型覆盖已更新')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  // 绑定接口为全量写：未变更的能力需带上当前值，传 null 表示解除绑定
  const bindCapability = (capability: ModelCapability, code: string | null) => {
    updateBinding.mutate({
      chat: capability === 'chat' ? code : (modelBinding.data?.chat ?? null),
      embedding: capability === 'embedding' ? code : (modelBinding.data?.embedding ?? null),
      webSearch: capability === 'web_search' ? code : (modelBinding.data?.web_search ?? null)
    })
  }
  // 环境覆盖与应用默认分开提交，解除环境项后运行时自动回退到下面的应用默认。
  const bindEnvironmentCapability = (capability: ModelCapability, code: string | null) => {
    updateEnvironmentBinding.mutate({
      chat: capability === 'chat' ? code : (environmentModelBinding.data?.chat ?? null),
      embedding: capability === 'embedding' ? code : (environmentModelBinding.data?.embedding ?? null),
      webSearch: capability === 'web_search' ? code : (environmentModelBinding.data?.web_search ?? null)
    })
  }
  // 模型配置加载失败时全局提示
  useEffect(() => {
    if (modelConfiguration.error) {
      message.error(asPlatformError(modelConfiguration.error).message)
    }
  }, [modelConfiguration.error, message])
  const update = useMutation({
    mutationFn: (values: { name: string; description?: string }) =>
      applicationApi.update(application!.id, values),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['applications'] })
      message.success('应用信息已保存')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const updateModel = useMutation({
    mutationFn: modelConfigurationApi.update,
    onSuccess: (data) => {
      queryClient.setQueryData(['model-configuration'], data)
      message.success('模型配置已保存并生效')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const testModel = useMutation({
    mutationFn: modelConfigurationApi.test,
    onSuccess: () => message.success('对话模型连接成功'),
    onError: (error) => message.error(asPlatformError(error).message)
  })
  if (!application || !environment) return null

  const catalogItems = modelCatalog.data?.items ?? []

  return (
    <div className="workspace-page">
      <PageHeader
        eyebrow="设置"
        title="应用边界与环境信息"
        description="应用编码和环境身份参与隔离，创建后不能通过普通编辑覆盖。"
      />
      <section className="settings-grid">
        <article className="panel-card">
          <h2>基本信息</h2>
          <Form
            layout="vertical"
            initialValues={{ name: application.name, description: application.description }}
            onFinish={(values) => update.mutate(values)}
          >
            <Form.Item label="应用名称" name="name" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item label="说明" name="description">
              <Input.TextArea rows={5} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={update.isPending}>保存</Button>
          </Form>
        </article>
        <article className="panel-card">
          <h2>不可变身份</h2>
          <Descriptions column={1} bordered>
            <Descriptions.Item label="应用编码"><code>{application.code}</code></Descriptions.Item>
            <Descriptions.Item label="应用类型">{application.applicationType}</Descriptions.Item>
            <Descriptions.Item label="应用状态"><StatusPill status={application.status} /></Descriptions.Item>
            <Descriptions.Item label="环境">{environment.name}</Descriptions.Item>
            <Descriptions.Item label="环境编码"><code>{environment.code}</code></Descriptions.Item>
            <Descriptions.Item label="环境状态"><StatusPill status={environment.status} /></Descriptions.Item>
          </Descriptions>
        </article>
        <article className="panel-card settings-model-card">
          <h2>模型与联网能力</h2>
          <p className="section-description">
            配置平台统一使用的对话模型、向量模型和联网搜索。凭证仅由部署环境变量提供，控制台不会接收或保存 API Key。
          </p>
          <div className="model-binding-section">
            <h3>当前环境覆盖（优先级最高）</h3>
            {BINDING_CAPABILITIES.map(({ capability, label }) => {
              const boundCode = environmentModelBinding.data?.[capability] ?? null
              const boundEntry = catalogItems.find((item) => item.code === boundCode)
              const options = catalogItems
                .filter((item) => item.capability === capability && item.enabled)
                .map((item) => ({ value: item.code, label: `${item.name} · ${item.model || item.provider}` }))
              return (
                <div className="model-binding-row" key={`environment-${capability}`}>
                  <span className="model-binding-label">{label}</span>
                  <Select
                    className="model-binding-select"
                    placeholder="不覆盖，使用应用默认或平台默认"
                    options={options}
                    value={boundCode}
                    loading={modelCatalog.isLoading || environmentModelBinding.isLoading}
                    disabled={updateEnvironmentBinding.isPending}
                    notFoundContent="暂无可选条目，请先到模型管理页登记"
                    onChange={(code: string) => bindEnvironmentCapability(capability, code)}
                  />
                  {boundCode && (
                    <>
                      <Tag color={boundEntry ? 'green' : 'orange'}>已覆盖 {boundEntry?.name ?? boundCode}</Tag>
                      <Button
                        type="link"
                        size="small"
                        disabled={updateEnvironmentBinding.isPending}
                        onClick={() => bindEnvironmentCapability(capability, null)}
                      >
                        解除覆盖
                      </Button>
                    </>
                  )}
                </div>
              )
            })}
            <Divider />
            <h3>应用默认（环境未覆盖时生效）</h3>
            {BINDING_CAPABILITIES.map(({ capability, label }) => {
              const boundCode = modelBinding.data?.[capability] ?? null
              const boundEntry = catalogItems.find((item) => item.code === boundCode)
              // 仅展示对应能力且处于启用状态的目录条目
              const options = catalogItems
                .filter((item) => item.capability === capability && item.enabled)
                .map((item) => ({ value: item.code, label: `${item.name} · ${item.model || item.provider}` }))
              return (
                <div className="model-binding-row" key={capability}>
                  <span className="model-binding-label">{label}</span>
                  <Select
                    className="model-binding-select"
                    placeholder="选择模型配置中心的条目"
                    options={options}
                    value={boundCode}
                    loading={modelCatalog.isLoading || modelBinding.isLoading}
                    disabled={updateBinding.isPending}
                    notFoundContent="暂无可选条目，请先到模型管理页登记"
                    onChange={(code: string) => bindCapability(capability, code)}
                  />
                  {boundCode && (
                    <>
                      <Tag color={boundEntry ? 'green' : 'orange'}>已绑定 {boundEntry?.name ?? boundCode}</Tag>
                      <Button
                        type="link"
                        size="small"
                        disabled={updateBinding.isPending}
                        onClick={() => bindCapability(capability, null)}
                      >
                        解除绑定
                      </Button>
                    </>
                  )}
                </div>
              )
            })}
            <p className="muted">应用和环境均未选择时，依次使用模型目录页配置的平台默认与下方兼容配置。</p>
          </div>
          <Divider />
          {modelConfiguration.isLoading && <Spin />}
          {modelConfiguration.data && (
            <Form<ModelConfigurationUpdate>
              layout="vertical"
              initialValues={toModelConfigurationFormValues(modelConfiguration.data)}
              onFinish={(values) => updateModel.mutate(values)}
            >
              {modelConfiguration.data.legacy_inline_credentials_detected && (
                <Alert
                  type="warning"
                  showIcon
                  message="检测到旧配置含内联凭证"
                  description="为避免覆盖或继续写入明文，保存已被阻止。请先在部署环境配置对应密钥，再保存下面的环境变量引用。"
                />
              )}
              <Divider orientation="left">对话模型</Divider>
              <div className="model-form-grid">
                <Form.Item label="Provider" name="chatProvider" rules={[{ required: true }]}>
                  <Select options={[
                    { value: 'disabled', label: '不启用' },
                    { value: 'openai', label: 'OpenAI' },
                    { value: 'openai_compatible', label: 'OpenAI 兼容服务' }
                  ]} />
                </Form.Item>
                <Form.Item label="模型名称" name="chatModel">
                  <Input placeholder="例如：gpt-4.1-mini" />
                </Form.Item>
                <Form.Item label="接口地址" name="chatBaseUrl" rules={[{ required: true, type: 'url' }]}>
                  <Input placeholder="https://api.openai.com/v1" />
                </Form.Item>
                <Form.Item
                  label={<>密钥环境变量 {modelConfiguration.data.chat_api_key_configured && <Tag color="green">已配置</Tag>}</>}
                  name="chatApiKeyEnv"
                  rules={[{ required: true, pattern: /^[A-Z][A-Z0-9_]*$/, message: '请输入大写环境变量名' }]}
                  extra="例如 CHAT_API_KEY；实际 API Key 只能配置在部署环境或密钥管理系统中"
                >
                  <Input autoComplete="off" placeholder="CHAT_API_KEY" />
                </Form.Item>
              </div>

              <Divider orientation="left">Embedding 模型</Divider>
              <div className="model-form-grid">
                <Form.Item label="Provider" name="embeddingProvider" rules={[{ required: true }]}>
                  <Select options={[
                    { value: 'local_hash', label: '本地测试向量（仅开发）' },
                    { value: 'openai', label: 'OpenAI' },
                    { value: 'openai_compatible', label: 'OpenAI 兼容服务' }
                  ]} />
                </Form.Item>
                <Form.Item label="模型名称" name="embeddingModel">
                  <Input placeholder="例如：text-embedding-3-small" />
                </Form.Item>
                <Form.Item label="接口地址" name="embeddingBaseUrl" rules={[{ required: true, type: 'url' }]}>
                  <Input />
                </Form.Item>
                <Form.Item
                  label={<>密钥环境变量 {modelConfiguration.data.embedding_api_key_configured && <Tag color="green">已配置</Tag>}</>}
                  name="embeddingApiKeyEnv"
                  rules={[{ required: true, pattern: /^[A-Z][A-Z0-9_]*$/, message: '请输入大写环境变量名' }]}
                  extra="例如 EMBEDDING_API_KEY；实际 API Key 不会经由浏览器传输"
                >
                  <Input autoComplete="off" placeholder="EMBEDDING_API_KEY" />
                </Form.Item>
              </div>

              <Divider orientation="left">联网搜索</Divider>
              <div className="model-form-grid">
                <Form.Item label="Provider" name="webSearchProvider" rules={[{ required: true }]}>
                  <Select options={[
                    { value: 'disabled', label: '不启用' },
                    { value: 'serper', label: 'Serper' }
                  ]} />
                </Form.Item>
                <Form.Item label="接口地址" name="webSearchBaseUrl" rules={[{ required: true, type: 'url' }]}>
                  <Input />
                </Form.Item>
                <Form.Item
                  label={<>密钥环境变量 {modelConfiguration.data.web_search_api_key_configured && <Tag color="green">已配置</Tag>}</>}
                  name="webSearchApiKeyEnv"
                  rules={[{ required: true, pattern: /^[A-Z][A-Z0-9_]*$/, message: '请输入大写环境变量名' }]}
                  extra="例如 WEB_SEARCH_API_KEY；实际 API Key 不会经由浏览器传输"
                >
                  <Input autoComplete="off" placeholder="WEB_SEARCH_API_KEY" />
                </Form.Item>
              </div>
              <Alert
                type="info"
                showIcon
                message="Embedding 维度固定为 1536"
                description="更换 Embedding 模型时必须选择输出 1536 维向量的模型，否则知识入库会失败。"
              />
              <Space className="form-actions">
                <Button type="primary" htmlType="submit" loading={updateModel.isPending} disabled={modelConfiguration.data.legacy_inline_credentials_detected}>保存模型配置</Button>
                <Button onClick={() => testModel.mutate()} loading={testModel.isPending}>测试对话模型</Button>
              </Space>
            </Form>
          )}
        </article>
      </section>
    </div>
  )
}
