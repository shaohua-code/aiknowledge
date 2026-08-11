import { useMemo } from 'react'
import { Alert, Progress, Tag, Typography, Divider, Empty, Spin } from 'antd'
import type { ResearchResult } from '@/api/research'
import EvidenceCard from './EvidenceCard'

const { Title, Paragraph, Text } = Typography

interface ResearchResultPanelProps {
  /** 研究结果（无结果时为 null） */
  result: ResearchResult | null
  /** 加载中状态 */
  loading: boolean
  /** 点击反馈回调 */
  onFeedback: () => void
}

/**
 * 研究结果展示面板
 * - 突出展示 answer
 * - 列表展示 conclusions / suggestedActions / uncertainties
 * - 卡片展示 evidence
 * - 置信度进度条、耗时、降级原因、风险提示
 */
export default function ResearchResultPanel({ result, loading, onFeedback }: ResearchResultPanelProps) {
  // 置信度百分比（0-100）
  const confidencePct = useMemo(() => {
    if (typeof result?.confidence !== 'number') return 0
    return Math.round(Math.min(Math.max(result.confidence, 0), 1) * 100)
  }, [result])

  // 加载态：仅展示 Spin
  if (loading) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <Spin tip="研究进行中..." size="large" />
      </div>
    )
  }

  // 空态
  if (!result) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <Empty description="暂无研究结果，请在左侧填写问题后开始研究" />
      </div>
    )
  }

  return (
    <div className="flex h-full w-full flex-col overflow-auto">
      {/* 顶部：任务信息 + 反馈按钮 */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex flex-col leading-tight">
          <Text type="secondary" className="!text-xs">taskId: {result.taskId}</Text>
          <Text type="secondary" className="!text-xs">requestId: {result.requestId}</Text>
        </div>
        <button
          type="button"
          onClick={onFeedback}
          className="rounded border border-blue-500 px-3 py-1 text-sm text-blue-600 hover:bg-blue-50"
        >
          反馈
        </button>
      </div>

      {/* 降级提示（黄色警示） */}
      {result.degraded && (
        <Alert
          type="warning"
          showIcon
          className="!mb-4"
          message="本次研究部分能力降级"
          description={result.degradedReasons?.join('；') || '部分环节未按预期执行'}
        />
      )}

      {/* 风险提示（红色警示） */}
      {result.riskNotice && (
        <Alert
          type="error"
          showIcon
          className="!mb-4"
          message="风险提示"
          description={result.riskNotice}
        />
      )}

      {/* 答案（突出显示） */}
      <div className="mb-4 rounded border border-blue-200 bg-blue-50 p-4">
        <Title level={5} className="!mb-2 !text-blue-700">研究答案</Title>
        <Paragraph className="!mb-0 !whitespace-pre-wrap !text-gray-800">{result.answer}</Paragraph>
      </div>

      {/* 置信度 */}
      <div className="mb-4">
        <div className="mb-1 flex items-center justify-between">
          <Text strong>置信度</Text>
          <Text type="secondary">{confidencePct}%</Text>
        </div>
        <Progress percent={confidencePct} status={confidencePct >= 70 ? 'success' : 'normal'} />
      </div>

      {/* 结论列表 */}
      {result.conclusions?.length ? (
        <div className="mb-4">
          <Title level={5} className="!mb-2">结论</Title>
          <ul className="list-disc space-y-1 pl-6">
            {result.conclusions.map((c, i) => (
              <li key={i} className="text-sm text-gray-700">{c}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* 建议动作 */}
      {result.suggestedActions?.length ? (
        <div className="mb-4">
          <Title level={5} className="!mb-2">建议动作</Title>
          <ul className="list-disc space-y-1 pl-6">
            {result.suggestedActions.map((a, i) => (
              <li key={i} className="text-sm text-gray-700">{a}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* 证据卡片列表 */}
      {result.evidence?.length ? (
        <div className="mb-4">
          <Title level={5} className="!mb-2">证据来源</Title>
          <div className="grid grid-cols-1 gap-3">
            {result.evidence.map((e, i) => (
              <EvidenceCard key={i} evidence={e} index={i} />
            ))}
          </div>
        </div>
      ) : null}

      {/* 不确定性 */}
      {result.uncertainties?.length ? (
        <div className="mb-4">
          <Title level={5} className="!mb-2">不确定性</Title>
          <div className="flex flex-wrap gap-2">
            {result.uncertainties.map((u, i) => (
              <Tag key={i} color="default">{u}</Tag>
            ))}
          </div>
        </div>
      ) : null}

      {/* 耗时统计 */}
      {result.timing ? (
        <>
          <Divider className="!my-3" />
          <div className="mb-4">
            <Title level={5} className="!mb-2">耗时统计</Title>
            <div className="grid grid-cols-2 gap-2 text-xs text-gray-600">
              <span>内部检索：{result.timing.internalRetrievalMs ?? '-'} ms</span>
              <span>外部并行：{result.timing.externalParallelMs ?? '-'} ms</span>
              <span>生成阶段：{result.timing.generationMs ?? '-'} ms</span>
              <span>总耗时：{result.timing.totalMs ?? '-'} ms</span>
            </div>
          </div>
        </>
      ) : null}
    </div>
  )
}
