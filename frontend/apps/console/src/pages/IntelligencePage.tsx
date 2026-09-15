import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App,
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
import type {
  AnswerProfileRevisionDraft,
  AnswerProfileCreateInput,
  AnswerDebugRequestInput,
  AnswerResult,
  ProfileRevision,
  RetrievalProfileRevisionDraft,
  RetrievalProfileCreateInput
} from '@aik/contracts'
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

interface RetrievalProfileFormValues {
  code: string
  name: string
  collectionIds: string[]
  topK: number
  minimumScore: number
  vectorWeight: number
  lexicalWeight: number
  metadataFilters?: string
}

interface AnswerProfileFormValues {
  code: string
  name: string
  retrievalProfileId: string
  systemPrompt?: string
  outputSchema?: string
  knowledgeRequired: boolean
  modelFallbackAllowed: boolean
  webFallbackAllowed: boolean
  minimumEvidenceCount: number
  minimumEvidenceScore: number
  requireFreshData: boolean
  maximumDataAgeSeconds?: number | null
}

type ProfileKind = 'retrieval' | 'answer'

interface ProfileHistoryTarget {
  kind: ProfileKind
  id: string
  name: string
}

interface ConfigurationDifference {
  field: string
  before: unknown
  after: unknown
}

function compareConfigurations(before: Record<string, unknown>, after: Record<string, unknown>) {
  // 使用稳定 JSON 串比较嵌套值，展示的是配置差异而不是对象引用差异。
  const fields = new Set([...Object.keys(before), ...Object.keys(after)])
  return [...fields]
    .filter((field) => JSON.stringify(before[field]) !== JSON.stringify(after[field]))
    .sort()
    .map<ConfigurationDifference>((field) => ({ field, before: before[field], after: after[field] }))
}

function retrievalDraftFromRevision(revision: ProfileRevision): RetrievalProfileRevisionDraft {
  // 配置快照来自后端不可变修订；复制只构造草稿，不会改写原版本。
  const configuration = revision.configuration as {
    collection_ids: string[]
    top_k: number
    minimum_score: number
    vector_weight: number
    lexical_weight: number
    metadata_filters: Record<string, string | number | boolean>
  }
  return {
    collectionIds: configuration.collection_ids,
    topK: configuration.top_k,
    minimumScore: configuration.minimum_score,
    vectorWeight: configuration.vector_weight,
    lexicalWeight: configuration.lexical_weight,
    metadataFilters: configuration.metadata_filters
  }
}

function answerDraftFromRevision(revision: ProfileRevision): AnswerProfileRevisionDraft | null {
  // 回答草稿必须保留所钉住的检索修订，缺失该关系时宁可拒绝复制。
  if (!revision.retrievalRevisionId) return null
  const configuration = revision.configuration as {
    system_prompt: string
    output_schema: Record<string, unknown>
    tool_codes: string[]
    knowledge_required: boolean
    model_fallback_allowed: boolean
    web_fallback_allowed: boolean
    minimum_evidence_count: number
    minimum_evidence_score: number
    require_fresh_data: boolean
    maximum_data_age_seconds: number | null
  }
  return {
    retrievalRevisionId: revision.retrievalRevisionId,
    systemPrompt: configuration.system_prompt,
    outputSchema: configuration.output_schema,
    toolCodes: configuration.tool_codes,
    knowledgeRequired: configuration.knowledge_required,
    modelFallbackAllowed: configuration.model_fallback_allowed,
    webFallbackAllowed: configuration.web_fallback_allowed,
    minimumEvidenceCount: configuration.minimum_evidence_count,
    minimumEvidenceScore: configuration.minimum_evidence_score,
    requireFreshData: configuration.require_fresh_data,
    maximumDataAgeSeconds: configuration.maximum_data_age_seconds
  }
}

