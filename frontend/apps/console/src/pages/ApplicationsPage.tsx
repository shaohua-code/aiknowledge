import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Form, Input, Modal, Select } from 'antd'
import { Link, useSearchParams } from 'react-router-dom'
import { ErrorState, LoadingState, PageHeader, StatusPill } from '@aik/ui'
import { asPlatformError } from '@/api/client'
import { applicationApi } from '@/api/platform'
import LineIcon from '@/components/LineIcon'

const applicationTypeNames: Record<string, string> = {
  resume: 'AI 简历',
  fund: 'AI 基金',
  general: '通用知识',
  custom: '自定义应用'
}

export default function ApplicationsPage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [open, setOpen] = useState(searchParams.get('create') === 'true')
  const [form] = Form.useForm()
  const closeCreate = () => {
    setOpen(false)
    setSearchParams({}, { replace: true })
    form.resetFields()
  }
  const openCreate = () => {
    setOpen(true)
    setSearchParams({ create: 'true' }, { replace: true })
  }
  const applications = useQuery({ queryKey: ['applications'], queryFn: applicationApi.list })
  const create = useMutation({
    mutationFn: applicationApi.create,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['applications'] })
      closeCreate()
      message.success('AI 应用空间已创建')
    }
  })

  if (applications.isLoading) return <LoadingState rows={7} />
  if (applications.isError) {
    const error = asPlatformError(applications.error)
    return (
      <ErrorState
        message={error.message}
        requestId={error.requestId}
        suggestion={error.suggestion ?? undefined}
        onRetry={() => applications.refetch()}
      />
    )
  }
  const rows = applications.data ?? []
  const createError = create.error ? asPlatformError(create.error) : null

  return (
    <div className="page-container">
      <PageHeader
        eyebrow="AI 应用"
        title="一个业务项目，一个独立知识边界"
        description="AI 简历、AI 基金以及未来项目分别拥有知识、策略、密钥和运行记录。"
        actions={
          <Button type="primary" onClick={openCreate}>
            创建 AI 应用 <LineIcon name="arrow" size={16} />
          </Button>
        }
      />

      {rows.length === 0 ? (
        <section className="applications-empty-state">
          <div className="applications-empty-visual" aria-hidden="true">
            <span className="empty-app-card card-one">R</span>
            <span className="empty-app-card card-two">F</span>
            <span className="empty-app-core"><LineIcon name="layers" size={30} /></span>
          </div>
          <span className="section-kicker">YOUR FIRST APPLICATION</span>
          <h2>从一个独立的 AI 应用空间开始</h2>
          <p>推荐先创建“AI 简历”，导入专属知识并跑通知识优先、模型兜底的完整回答链路。</p>
          <div className="applications-empty-benefits">
            <span><LineIcon name="shield" size={16} /> 独立知识边界</span>
            <span><LineIcon name="search" size={16} /> 可验证回答</span>
            <span><LineIcon name="pulse" size={16} /> 全链路追踪</span>
          </div>
          <Button type="primary" size="large" onClick={openCreate}>
            创建 AI 简历 <LineIcon name="arrow" size={17} />
          </Button>
        </section>
      ) : (
        <div className="application-card-grid wide">
          {rows.map((application) => {
            const environment =
              application.environments.find((item) => item.code === 'development') ??
              application.environments[0]
            return (
              <article className={`application-card type-${application.applicationType}`} key={application.id}>
                <div className="application-card-top">
                  <span className="application-avatar">{application.name.slice(0, 1)}</span>
                  <StatusPill status={application.status} />
                </div>
                <span className="application-type-name">
                  {applicationTypeNames[application.applicationType] ?? 'AI 应用'}
                </span>
                <h3>{application.name}</h3>
                <p>{application.description || '尚未填写应用说明'}</p>
                <div className="environment-row">
                  {application.environments.map((item) => (
                    <StatusPill status={item.status} key={item.id}>
                      {item.name}
                    </StatusPill>
                  ))}
                </div>
                <footer>
                  <code>{application.code}</code>
                  <Link to={`/applications/${application.id}/${environment.id}/overview`}>
                    进入应用 <LineIcon name="arrow" size={15} />
                  </Link>
                </footer>
              </article>
            )
          })}
        </div>
      )}

      <Modal
        title="创建 AI 应用"
        open={open}
        onCancel={closeCreate}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
        okText="创建应用"
        width={560}
      >
        <div className="create-application-intro">
          <span><LineIcon name="layers" size={18} /></span>
          <p><strong>应用就是一条独立知识边界</strong><small>创建后自动生成开发、测试和生产三个隔离环境。</small></p>
        </div>
        {createError && (
          <Alert
            type="error"
            showIcon
            message={createError.title}
            description={`${createError.message}${createError.requestId ? ` · ${createError.requestId}` : ''}`}
          />
        )}
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => create.mutate(values)}
          initialValues={{ applicationType: 'resume' }}
        >
          <Form.Item label="应用名称" name="name" rules={[{ required: true }, { min: 2 }]}>
            <Input placeholder="例如：AI 简历" />
          </Form.Item>
          <Form.Item
            label="应用编码"
            name="code"
            extra="创建后不可直接修改，只允许小写字母、数字和连字符"
            rules={[{ required: true }, { pattern: /^[a-z][a-z0-9-]+$/ }]}
          >
            <Input placeholder="ai-resume" />
          </Form.Item>
          <Form.Item label="应用类型" name="applicationType" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'resume', label: 'AI 简历' },
                { value: 'fund', label: 'AI 基金' },
                { value: 'general', label: '通用知识应用' },
                { value: 'custom', label: '自定义应用' }
              ]}
            />
          </Form.Item>
          <Form.Item label="说明" name="description">
            <Input.TextArea rows={3} placeholder="说明这个 AI 应用负责什么，以及知识边界是什么" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
