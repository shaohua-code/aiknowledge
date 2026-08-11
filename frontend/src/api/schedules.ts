import request from './request'

// === 定时任务相关类型定义 ===

/** 定时任务类型（与后端枚举对齐） */
export type ScheduleTaskType =
  | 'KNOWLEDGE_SYNC'
  | 'CRAWL_SOURCE'
  | 'RESEARCH'
  | 'EMBEDDING_REFRESH'
  | 'WEB_MATERIAL_REVIEW'

/** 并发策略 */
export type ConcurrencyPolicy = 'ALLOW' | 'FORBID' | 'REPLACE'

/** 定时任务状态（运行态：启用/暂停） */
export type ScheduleEnabledStatus = 'enabled' | 'paused'

/** 定时任务实体 */
export interface Schedule {
  id: string
  name: string
  taskType: ScheduleTaskType
  cronExpression: string
  timezone: string
  /** 任务配置（JSON 字符串，前端展示与编辑使用） */
  config?: string
  concurrencyPolicy: ConcurrencyPolicy
  /** 超时秒数 */
  timeoutSeconds?: number
  /** 最大重试次数 */
  maxRetries?: number
  enabled: boolean
  /** 下次预计运行时间（ISO） */
  nextRunAt?: string
  /** 上次运行时间（ISO） */
  lastRunAt?: string
  createdAt: string
  updatedAt?: string
}

/** 创建定时任务入参 */
export interface CreateSchedulePayload {
  name: string
  taskType: ScheduleTaskType
  cronExpression: string
  timezone: string
  config?: string
  concurrencyPolicy: ConcurrencyPolicy
  timeoutSeconds?: number
  maxRetries?: number
}

/** 更新定时任务入参 */
export interface UpdateSchedulePayload {
  name?: string
  taskType?: ScheduleTaskType
  cronExpression?: string
  timezone?: string
  config?: string
  concurrencyPolicy?: ConcurrencyPolicy
  timeoutSeconds?: number
  maxRetries?: number
}

/** 定时任务列表查询参数 */
export interface ListSchedulesParams {
  taskType?: ScheduleTaskType
  enabled?: boolean
}

/** 定时任务运行记录实体 */
export interface ScheduleRun {
  id: string
  scheduleId: string
  /** 计划运行时间 */
  plannedAt: string
  /** 实际开始时间 */
  startedAt?: string
  /** 完成时间 */
  completedAt?: string
  status: ScheduleRunStatus
  /** 第几次尝试 */
  attempt?: number
  /** 耗时（秒） */
  duration?: number
  /** 结果摘要 */
  resultSummary?: string
  /** 错误信息 */
  error?: string
}

/** 定时任务运行记录状态 */
export type ScheduleRunStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'SUCCESS'
  | 'FAILED'
  | 'TIMEOUT'
  | 'SKIPPED'

// === 定时任务管理接口 ===

/** 查询定时任务列表 */
export function listSchedules(params?: ListSchedulesParams) {
  return request.get<Schedule[]>('/v1/schedules', { params })
}

/** 查询定时任务详情 */
export function getSchedule(id: string) {
  return request.get<Schedule>(`/v1/schedules/${id}`)
}

/** 创建定时任务 */
export function createSchedule(payload: CreateSchedulePayload) {
  return request.post<Schedule>('/v1/schedules', payload)
}

/** 更新定时任务 */
export function updateSchedule(id: string, payload: UpdateSchedulePayload) {
  return request.patch<Schedule>(`/v1/schedules/${id}`, payload)
}

/** 删除定时任务 */
export function deleteSchedule(id: string) {
  return request.delete<void>(`/v1/schedules/${id}`)
}

/** 暂停定时任务 */
export function pauseSchedule(id: string) {
  return request.post<void>(`/v1/schedules/${id}/pause`)
}

/** 恢复定时任务 */
export function resumeSchedule(id: string) {
  return request.post<void>(`/v1/schedules/${id}/resume`)
}

/** 手动触发一次运行 */
export function runSchedule(id: string) {
  return request.post<void>(`/v1/schedules/${id}/run`)
}

/** 查询某个定时任务的运行记录列表 */
export function listScheduleRuns(id: string) {
  return request.get<ScheduleRun[]>(`/v1/schedules/${id}/runs`)
}

/** 查询运行记录详情 */
export function getScheduleRun(runId: string) {
  return request.get<ScheduleRun>(`/v1/schedule-runs/${runId}`)
}