export default function IntelligencePage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const { applicationId, environmentId, application } = useApplicationContext()
  const [retrievalOpen, setRetrievalOpen] = useState(false)
  const [answerOpen, setAnswerOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [inputs, setInputs] = useState('{}')
  const [profileCode, setProfileCode] = useState('')
  const [result, setResult] = useState<AnswerResult | null>(null)
  const [historyTarget, setHistoryTarget] = useState<ProfileHistoryTarget | null>(null)
  const [comparisonRevisionIds, setComparisonRevisionIds] = useState<string[]>([])
  const [editingRevision, setEditingRevision] = useState<ProfileRevision | null>(null)
  const [draftConfiguration, setDraftConfiguration] = useState('')
  const [retrievalForm] = Form.useForm<RetrievalProfileFormValues>()
  const [answerForm] = Form.useForm<AnswerProfileFormValues>()

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
  const profileRevisions = useQuery({
    queryKey: ['profile-revisions', applicationId, environmentId, historyTarget?.kind, historyTarget?.id],
    queryFn: () =>
      historyTarget?.kind === 'retrieval'
        ? intelligenceApi.retrievalRevisions(applicationId, environmentId, historyTarget.id)
        : intelligenceApi.answerRevisions(applicationId, environmentId, historyTarget!.id),
    enabled: Boolean(historyTarget)
  })
  const profileReleases = useQuery({
    queryKey: ['profile-releases', applicationId, environmentId, historyTarget?.id],
    queryFn: () => intelligenceApi.profileReleases(applicationId, environmentId, historyTarget!.id),
    enabled: Boolean(historyTarget)
  })

  const createRetrieval = useMutation({
    mutationFn: (values: RetrievalProfileFormValues) => {
      const metadataFilters = values.metadataFilters?.trim()
        ? JSON.parse(values.metadataFilters)
        : {}
      const payload: RetrievalProfileCreateInput = {
        code: values.code,
        name: values.name,
        collectionIds: values.collectionIds,
        topK: values.topK,
        minimumScore: values.minimumScore,
        vectorWeight: values.vectorWeight,
        lexicalWeight: values.lexicalWeight,
        metadataFilters
      }
      return intelligenceApi.createRetrievalProfile(applicationId, environmentId, payload)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['retrieval-profiles', applicationId, environmentId]
      })
      retrievalForm.resetFields()
      setRetrievalOpen(false)
      message.success('检索策略已创建')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const createAnswer = useMutation({
    mutationFn: (values: AnswerProfileFormValues) => {
      const outputSchema = values.outputSchema?.trim() ? JSON.parse(values.outputSchema) : {}
      const payload: AnswerProfileCreateInput = {
        code: values.code,
        name: values.name,
        retrievalProfileId: values.retrievalProfileId,
        systemPrompt: values.systemPrompt ?? '',
        outputSchema,
        knowledgeRequired: values.knowledgeRequired,
        modelFallbackAllowed: values.modelFallbackAllowed,
        webFallbackAllowed: values.webFallbackAllowed,
        minimumEvidenceCount: values.minimumEvidenceCount,
        minimumEvidenceScore: values.minimumEvidenceScore,
        requireFreshData: values.requireFreshData,
        maximumDataAgeSeconds: values.maximumDataAgeSeconds
      }
      return intelligenceApi.createAnswerProfile(applicationId, environmentId, payload)
    },
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: ['answer-profiles', applicationId, environmentId] })
      answerForm.resetFields()
      setProfileCode(row.code)
      setAnswerOpen(false)
      message.success('回答策略已创建')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const runAnswer = useMutation({
    mutationFn: () => {
      let temporaryInputs: Record<string, unknown>
      try {
        temporaryInputs = JSON.parse(inputs || '{}')
      } catch {
        throw new Error('临时上下文必须是合法 JSON')
      }
      const profile = answerRows.find((item) => item.code === profileCode)
      if (!profile) throw new Error('请选择当前环境中的回答策略')
      const payload: AnswerDebugRequestInput = {
        query,
        inputs: temporaryInputs
      }
      return intelligenceApi.debugAnswerProfile(applicationId, environmentId, profile.id, payload)
    },
    onSuccess: setResult,
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const copyRevisionAsDraft = useMutation({
    mutationFn: (revision: ProfileRevision) => {
      if (!historyTarget) throw new Error('请先选择策略')
      if (historyTarget.kind === 'retrieval') {
        return intelligenceApi.createRetrievalDraft(
          applicationId,
          environmentId,
          historyTarget.id,
          retrievalDraftFromRevision(revision)
        )
      }
      const payload = answerDraftFromRevision(revision)
      if (!payload) throw new Error('回答修订缺少检索版本引用，不能复制')
      return intelligenceApi.createAnswerDraft(applicationId, environmentId, historyTarget.id, payload)
    },
    onSuccess: async () => {
      await profileRevisions.refetch()
      message.success('已创建新的草稿修订')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const releaseRevision = useMutation({
    mutationFn: ({ revision, action }: { revision: ProfileRevision; action: 'publish' | 'rollback' }) => {
      if (!historyTarget) throw new Error('请先选择策略')
      const api = historyTarget.kind === 'retrieval'
        ? action === 'publish' ? intelligenceApi.publishRetrievalRevision : intelligenceApi.rollbackRetrievalRevision
        : action === 'publish' ? intelligenceApi.publishAnswerRevision : intelligenceApi.rollbackAnswerRevision
      return api(applicationId, environmentId, historyTarget.id, revision.id)
    },
    onSuccess: async () => {
      await Promise.all([profileRevisions.refetch(), profileReleases.refetch()])
      message.success('发布记录已追加')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const updateDraft = useMutation({
    mutationFn: () => {
      if (!historyTarget || !editingRevision) throw new Error('请先选择草稿')
      const parsed = JSON.parse(draftConfiguration) as Record<string, unknown>
      if (historyTarget.kind === 'retrieval') {
        return intelligenceApi.updateRetrievalDraft(
          applicationId,
          environmentId,
          historyTarget.id,
          editingRevision.id,
          parsed as RetrievalProfileRevisionDraft
        )
      }
      return intelligenceApi.updateAnswerDraft(
        applicationId,
        environmentId,
        historyTarget.id,
        editingRevision.id,
        parsed as AnswerProfileRevisionDraft
      )
    },
    onSuccess: async () => {
      await profileRevisions.refetch()
      setEditingRevision(null)
      message.success('草稿已保存，尚未发布')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })

  const beginDraftEdit = (revision: ProfileRevision) => {
    // 编辑器只展示接口允许提交的字段，策略编码和名称仍由主策略固定管理。
    const payload = historyTarget?.kind === 'retrieval'
      ? retrievalDraftFromRevision(revision)
      : answerDraftFromRevision(revision)
    if (!payload) {
      message.error('该回答修订缺少检索版本引用，不能编辑')
      return
    }
    setDraftConfiguration(JSON.stringify(payload, null, 2))
    setEditingRevision(revision)
  }

  const comparedRevisions = (profileRevisions.data ?? []).filter((revision) =>
    comparisonRevisionIds.includes(revision.id)
  )
  const configurationDifferences = comparedRevisions.length === 2
    ? compareConfigurations(comparedRevisions[0].configuration, comparedRevisions[1].configuration)
    : []

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
                      message="调试使用管理员会话，不需要业务 API Key"
                      description="控制面固定调用所选策略的已发布修订，并记录真实运行轨迹；浏览器不会接收或转发业务 Key。"
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
                    <Button
                      type="primary"
                      loading={runAnswer.isPending}
                      disabled={!query || !profileCode}
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
                      <div className="profile-card-actions">
                        <Button
                          size="small"
                          onClick={() => setHistoryTarget({ kind: 'retrieval', id: item.id, name: item.name })}
                        >
                          版本与发布
                        </Button>
                      </div>
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
                      <div className="profile-card-actions">
                        <Button
                          size="small"
                          onClick={() => setHistoryTarget({ kind: 'answer', id: item.id, name: item.name })}
                        >
                          版本与发布
                        </Button>
                      </div>
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
        title={historyTarget ? `${historyTarget.name} · 版本与发布` : '版本与发布'}
        open={Boolean(historyTarget)}
        width={820}
        footer={null}
        onCancel={() => {
          setHistoryTarget(null)
          setComparisonRevisionIds([])
        }}
      >
        {profileRevisions.isLoading || profileReleases.isLoading ? (
          <LoadingState rows={4} />
        ) : profileRevisions.error || profileReleases.error ? (
          <ErrorState
            message={asPlatformError(profileRevisions.error || profileReleases.error).message}
            onRetry={() => Promise.all([profileRevisions.refetch(), profileReleases.refetch()])}
          />
        ) : (
          <Space direction="vertical" size="middle" className="full-width">
            <Alert
              type="info"
              showIcon
              message="发布决定运行版本"
              description="复制生成新草稿；发布和回滚都会追加不可变事件，不会覆写历史配置。"
            />
            <List
              header={<strong>修订历史</strong>}
              dataSource={profileRevisions.data ?? []}
              locale={{ emptyText: '尚无修订' }}
              renderItem={(revision) => (
                <List.Item
                  actions={[
                    <Button
                      key="copy"
                      size="small"
                      loading={copyRevisionAsDraft.isPending}
                      onClick={() => copyRevisionAsDraft.mutate(revision)}
                    >
                      复制为草稿
                    </Button>,
                    revision.state === 'draft' ? (
                      <Space key="draft-actions" size="small">
                        <Button size="small" onClick={() => beginDraftEdit(revision)}>
                          编辑草稿
                        </Button>
                        <Button
                          size="small"
                          type="primary"
                          loading={releaseRevision.isPending}
                          onClick={() => releaseRevision.mutate({ revision, action: 'publish' })}
                        >
                          发布
                        </Button>
                      </Space>
                    ) : null,
                    revision.state === 'published' ? (
                      <Button
                        key="rollback"
                        size="small"
                        loading={releaseRevision.isPending}
                        onClick={() => releaseRevision.mutate({ revision, action: 'rollback' })}
                      >
                        回滚到此版本
                      </Button>
                    ) : null
                  ].filter(Boolean)}
                >
                  <List.Item.Meta
                    title={
                      <Space>
                        <span>修订 {revision.revision}</span>
                        <StatusPill status={revision.state} />
                        {revision.publishedAt ? <span className="muted">已发布</span> : null}
                      </Space>
                    }
                    description={
                      <Collapse
                        ghost
                        items={[
                          {
                            key: revision.id,
                            label: '查看冻结配置',
                            children: <pre>{JSON.stringify(revision.configuration, null, 2)}</pre>
                          }
                        ]}
                      />
                    }
                  />
                </List.Item>
              )}
            />
            <section className="revision-comparison">
              <strong>配置差异比较</strong>
              <p className="muted">选择两个修订，逐字段查看发布前后的实际变化。</p>
              <Select
                mode="multiple"
                maxCount={2}
                value={comparisonRevisionIds}
                onChange={setComparisonRevisionIds}
                placeholder="选择两个修订进行比较"
                className="full-width"
                options={(profileRevisions.data ?? []).map((revision) => ({
                  label: `修订 ${revision.revision} · ${revision.state}`,
                  value: revision.id
                }))}
              />
              {comparedRevisions.length === 2 ? (
                <List
                  size="small"
                  dataSource={configurationDifferences}
                  locale={{ emptyText: '两个修订的冻结配置相同' }}
                  renderItem={(difference) => (
                    <List.Item>
                      <List.Item.Meta
                        title={<code>{difference.field}</code>}
                        description={
                          <div className="revision-diff-values">
                            <pre>{JSON.stringify(difference.before, null, 2)}</pre>
                            <pre>{JSON.stringify(difference.after, null, 2)}</pre>
                          </div>
                        }
                      />
                    </List.Item>
                  )}
                />
              ) : null}
            </section>
            <List
              size="small"
              header={<strong>发布事件</strong>}
              dataSource={profileReleases.data ?? []}
              locale={{ emptyText: '尚无发布事件' }}
              renderItem={(event) => (
                <List.Item>
                  <span>
                    {event.action} · 修订 {event.revisionId.slice(0, 8)} · {new Date(event.createdAt).toLocaleString()}
                  </span>
                </List.Item>
              )}
            />
          </Space>
        )}
      </Modal>

      <Modal
        title={editingRevision ? `编辑草稿修订 ${editingRevision.revision}` : '编辑草稿'}
        open={Boolean(editingRevision)}
        width={760}
        okText="保存草稿"
        confirmLoading={updateDraft.isPending}
        onOk={() => updateDraft.mutate()}
        onCancel={() => setEditingRevision(null)}
      >
        <Alert
          type="info"
          showIcon
          message="保存不等于发布"
          description="此处只接受当前策略类型的生成契约字段；服务端会再次校验集合、检索修订与 JSON Schema。"
        />
        <Input.TextArea
          value={draftConfiguration}
          onChange={(event) => setDraftConfiguration(event.target.value)}
          rows={20}
          spellCheck={false}
          className="revision-json-editor"
        />
      </Modal>

      <Modal
        title={`新建${application?.applicationType === 'fund' ? '严格证据' : '知识增强'}回答策略`}
        open={answerOpen}
        width={680}
        onCancel={() => setAnswerOpen(false)}
        onOk={() => answerForm.submit()}
        confirmLoading={createAnswer.isPending}
      >
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
