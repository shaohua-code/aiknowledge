import { useState, useMemo } from 'react'
import { message } from 'antd'
import { useCurrentProject } from '@/stores/project'
import { useKnowledgeBases } from '@/pages/knowledge-bases/hooks/useKnowledgeBases'
import { useResearchRun } from './hooks/useResearchRun'
import ResearchForm, { type ResearchFormValues } from './components/ResearchForm'
import ResearchResultPanel from './components/ResearchResultPanel'
import FeedbackModal from './components/FeedbackModal'
import type { ResearchResult, ResearchRunPayload } from '@/api/research'

// 默认表单值
const DEFAULT_FORM: ResearchFormValues = {
  question: '',
  outputType: 'narrative',
  strategy: 'full',
  knowledgeBaseIds: [],
  toolCodes: [],
  context: ''
}

/**
 * 智能研究台页面
 * - 左侧：研究表单（问题、输出类型、策略、知识库、工具、上下文）
 * - 右侧：研究结果面板（答案/结论/证据/置信度/耗时/降级）
 * - 结果底部反馈弹窗
 * - 查询条件集中在 useState 对象
 */
export default function ResearchPage() {
  const currentProject = useCurrentProject()
  // 加载知识库列表（仅 active）
  const { data: knowledgeBases = [] } = useKnowledgeBases({ status: 'active' })

  // 研究表单状态（集中管理）
  const [formData, setFormData] = useState<ResearchFormValues>(DEFAULT_FORM)
  // 研究结果
  const [result, setResult] = useState<ResearchResult | null>(null)
  // 反馈弹窗
  const [feedbackOpen, setFeedbackOpen] = useState(false)

  const runMutation = useResearchRun()

  /** 表单字段更新（patch 模式） */
  function handleChange(patch: Partial<ResearchFormValues>) {
    setFormData((f) => ({ ...f, ...patch }))
  }

  /** 重置表单与结果 */
  function handleReset() {
    setFormData(DEFAULT_FORM)
    setResult(null)
  }

  /** 校验并提交研究 */
  async function handleSubmit() {
    // 基础校验
    if (!formData.question.trim()) {
      message.warning('请输入研究问题')
      return
    }
    if (formData.knowledgeBaseIds.length === 0) {
      message.warning('请至少选择一个知识库')
      return
    }

    // 解析 context JSON（可选）
    let context: Record<string, unknown> | null = null
    if (formData.context.trim()) {
      try {
        context = JSON.parse(formData.context)
      } catch {
        message.error('上下文必须为合法 JSON')
        return
      }
    }

    // 组装同步研究请求
    const payload: ResearchRunPayload = {
      question: formData.question.trim(),
      outputType: formData.outputType,
      strategy: formData.strategy,
      knowledgeBaseIds: formData.knowledgeBaseIds,
      toolCodes: formData.toolCodes.length ? formData.toolCodes : undefined,
      context
    }

    try {
      const data = await runMutation.mutateAsync(payload)
      setResult(data)
      message.success('研究完成')
    } catch {
      // 错误已由拦截器提示
    }
  }

  // 当前研究 requestId（用于反馈）
  const currentRequestId = useMemo(() => result?.requestId || '', [result])

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部标题 */}
      <div className="mb-4 flex flex-col leading-tight">
        <span className="text-xl font-semibold text-gray-800">智能研究台</span>
        {currentProject && (
          <span className="text-sm text-gray-400">
            当前项目：{currentProject.name}（{currentProject.code}）
          </span>
        )}
      </div>

      {/* 主体：左右两栏 */}
      <div className="grid flex-1 grid-cols-1 gap-4 overflow-hidden lg:grid-cols-12">
        {/* 左侧表单 */}
        <div className="lg:col-span-5 xl:col-span-4">
          <ResearchForm
            value={formData}
            onChange={handleChange}
            knowledgeBases={knowledgeBases}
            onSubmit={handleSubmit}
            loading={runMutation.isPending}
            onReset={handleReset}
          />
        </div>
        {/* 右侧结果 */}
        <div className="rounded border border-gray-200 bg-white p-4 lg:col-span-7 xl:col-span-8">
          <ResearchResultPanel
            result={result}
            loading={runMutation.isPending}
            onFeedback={() => {
              if (!result) {
                message.warning('暂无研究结果可反馈')
                return
              }
              setFeedbackOpen(true)
            }}
          />
        </div>
      </div>

      {/* 反馈弹窗 */}
      <FeedbackModal
        open={feedbackOpen}
        requestId={currentRequestId}
        onCancel={() => setFeedbackOpen(false)}
        onSuccess={() => setFeedbackOpen(false)}
      />
    </div>
  )
}
