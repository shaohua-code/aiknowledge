import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Alert, Button, Card, Collapse, Empty, Form, Input, List, Modal, Select, Space } from 'antd'
import { ErrorState, LoadingState, PageHeader, StatusPill } from '@aik/ui'
import type { EvaluationCaseCreateInput, EvaluationSetCreateInput } from '@aik/contracts'
import { asPlatformError } from '@/api/client'
import { evaluationApi, intelligenceApi } from '@/api/platform'
import { useApplicationContext } from '@/hooks/useApplicationContext'

interface EvaluationSetFormValues {
  code: string
  name: string
  description?: string
}

interface EvaluationCaseFormValues {
  caseCode: string
  question: string
  expectedDocumentIds?: string
  expectedAnswerModes?: string[]
  expectedOutputSchema?: string
  labels?: string
}

// 质量评测页以持久化运行和样本修订为唯一依据，不把一次请求的内存状态当成发布证据。
export default function EvaluationsPage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const { applicationId, environmentId } = useApplicationContext()
  const [createOpen, setCreateOpen] = useState(false)
  const [caseOpen, setCaseOpen] = useState(false)
  const [selectedSetId, setSelectedSetId] = useState<string>()
  const [selectedRunId, setSelectedRunId] = useState<string>()
  const [selectedAnswerProfileId, setSelectedAnswerProfileId] = useState<string>()
  const [selectedAnswerRevisionId, setSelectedAnswerRevisionId] = useState<string>()
  const [form] = Form.useForm<EvaluationSetFormValues>()
  const [caseForm] = Form.useForm<EvaluationCaseFormValues>()
  const sets = useQuery({
    queryKey: ['evaluation-sets', applicationId, environmentId],
    queryFn: () => evaluationApi.sets(applicationId, environmentId)
  })
  const cases = useQuery({
    queryKey: ['evaluation-cases', applicationId, environmentId, selectedSetId],
    queryFn: () => evaluationApi.cases(applicationId, environmentId, selectedSetId!),
    enabled: Boolean(selectedSetId)
  })
  const runs = useQuery({
    queryKey: ['evaluation-runs', applicationId, environmentId, selectedSetId],
    queryFn: () => evaluationApi.runs(applicationId, environmentId, selectedSetId!),
    enabled: Boolean(selectedSetId)
  })
  const results = useQuery({
    queryKey: ['evaluation-results', applicationId, environmentId, selectedRunId],
    queryFn: () => evaluationApi.results(applicationId, environmentId, selectedRunId!),
    enabled: Boolean(selectedRunId)
  })
  const answerProfiles = useQuery({
    queryKey: ['answer-profiles', applicationId, environmentId],
    queryFn: () => intelligenceApi.answerProfiles(applicationId, environmentId)
  })
  const answerRevisions = useQuery({
    queryKey: ['answer-revisions', applicationId, environmentId, selectedAnswerProfileId],
    queryFn: () => intelligenceApi.answerRevisions(applicationId, environmentId, selectedAnswerProfileId!),
    enabled: Boolean(selectedAnswerProfileId)
  })
  const createSet = useMutation({
    mutationFn: (values: EvaluationSetFormValues) => {
      const payload: EvaluationSetCreateInput = {
        code: values.code,
        name: values.name,
        description: values.description || ''
      }
      return evaluationApi.createSet(applicationId, environmentId, payload)
    },
    onSuccess: async (set) => {
      await queryClient.invalidateQueries({ queryKey: ['evaluation-sets', applicationId, environmentId] })
      setSelectedSetId(set.id)
      form.resetFields()
      setCreateOpen(false)
      message.success('评测集已创建，请先录入固定样本')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const createCase = useMutation({
    mutationFn: (values: EvaluationCaseFormValues) => {
      if (!selectedSetId) throw new Error('请先选择评测集')
      const expectedDocumentIds = values.expectedDocumentIds?.split(',').map((item) => item.trim()).filter(Boolean) || []
      const payload: EvaluationCaseCreateInput = {
        caseCode: values.caseCode,
        question: values.question,
        inputs: {},
        expectedDocumentIds,
        expectedAnswerModes: values.expectedAnswerModes || [],
        expectedOutputSchema: values.expectedOutputSchema?.trim() ? JSON.parse(values.expectedOutputSchema) : {},
        labels: values.labels?.split(',').map((item) => item.trim()).filter(Boolean) || []
      }
      return evaluationApi.createCase(applicationId, environmentId, selectedSetId, payload)
    },
    onSuccess: async () => {
      await cases.refetch()
      caseForm.resetFields()
      setCaseOpen(false)
      message.success('固定样本已创建')
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })
  const runEvaluation = useMutation({
    mutationFn: () => {
      if (!selectedSetId || !selectedAnswerRevisionId) throw new Error('请选择评测集和候选回答修订')
      return evaluationApi.run(applicationId, environmentId, selectedSetId, {
        answerProfileRevisionId: selectedAnswerRevisionId
      })
    },
    onSuccess: async (run) => {
      await queryClient.invalidateQueries({ queryKey: ['evaluation-runs', applicationId, environmentId, selectedSetId] })
      setSelectedRunId(run.id)
      message[run.gatePassed ? 'success' : 'warning'](
        run.gatePassed ? '评测通过基础门禁' : '评测未通过门禁，请查看失败样本后修订策略'
      )
    },
    onError: (error) => message.error(asPlatformError(error).message)
  })

  if (sets.isLoading) return <LoadingState rows={6} />
  if (sets.error) {
    const error = asPlatformError(sets.error)
    return <ErrorState message={error.message} requestId={error.requestId} onRetry={() => sets.refetch()} />
  }

  return (
    <div className="workspace-page">
      <PageHeader
        eyebrow="质量评测"
        title="用固定样本验证候选策略，再决定是否发布"
        description="评测运行会固定策略和样本修订；没有通过门禁的候选回答策略无法发布。"
        actions={<Space><Button onClick={() => setCreateOpen(true)}>新建评测集</Button><Button type="primary" disabled={!selectedSetId || !selectedAnswerRevisionId} loading={runEvaluation.isPending} onClick={() => runEvaluation.mutate()}>运行评测</Button></Space>}
      />
      <Card className="profile-card">
        <div className="form-grid">
          <label>候选回答策略<Select value={selectedAnswerProfileId} onChange={(value) => { setSelectedAnswerProfileId(value); setSelectedAnswerRevisionId(undefined) }} options={(answerProfiles.data ?? []).map((item) => ({ label: item.name, value: item.id }))} /></label>
          <label>候选修订<Select value={selectedAnswerRevisionId} disabled={!selectedAnswerProfileId || answerRevisions.isLoading} onChange={setSelectedAnswerRevisionId} options={(answerRevisions.data ?? []).filter((item) => item.state !== 'retired').map((item) => ({ label: `修订 ${item.revision} · ${item.state}`, value: item.id }))} /></label>
        </div>
        {runEvaluation.data ? <Alert className="space-top" type={runEvaluation.data.gatePassed ? 'success' : 'warning'} showIcon message={runEvaluation.data.gatePassed ? '基础门禁通过' : '基础门禁未通过'} description={`样本 ${runEvaluation.data.summary.totalCases ?? 0}，通过 ${runEvaluation.data.summary.passedCases ?? 0}，失败 ${runEvaluation.data.summary.failedCases ?? 0}`} /> : null}
      </Card>
      <div className="profile-grid">
        <section>
          <div className="section-heading compact"><h2>评测集</h2></div>
          {sets.data?.length ? sets.data.map((set) => (
            <Card key={set.id} className="profile-card" onClick={() => { setSelectedSetId(set.id); setSelectedRunId(undefined) }}>
              <div className="profile-title"><strong>{set.name}</strong><StatusPill status={set.status} /></div>
              <code>{set.code}</code><p>{set.description || '尚未填写说明'}</p>
            </Card>
          )) : <Empty description="尚无评测集" />}
        </section>
        <section>
          <div className="section-heading compact"><h2>固定样本</h2><Button disabled={!selectedSetId} onClick={() => setCaseOpen(true)}>录入样本</Button></div>
          {!selectedSetId ? <Empty description="选择左侧评测集查看样本" /> : cases.isLoading ? <LoadingState rows={4} /> : cases.error ? (
            <ErrorState message={asPlatformError(cases.error).message} onRetry={() => cases.refetch()} />
          ) : <List
            dataSource={cases.data ?? []}
            locale={{ emptyText: '尚无样本；发布门禁不会因空集通过。' }}
            renderItem={(item) => <List.Item><List.Item.Meta title={`${item.caseCode} · r${item.revision}`} description={item.question} /></List.Item>}
          />}
        </section>
      </div>
      <Card className="profile-card space-top">
        <div className="section-heading compact"><h2>运行历史与逐样本结果</h2></div>
        {!selectedSetId ? <Empty description="选择评测集后可查看运行历史" /> : runs.isLoading ? <LoadingState rows={3} /> : runs.error ? (
          <ErrorState message={asPlatformError(runs.error).message} onRetry={() => runs.refetch()} />
        ) : <div className="profile-grid">
          <section>
            <List
              dataSource={runs.data ?? []}
              locale={{ emptyText: '尚未运行评测。' }}
              renderItem={(item) => <List.Item actions={[<Button key="view" type={selectedRunId === item.id ? 'primary' : 'link'} onClick={() => setSelectedRunId(item.id)}>查看结果</Button>]}>
                <List.Item.Meta
                  title={<Space><code>{item.id.slice(0, 8)}</code><StatusPill status={item.status} /></Space>}
                  description={`样本 ${item.summary.totalCases ?? 0}，通过 ${item.summary.passedCases ?? 0}，失败 ${item.summary.failedCases ?? 0}；${item.gatePassed ? '基础门禁通过' : '基础门禁未通过'}`}
                />
              </List.Item>}
            />
          </section>
          <section>
            {!selectedRunId ? <Empty description="选择一次运行查看逐样本检查" /> : results.isLoading ? <LoadingState rows={3} /> : results.error ? (
              <ErrorState message={asPlatformError(results.error).message} onRetry={() => results.refetch()} />
            ) : <List
              dataSource={results.data ?? []}
              locale={{ emptyText: '该运行没有持久化样本结果。' }}
              renderItem={(item) => <List.Item>
                <List.Item.Meta
                  title={<Space><StatusPill status={item.status} /><code>{item.caseRevisionId.slice(0, 8)}</code>{item.errorCode ? <span>{item.errorCode}</span> : null}</Space>}
                  description={<Collapse size="small" items={[{ key: 'checks', label: '查看确定性检查', children: <pre>{JSON.stringify(item.checks, null, 2)}</pre> }]} />}
                />
              </List.Item>}
            />}
          </section>
        </div>}
      </Card>
      <Modal title="新建固定评测集" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={() => form.submit()} confirmLoading={createSet.isPending}>
        <Form form={form} layout="vertical" onFinish={(values) => createSet.mutate(values)}>
          <Form.Item label="名称" name="name" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item label="编码" name="code" rules={[{ required: true }, { pattern: /^[a-z][a-z0-9_]+$/ }]}><Input placeholder="resume_release_gate" /></Form.Item>
          <Form.Item label="说明" name="description"><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>
      <Modal title="录入固定评测样本" open={caseOpen} onCancel={() => setCaseOpen(false)} onOk={() => caseForm.submit()} confirmLoading={createCase.isPending}>
        <Form form={caseForm} layout="vertical" onFinish={(values) => createCase.mutate(values)}>
          <Form.Item label="样本编码" name="caseCode" rules={[{ required: true }, { pattern: /^[a-z][a-z0-9_]+$/ }]}><Input placeholder="resume_no_evidence" /></Form.Item>
          <Form.Item label="脱敏问题" name="question" rules={[{ required: true }]}><Input.TextArea rows={4} /></Form.Item>
          <Form.Item label="预期回答模式" name="expectedAnswerModes"><Select mode="multiple" options={['KNOWLEDGE_GROUNDED', 'HYBRID', 'MODEL_ONLY', 'WEB_GROUNDED', 'INSUFFICIENT_EVIDENCE', 'DEGRADED'].map((value) => ({ value }))} /></Form.Item>
          <Form.Item label="必需文档 UUID（逗号分隔）" name="expectedDocumentIds"><Input /></Form.Item>
          <Form.Item label="预期输出 Schema（JSON）" name="expectedOutputSchema" initialValue="{}"><Input.TextArea rows={4} spellCheck={false} /></Form.Item>
          <Form.Item label="标签（逗号分隔）" name="labels"><Input placeholder="no-hit,resume" /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
