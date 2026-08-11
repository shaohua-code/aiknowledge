import { Tag, Typography, Divider, Empty, Alert, Descriptions, Spin } from 'antd'
import type { ExecutionJobDetail } from '@/api/execution-logs'
import type { ResearchEvidence } from '@/api/research'
import dayjs from 'dayjs'

const { Title, Paragraph, Text } = Typography

interface JobDetailDrawerProps {
  /** 详情数据（加载中或无选中时为 null） */
  detail: ExecutionJobDetail | null
  /** 加载中状态 */
  loading: boolean
}

/** 状态文案与颜色 */
const STATUS_META: Record<string, { color: string; label: string }> = {
  pending: { color: 'default', label: '等待中' },
  running: { color: 'processing', label: '执行中' },
  succeeded: { color: 'green', label: '成功' },
  failed: { color: 'red', label: '失败' },
  timeout: { color: 'orange', label: '超时' }
}

/** 证据类型颜色 */
const EVIDENCE_COLOR: Record<string, string> = {
  internal: 'blue',
  web: 'cyan',
  tool: 'purple'
}

/**
 * 任务详情内容（用于 Drawer 内展示）
 * - 展示 answer、conclusions、evidence、timing、token、error
 * - 由父组件 Drawer 包裹，此处仅渲染内容
 */
export default function JobDetailDrawer({ detail, loading }: JobDetailDrawerProps) {
  // 加载态
  if (loading) {
    return (
      <div className="flex h-full w-full items-center justify-center py-10">
        <Spin tip="加载详情..." />
      </div>
    )
  }

  // 空态
  if (!detail) {
    return (
      <div className="flex h-full w-full items-center justify-center py-10">
        <Empty description="暂无详情" />
      </div>
    )
  }

  const statusMeta = STATUS_META[detail.status] || { color: 'default', label: detail.status }

  return (
    <div className="flex flex-col gap-3">
      {/* 基础信息 */}
      <Descriptions column={1} bordered size="small">
        <Descriptions.Item label="任务 ID">{detail.jobId}</Descriptions.Item>
        <Descriptions.Item label="请求 ID">{detail.requestId}</Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={statusMeta.color} className="!m-0">{statusMeta.label}</Tag>
          {detail.degraded && <Tag color="orange" className="!ml-2 !m-0">已降级</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="问题">{detail.question}</Descriptions.Item>
        <Descriptions.Item label="创建时间">
          {detail.createdAt ? dayjs(detail.createdAt).format('YYYY-MM-DD HH:mm:ss') : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="更新时间">
          {detail.updatedAt ? dayjs(detail.updatedAt).format('YYYY-MM-DD HH:mm:ss') : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="总耗时">
          {typeof detail.totalDurationMs === 'number' ? `${detail.totalDurationMs} ms` : '-'}
        </Descriptions.Item>
      </Descriptions>

      {/* 错误信息 */}
      {detail.error && (
        <Alert type="error" showIcon message="错误信息" description={detail.error} />
      )}

      {/* 降级提示 */}
      {detail.degraded && detail.result?.degradedReasons?.length ? (
        <Alert
          type="warning"
          showIcon
          message="本次研究部分能力降级"
          description={detail.result.degradedReasons.join('；')}
        />
      ) : null}

      {/* 研究结果 */}
      {detail.result ? (
        <>
          <Divider className="!my-2" />
          {/* 答案 */}
          <div className="rounded border border-blue-200 bg-blue-50 p-3">
            <Title level={5} className="!mb-2 !text-blue-700">研究答案</Title>
            <Paragraph className="!mb-0 !whitespace-pre-wrap !text-gray-800">
              {detail.result.answer || '-'}
            </Paragraph>
          </div>

          {/* 结论 */}
          {detail.result.conclusions?.length ? (
            <div>
              <Title level={5} className="!mb-2">结论</Title>
              <ul className="list-disc space-y-1 pl-6">
                {detail.result.conclusions.map((c, i) => (
                  <li key={i} className="text-sm text-gray-700">{c}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* 证据 */}
          {detail.result.evidence?.length ? (
            <div>
              <Title level={5} className="!mb-2">证据来源</Title>
              <div className="flex flex-col gap-2">
                {detail.result.evidence.map((e: ResearchEvidence, i) => (
                  <div key={i} className="rounded border border-gray-200 bg-gray-50 p-2">
                    <div className="mb-1 flex items-center gap-2">
                      <Tag color={EVIDENCE_COLOR[e.type] || 'default'} className="!m-0">{e.type}</Tag>
                      <Text strong className="!text-sm">{e.title}</Text>
                      {typeof e.score === 'number' && (
                        <Tag className="!m-0">score: {e.score}</Tag>
                      )}
                    </div>
                    <Paragraph className="!mb-1 !text-xs !text-gray-600">{e.snippet}</Paragraph>
                    {e.sourceUrl && (
                      <a href={e.sourceUrl} target="_blank" rel="noreferrer" className="!text-xs">
                        {e.sourceUrl}
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* 耗时与 Token 统计 */}
          {detail.result.timing ? (
            <Descriptions column={2} bordered size="small" title="耗时统计">
              <Descriptions.Item label="内部检索">
                {detail.result.timing.internalRetrievalMs ?? '-'} ms
              </Descriptions.Item>
              <Descriptions.Item label="外部并行">
                {detail.result.timing.externalParallelMs ?? '-'} ms
              </Descriptions.Item>
              <Descriptions.Item label="生成阶段">
                {detail.result.timing.generationMs ?? '-'} ms
              </Descriptions.Item>
              <Descriptions.Item label="总耗时">
                {detail.result.timing.totalMs ?? '-'} ms
              </Descriptions.Item>
            </Descriptions>
          ) : null}
        </>
      ) : (
        !detail.error && <Empty description="暂无研究结果" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </div>
  )
}
