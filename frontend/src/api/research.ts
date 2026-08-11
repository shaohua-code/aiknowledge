import request from './request'

// === 智能研究相关类型定义 ===

/** 研究输出类型：叙述 / JSON / 列表 */
export type ResearchOutputType = 'narrative' | 'json' | 'bullet_points'

/** 研究策略：仅知识库 / 知识库+Web / 知识库+工具 / 全量 */
export type ResearchStrategy = 'knowledge_only' | 'knowledge_web' | 'knowledge_tools' | 'full'

/** 证据类型：内部知识 / Web / 工具 */
export type EvidenceType = 'internal' | 'web' | 'tool'

/** 单条证据 */
export interface ResearchEvidence {
  type: EvidenceType
  title: string
  snippet: string
  sourceUrl?: string
  publishedAt?: string
  dataAsOf?: string
  score?: number
}

/** 研究耗时统计 */
export interface ResearchTiming {
  internalRetrievalMs?: number
  externalParallelMs?: number
  generationMs?: number
  totalMs?: number
}

/** 研究响应结果 */
export interface ResearchResult {
  taskId: string
  requestId: string
  answer: string
  conclusions?: string[]
  suggestedActions?: string[]
  evidence?: ResearchEvidence[]
  confidence?: number
  uncertainties?: string[]
  riskNotice?: string
  timing?: ResearchTiming
  degraded?: boolean
  degradedReasons?: string[]
}

/** 异步研究任务状态 */
export type ResearchJobStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'timeout'

/** 异步任务信息 */
export interface ResearchJob {
  jobId: string
  status: ResearchJobStatus
  statusUrl?: string
  result?: ResearchResult
  error?: string
  createdAt?: string
  updatedAt?: string
}

/** 同步研究请求入参 */
export interface ResearchRunPayload {
  question: string
  outputType: ResearchOutputType
  strategy: ResearchStrategy
  knowledgeBaseIds: string[]
  toolCodes?: string[]
  toolInputs?: Record<string, unknown>
  context?: Record<string, unknown> | null
}

/** 异步研究任务创建入参 */
export interface CreateResearchJobPayload extends ResearchRunPayload {}

/** 研究反馈入参 */
export interface ResearchFeedbackPayload {
  rating: number
  accepted: boolean
  comment?: string
  businessResultId?: string
}

// === 智能研究接口 ===

/**
 * 同步执行研究
 * - 直接返回完整研究结果（适合短耗时场景）
 */
export function researchRun(payload: ResearchRunPayload) {
  return request.post<ResearchResult>('/v1/research/run', payload)
}

/**
 * 创建异步研究任务
 * - 需要 Idempotency-Key 幂等头
 * - 返回 jobId / status / statusUrl
 */
export function createResearchJob(payload: CreateResearchJobPayload, idempotencyKey: string) {
  return request.post<ResearchJob>('/v1/research/jobs', payload, {
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 查询异步研究任务详情 */
export function getResearchJob(jobId: string) {
  return request.get<ResearchJob>(`/v1/research/jobs/${jobId}`)
}

/** 查询异步研究任务列表 */
export function listResearchJobs(params?: { status?: ResearchJobStatus }) {
  return request.get<ResearchJob[]>('/v1/research/jobs', { params })
}

/**
 * 提交研究反馈
 * @param requestId 研究请求 ID
 */
export function submitFeedback(requestId: string, payload: ResearchFeedbackPayload) {
  return request.post<void>(`/v1/research/${requestId}/feedback`, payload)
}
