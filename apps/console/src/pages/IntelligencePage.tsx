import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Select,
  Slider,
  Space,
  Switch,
  Tabs
} from 'antd'
import { EmptyState, ErrorState, LoadingState, PageHeader, StatusPill } from '@aik/ui'
import type { AnswerResult } from '@aik/contracts'
import { asPlatformError } from '@/api/client'
import { intelligenceApi, knowledgeApi } from '@/api/platform'
import { useApplicationContext } from '@/hooks/useApplicationContext'

const MODE_LABELS: Record<string, string> = {
  KNOWLEDGE_GROUNDED: '基于知识库',
  HYBRID: '知识 + AI 分析',
  MODEL_ONLY: 'AI 通用回答',
  WEB_GROUNDED: '联网证据回答',
  INSUFFICIENT_EVIDENCE: '证据不足',
  DEGRADED: '降级回答'
}

export default function IntelligencePage() {
  const queryClient = useQueryClient()
  const { applicationId, environmentId, application } = useApplicationContext()
  const [retrievalOpen, setRetrievalOpen] = useState(false)
  const [answerOpen, setAnswerOpen] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [query, setQuery] = useState('')
  const [inputs, setInputs] = useState('{}')
  const [profileCode, setProfileCode] = useState('')
  const [result, setResult] = useState<AnswerResult | null>(null)
  const [retrievalForm] = Form.useForm()
  const [answerForm] = Form.useForm()

  const collections = useQuery({
    queryKey: ['collections', applicationId, environmentId],
    queryFn: () => knowledgeApi.collections(applicationId, environmentId)
  })
  const retrievalProfiles = useQuery({
    queryKey: ['retrieval-profiles', applicationId, environmentId],
    queryFn: () => intelligenceApi.retrievalProfiles(applicationId, environmentId)
  })
  const answerProfiles = useQuery({
    queryKey: ['answer-profiles', applicationId, environmentId],
    queryFn: () => intelligenceApi.answerProfiles(applicationId, environmentId)
  })

  const createRetrieval = useMutation({
    mutationFn: (values: Record<string, any>) => {
      const metadataFilters = values.metadataFilters?.trim()
        ? JSON.parse(values.metadataFilters)
        : {}
      return intelligenceApi.createRetrievalProfile(applicationId, environmentId, {
        ...values,
        metadataFilters
      })
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['retrieval-profiles', applicationId, environmentId]
      })
      retrievalForm.resetFields()
      setRetrievalOpen(false)
    }
  })
  const createAnswer = useMutation({
    mutationFn: (values: Record<string, any>) => {
      const outputSchema = values.outputSchema?.trim() ? JSON.parse(values.outputSchema) : {}
      return intelligenceApi.createAnswerProfile(applicationId, environmentId, {
        ...values,
        outputSchema
      })
    },
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: ['answer-profiles', applicationId, environmentId] })
      answerForm.resetFields()
      setProfileCode(row.code)
      setAnswerOpen(false)
    }
  })
  const runAnswer = useMutation({
    mutationFn: () => {
      let temporaryInputs: Record<string, unknown>
      try {
        temporaryInputs = JSON.parse(inputs || '{}')
      } catch {
        throw new Error('临时上下文必须是合法 JSON')
      }
      return intelligenceApi.answer(apiKey, {
        profile: profileCode,
        query,
        inputs: temporaryInputs,
        options: { includeCitations: true, includeEvidence: true }
      })
    },
    onSuccess: setResult
  })

  if (collections.isLoading || retrievalProfiles.isLoading || answerProfiles.isLoading) {
    return <LoadingState rows={8} />
  }
  const firstError = collections.error || retrievalProfiles.error || answerProfiles.error
  if (firstError) {
    const error = asPlatformError(firstError)
    return (
      <ErrorState
        message={error.message}
        requestId={error.requestId}
        suggestion={error.suggestion ?? undefined}
        onRetry={() => Promise.all([collections.refetch(), retrievalProfiles.refetch(), answerProfiles.refetch()])}
      />
    )
  }

  const retrievalRows = retrievalProfiles.data ?? []
  const answerRows = answerProfiles.data ?? []
  const answerError = runAnswer.error ? asPlatformError(runAnswer.error) : null

  return (
    <div className="workspace-page">
      <PageHeader
        eyebrow="AI 能力"
        title="决定 AI 什么时候引用知识，什么时候可以自己思考"
        description="回答策略明确区分知识事实、模型分析、通用回答和证据不足。"
        actions={
          <Space>
            <Button onClick={() => setRetrievalOpen(true)}>新建检索策略</Button>
            <Button type="primary" onClick={() => setAnswerOpen(true)} disabled={retrievalRows.length === 0}>
              新建回答策略
            </Button>
          </Space>
        }
      />

      <Tabs
        defaultActiveKey="playground"
        items={[
          {
            key: 'playground',
            label: '回答 Playground',
            children:
              answerRows.length === 0 ? (
                <EmptyState
                  title="还没有回答策略"
                  description="先创建检索策略，再配置知识不足时是否允许模型兜底。"
                  action={<Button onClick={() => setAnswerOpen(true)}>创建回答策略</Button>}
                />
              ) : (
                <div className="playground-grid">
                  <section className="panel-card playground-form">
                    <Alert
                      type="info"
                      showIcon
                      message="API Key 只保存在本页内存"
                      description="刷新或离开页面后会立即丢失，不会写入 localStorage。"
                    />
                    <label>
                      回答策略
                      <Select
                        value={profileCode || undefined}
                        onChange={setProfileCode}
                        placeholder="选择回答策略"
                        options={answerRows.map((item) => ({ label: item.name, value: item.code }))}
                      />
                    </label>
                    <label>
                      当前环境 API Key
                      <Input.Password
                        value={apiKey}
                        onChange={(event) => setApiKey(event.target.value)}
                        placeholder="aik_test_..."
                      />
                    </label>
                    <label>
                      用户问题
                      <Input.TextArea
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        rows={5}
                        placeholder="例如：分析这份简历与高级前端岗位的匹配程度"
                      />
                    </label>
                    <label>
                      临时上下文 JSON
                      <Input.TextArea
                        value={inputs}
                        onChange={(event) => setInputs(event.target.value)}
                        rows={7}
                        spellCheck={false}
                      />
                    </label>
                    {answerError && (
                      <ErrorState
                        title={answerError.title}
                        message={answerError.message}
                        requestId={answerError.requestId}
                        suggestion={answerError.suggestion ?? undefined}
                      />
                    )}
                    <Button
                      type="primary"
                      loading={runAnswer.isPending}
                      disabled={!apiKey || !query || !profileCode}
                      onClick={() => runAnswer.mutate()}
                    >
                      运行回答测试
                    </Button>
                  </section>
                  <section className="panel-card answer-result">
                    {!result ? (
                      <div className="inline-empty centered">
                        <strong>等待一次真实回答</strong>
                        <p>结果会展示回答模式、知识引用、模型补充、置信度和耗时。</p>
                      </div>
                    ) : (
                      <>
                        <div className="answer-result-header">
                          <StatusPill status={result.degraded ? 'degraded' : 'ready'}>
                            {MODE_LABELS[result.answerMode] || result.answerMode}
                          </StatusPill>
                          <span>置信度 {Math.round(result.confidence * 100)}%</span>
                        </div>
                        <div className="answer-copy">{result.answer}</div>
                        {result.warnings.length > 0 && (
                          <Alert
                            type="warning"
                            showIcon
                            message="回答声明"
                            description={result.warnings.join('；')}
                          />
                        )}
                        <Collapse
                          ghost
                          items={[
                            {
                              key: 'structured',
                              label: '结构化输出',
                              children: <pre>{JSON.stringify(result.structuredOutput, null, 2)}</pre>
                            },
                            {
                              key: 'evidence',
                              label: `知识证据 ${result.knowledge.hitCount}`,
                              children: (
                                <List
                                  dataSource={result.knowledge.evidence}
                                  renderItem={(item: any) => (
                                    <List.Item>
                                      <List.Item.Meta
                                        title={`${item.title} · ${Math.round(item.score * 100)}%`}
                                        description={item.content}
                                      />
                                    </List.Item>
                                  )}
                                />
                              )
                            },
                            {
                              key: 'web',
                              label: `联网证据 ${result.web.hitCount}`,
                              children: result.web.hitCount ? (
                                <List
                                  dataSource={result.web.citations}
                                  renderItem={(item: any) => (
                                    <List.Item>
                                      <a href={String(item.url)} target="_blank" rel="noreferrer">
                                        {String(item.title || item.url)}
                                      </a>
                                    </List.Item>
                                  )}
                                />
                              ) : (
                                <span className="muted">本次回答未使用联网证据</span>
                              )
                            }
                          ]}
                        />
                        <footer className="answer-metadata">
                          <code>{result.requestId}</code>
                          <span>{result.timing.totalMs} ms</span>
                          <span>{result.usage.inputTokens + result.usage.outputTokens} tokens</span>
                        </footer>
                      </>
                    )}
                  </section>
                </div>
              )
          },
          {
            key: 'profiles',
            label: `策略 ${retrievalRows.length + answerRows.length}`,
            children: (
              <div className="profile-grid">
                <section>
                  <div className="section-heading compact">
                    <h2>检索策略</h2>
                    <Button onClick={() => setRetrievalOpen(true)}>新增</Button>
                  </div>
                  {retrievalRows.map((item) => (
                    <Card key={item.id} className="profile-card">
                      <div className="profile-title">
                        <strong>{item.name}</strong>
                        <StatusPill status={item.status} />
                      </div>
                      <code>{item.code}</code>
                      <p>Top {item.topK} · 最低分 {item.minimumScore}</p>
                      <small>{item.collectionIds.length} 个知识集合</small>
                    </Card>
                  ))}
                </section>
                <section>
                  <div className="section-heading compact">
                    <h2>回答策略</h2>
                    <Button onClick={() => setAnswerOpen(true)} disabled={retrievalRows.length === 0}>
                      新增
                    </Button>
                  </div>
                  {answerRows.map((item) => (
                    <Card key={item.id} className="profile-card">
                      <div className="profile-title">
                        <strong>{item.name}</strong>
                        <StatusPill status={item.status} />
                      </div>
                      <code>{item.code}</code>
                      <p>
                        {item.knowledgeRequired ? '必须命中知识' : '知识优先'} ·{' '}
                        {item.modelFallbackAllowed ? '允许模型兜底' : '禁止无知识兜底'}
                      </p>
                    </Card>
                  ))}
                </section>
              </div>
            )
          }
        ]}
      />

      <Modal
        title="新建检索策略"
        open={retrievalOpen}
        onCancel={() => setRetrievalOpen(false)}
        onOk={() => retrievalForm.submit()}
        confirmLoading={createRetrieval.isPending}
      >
        {createRetrieval.error && (
          <Alert type="error" showIcon message={asPlatformError(createRetrieval.error).message} />
        )}
        <Form
          form={retrievalForm}
          layout="vertical"
          onFinish={(values) => createRetrieval.mutate(values)}
          initialValues={{ topK: 8, minimumScore: 0.55, vectorWeight: 0.65, lexicalWeight: 0.35 }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="简历岗位知识检索" />
          </Form.Item>
          <Form.Item
            label="编码"
            name="code"
            rules={[{ required: true }, { pattern: /^[a-z][a-z0-9_]+$/ }]}
          >
            <Input placeholder="resume_job_knowledge" />
          </Form.Item>
          <Form.Item label="知识集合" name="collectionIds" rules={[{ required: true }]}>
            <Select
              mode="multiple"
              options={(collections.data ?? []).map((item) => ({ label: item.name, value: item.id }))}
            />
          </Form.Item>
          <Form.Item label="最大证据数" name="topK">
            <InputNumber min={1} max={30} className="full-width" />
          </Form.Item>
          <Form.Item label="最低分" name="minimumScore">
            <Slider min={0} max={1} step={0.05} />
          </Form.Item>
          <Form.Item label="向量权重" name="vectorWeight">
            <Slider min={0} max={1} step={0.05} />
          </Form.Item>
          <Form.Item label="关键词权重" name="lexicalWeight">
            <Slider min={0} max={1} step={0.05} />
          </Form.Item>
          <Form.Item label="元数据过滤（JSON）" name="metadataFilters" initialValue="{}">
            <Input.TextArea rows={3} spellCheck={false} placeholder='{"department":"engineering"}' />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`新建${application?.applicationType === 'fund' ? '严格证据' : '知识增强'}回答策略`}
        open={answerOpen}
        width={680}
        onCancel={() => setAnswerOpen(false)}
        onOk={() => answerForm.submit()}
        confirmLoading={createAnswer.isPending}
      >
        {createAnswer.error && <Alert type="error" showIcon message={asPlatformError(createAnswer.error).message} />}
        <Form
          form={answerForm}
          layout="vertical"
          onFinish={(values) => createAnswer.mutate(values)}
          initialValues={{
            knowledgeRequired: application?.applicationType === 'fund',
            modelFallbackAllowed: application?.applicationType !== 'fund',
            webFallbackAllowed: false,
            minimumEvidenceCount: 1,
            minimumEvidenceScore: 0.55,
            requireFreshData: application?.applicationType === 'fund',
            outputSchema: '{}'
          }}
        >
          <div className="form-grid">
            <Form.Item label="名称" name="name" rules={[{ required: true }]}>
              <Input placeholder="简历岗位匹配" />
            </Form.Item>
            <Form.Item
              label="编码"
              name="code"
              rules={[{ required: true }, { pattern: /^[a-z][a-z0-9_]+$/ }]}
            >
              <Input placeholder="resume_job_match" />
            </Form.Item>
          </div>
          <Form.Item label="检索策略" name="retrievalProfileId" rules={[{ required: true }]}>
            <Select options={retrievalRows.map((item) => ({ label: item.name, value: item.id }))} />
          </Form.Item>
          <Form.Item label="系统提示词" name="systemPrompt">
            <Input.TextArea
              rows={4}
              placeholder="你是专业的简历分析助手，不得捏造用户经历和招聘结果。"
            />
          </Form.Item>
          <Form.Item label="输出 Schema（JSON）" name="outputSchema">
            <Input.TextArea rows={4} spellCheck={false} />
          </Form.Item>
          <div className="switch-grid">
            <Form.Item label="必须命中知识" name="knowledgeRequired" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="允许模型无知识兜底" name="modelFallbackAllowed" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="允许联网兜底" name="webFallbackAllowed" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="要求新鲜数据" name="requireFreshData" valuePropName="checked">
              <Switch />
            </Form.Item>
          </div>
          <div className="form-grid">
            <Form.Item label="最少证据数" name="minimumEvidenceCount">
              <InputNumber min={0} max={20} className="full-width" />
            </Form.Item>
            <Form.Item label="最低证据分" name="minimumEvidenceScore">
              <InputNumber min={0} max={1} step={0.05} className="full-width" />
            </Form.Item>
            <Form.Item label="最大数据年龄（秒）" name="maximumDataAgeSeconds">
              <InputNumber min={1} className="full-width" placeholder="例如 3600" />
            </Form.Item>
          </div>
        </Form>
      </Modal>
    </div>
  )
}
