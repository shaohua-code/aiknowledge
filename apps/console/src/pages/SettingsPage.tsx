import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Descriptions, Form, Input } from 'antd'
import { PageHeader, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { applicationApi } from '@/api/platform'
import { useApplicationContext } from '@/hooks/useApplicationContext'

export default function SettingsPage() {
  const queryClient = useQueryClient()
  const { application, environment } = useApplicationContext()
  const update = useMutation({
    mutationFn: (values: { name: string; description?: string }) =>
      applicationApi.update(application!.id, values),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['applications'] })
  })
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
      </section>
    </div>
  )
}

