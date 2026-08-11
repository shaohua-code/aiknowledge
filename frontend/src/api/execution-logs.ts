import request from './request'
import type { ResearchResult } from './research'

// === 执行记录相关类型定义 ===

/** 研究任务状态（与 research.ts 对齐） */
export type ExecutionJobStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'timeout'

/** 执行记录列表查询参数 */
export interface ListExecutionJobsParams {
  status?: ExecutionJobStatus
  keyword?: string
}

/** 执行记录列表项（任务概要） */
export interface ExecutionJobSummary {
  jobId: string
  /** 研究请求 ID */
  requestId: string
  /** 用户问题（截断展示） */
  question: string
  status: ExecutionJobStatus
  /** 是否降级 */
  degraded?: boolean
  /** 总耗时（毫秒） */
  totalDurationMs?: number
  createdAt?: string
  updatedAt?: string
}

/** 执行记录详情（含完整结果） */
export interface ExecutionJobDetail extends ExecutionJobSummary {
  /** 完整研究结果（answer、conclusions、evidence、timing 等） */
  result?: ResearchResult
  /** 错误信息 */
  error?: string
}

// === 执行记录接口 ===

/** 查询执行记录列表 */
export function listExecutionJobs(params?: ListExecutionJobsParams) {
  return request.get<ExecutionJobSummary[]>('/v1/research/jobs', { params })
}

/** 查询执行记录详情 */
export function getExecutionJob(jobId: string) {
  return request.get<ExecutionJobDetail>(`/v1/research/jobs/${jobId}`)
}
