import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Checkbox, Form, Input, Modal, Popconfirm, Table, Typography } from 'antd'
import type { ApiKeyCreated } from '@aik/contracts'
import { EmptyState, ErrorState, LoadingState, PageHeader, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { developerApi } from '@/api/platform'
import { useApplicationContext } from '@/hooks/useApplicationContext'

const SCOPE_OPTIONS = [
  { label: '检索知识 knowledge:read', value: 'knowledge:read' },
  { label: '运行回答 answer:run', value: 'answer:run' },
  { label: '提交反馈 feedback:write', value: 'feedback:write' }
]

export default function DeveloperPage() {
  const queryClient = useQueryClient()
  const { applicationId, environmentId, environment } = useApplicationContext()
  const [open, setOpen] = useState(false)
  const [created, setCreated] = useState<ApiKeyCreated | null>(null)
  const [form] = Form.useForm()
  const keys = useQuery({
    queryKey: ['api-keys', applicationId, environmentId],
    queryFn: () => developerApi.keys(applicationId, environmentId)
  })
  const create = useMutation({
    mutationFn: (values: { name: string; scopes: string[] }) =>
      developerApi.createKey(applicationId, environmentId, values),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: ['api-keys', applicationId, environmentId] })
      setOpen(false)
      setCreated(row)
      form.resetFields()
    }
  })
  const revoke = useMutation({
    mutationFn: (keyId: string) => developerApi.revokeKey(applicationId, environmentId, keyId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['api-keys', applicationId, environmentId] })
  })

  if (keys.isLoading) return <LoadingState rows={7} />
  if (keys.isError) {
    const error = asPlatformError(keys.error)
    return <ErrorState message={error.message} requestId={error.requestId} onRetry={() => keys.refetch()} />
  }

  const example = `curl -X POST http://localhost:8000/runtime/v1/answer \\
  -H "Authorization: Bearer <YOUR_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{"profile":"resume_job_match","query":"如何优化项目经历？","inputs":{}}'`

  return (
    <div className="workspace-page">
      <PageHeader
        eyebrow="开发者接入"
        title="用最小权限把知识能力接入业务项目"
        description={`${environment?.name ?? ''}的 Key 只能访问当前应用环境，不能切换身份。`}
        actions={<Button type="primary" onClick={() => setOpen(true)}>创建 API Key</Button>}
      />

      {(keys.data ?? []).length === 0 ? (
        <EmptyState
          title="当前环境没有 API Key"
          description="创建后明文只展示一次。推荐分别为业务后端、定时任务创建独立 Key。"
          action={<Button onClick={() => setOpen(true)}>创建第一个 Key</Button>}
        />
      ) : (
        <Table
          rowKey="id"
          dataSource={keys.data ?? []}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '前缀', dataIndex: 'keyPrefix', render: (value) => <code>{value}…</code> },
            { title: 'Scope', dataIndex: 'scopes', render: (values: string[]) => values.join(' · ') },
            { title: '状态', dataIndex: 'status', render: (value) => <StatusPill status={value} /> },
            { title: '最近调用', dataIndex: 'lastUsedAt', render: (value) => value ? new Date(value).toLocaleString() : '从未调用' },
            {
              title: '操作',
              render: (_, row) =>
                row.status === 'active' ? (
                  <Popconfirm title="吊销后无法恢复，确认继续？" onConfirm={() => revoke.mutate(row.id)}>
                    <Button type="link" danger>吊销</Button>
                  </Popconfirm>
                ) : null
            }
          ]}
        />
      )}

      <section className="panel-card code-example">
        <div className="section-heading compact">
          <div>
            <span className="aik-eyebrow">RUNTIME API</span>
            <h2>最小调用示例</h2>
          </div>
        </div>
        <pre>{example}</pre>
      </section>

      <Modal
        title="创建应用 API Key"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
      >
        {create.error && <Alert type="error" showIcon message={asPlatformError(create.error).message} />}
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => create.mutate(values)}
          initialValues={{ scopes: ['knowledge:read', 'answer:run', 'feedback:write'] }}
        >
          <Form.Item label="Key 名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="AI 简历后端开发环境" />
          </Form.Item>
          <Form.Item label="最小权限" name="scopes" rules={[{ required: true }]}>
            <Checkbox.Group options={SCOPE_OPTIONS} className="scope-list" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="立即保存 API Key"
        open={Boolean(created)}
        onCancel={() => setCreated(null)}
        footer={<Button type="primary" onClick={() => setCreated(null)}>我已安全保存</Button>}
        closable={false}
        maskClosable={false}
      >
        <Alert
          type="warning"
          showIcon
          message="这是唯一一次显示完整密钥"
          description="关闭后平台不能恢复明文，只能吊销并创建新 Key。"
        />
        <Typography.Paragraph copyable className="secret-reveal">
          {created?.secret}
        </Typography.Paragraph>
      </Modal>
    </div>
  )
}

