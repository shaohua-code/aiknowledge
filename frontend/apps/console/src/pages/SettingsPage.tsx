import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Descriptions, Divider, Form, Input, Select, Space, Spin, Tag } from 'antd'
import type { ModelConfigurationUpdate } from '@aik/contracts'
import { PageHeader, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { applicationApi, modelConfigurationApi } from '@/api/platform'
import { useApplicationContext } from '@/hooks/useApplicationContext'

export default function SettingsPage() {
  const queryClient = useQueryClient()
  const { application, environment } = useApplicationContext()
  const modelConfiguration = useQuery({
    queryKey: ['model-configuration'],
    queryFn: modelConfigurationApi.get
  })
  const update = useMutation({
    mutationFn: (values: { name: string; description?: string }) =>
      applicationApi.update(application!.id, values),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['applications'] })
  })
  const updateModel = useMutation({
    mutationFn: modelConfigurationApi.update,
    onSuccess: (data) => queryClient.setQueryData(['model-configuration'], data)
  })
  const testModel = useMutation({ mutationFn: modelConfigurationApi.test })
  if (!application || !environment) return null

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
          {update.isSuccess && <Alert type="success" showIcon message="应用信息已保存" />}
          {update.error && <Alert type="error" showIcon message={asPlatformError(update.error).message} />}
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
            配置平台统一使用的对话模型、向量模型和联网搜索。密钥保存后不会在页面回显。
          </p>
          {modelConfiguration.isLoading && <Spin />}
          {modelConfiguration.error && (
            <Alert type="error" showIcon message={asPlatformError(modelConfiguration.error).message} />
          )}
          {updateModel.isSuccess && <Alert type="success" showIcon message="模型配置已保存并生效" />}
          {updateModel.error && (
            <Alert type="error" showIcon message={asPlatformError(updateModel.error).message} />
          )}
          {testModel.isSuccess && <Alert type="success" showIcon message="对话模型连接成功" />}
          {testModel.error && (
            <Alert type="error" showIcon message={asPlatformError(testModel.error).message} />
          )}
          {modelConfiguration.data && (
            <Form<ModelConfigurationUpdate>
              layout="vertical"
              initialValues={modelConfiguration.data}
              onFinish={(values) => updateModel.mutate(values)}
            >
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
                  label={<>API Key {modelConfiguration.data.chatApiKeyConfigured && <Tag color="green">已配置</Tag>}</>}
                  name="chatApiKey"
                  extra="留空表示保留现有密钥"
                >
                  <Input.Password autoComplete="new-password" placeholder="sk-..." />
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
                  label={<>API Key {modelConfiguration.data.embeddingApiKeyConfigured && <Tag color="green">已配置</Tag>}</>}
                  name="embeddingApiKey"
                  extra="留空表示保留现有密钥"
                >
                  <Input.Password autoComplete="new-password" />
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
                  label={<>API Key {modelConfiguration.data.webSearchApiKeyConfigured && <Tag color="green">已配置</Tag>}</>}
                  name="webSearchApiKey"
                  extra="留空表示保留现有密钥"
                >
                  <Input.Password autoComplete="new-password" />
                </Form.Item>
              </div>
              <Alert
                type="info"
                showIcon
                message="Embedding 维度固定为 1536"
                description="更换 Embedding 模型时必须选择输出 1536 维向量的模型，否则知识入库会失败。"
              />
              <Space className="form-actions">
                <Button type="primary" htmlType="submit" loading={updateModel.isPending}>保存模型配置</Button>
                <Button onClick={() => testModel.mutate()} loading={testModel.isPending}>测试对话模型</Button>
              </Space>
            </Form>
          )}
        </article>
      </section>
    </div>
  )
}
