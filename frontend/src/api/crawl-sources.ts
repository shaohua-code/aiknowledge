import request from './request'

// === 采集源相关类型定义 ===

/** 采集源类型 */
export type CrawlSourceType = 'WEB' | 'SITEMAP' | 'RSS' | 'API'

/** 采集导入策略 */
export type CrawlImportPolicy = 'AUTO' | 'REVIEW' | 'DRAFT'

/** 采集源状态 */
export type CrawlSourceEnabledStatus = 'enabled' | 'paused'

/** 采集源实体 */
export interface CrawlSource {
  id: string
  code: string
  name: string
  type: CrawlSourceType
  /** 起始 URL 列表 */
  startUrls: string[]
  /** 允许抓取的域名 */
  allowedDomains: string[]
  /** 抽取规则（JSON 字符串） */
  extractRules?: string
  /** 导入策略 */
  importPolicy: CrawlImportPolicy
  /** 限制项（JSON 字符串，如 maxDepth、maxPages） */
  limits?: string
  /** 目标知识库 ID */
  destinationKnowledgeBaseId?: string
  enabled: boolean
  /** 上次运行时间 */
  lastRunAt?: string
  createdAt: string
  updatedAt?: string
}

/** 创建采集源入参 */
export interface CreateCrawlSourcePayload {
  code: string
  name: string
  type: CrawlSourceType
  startUrls: string[]
  allowedDomains: string[]
  extractRules?: string
  importPolicy: CrawlImportPolicy
  limits?: string
  destinationKnowledgeBaseId?: string
}

/** 更新采集源入参 */
export interface UpdateCrawlSourcePayload {
  name?: string
  type?: CrawlSourceType
  startUrls?: string[]
  allowedDomains?: string[]
  extractRules?: string
  importPolicy?: CrawlImportPolicy
  limits?: string
  destinationKnowledgeBaseId?: string
}

/** 采集源列表查询参数 */
export interface ListCrawlSourcesParams {
  type?: CrawlSourceType
  enabled?: boolean
}

/** 采集运行状态 */
export type CrawlRunStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'SUCCESS'
  | 'PARTIAL'
  | 'FAILED'
  | 'CANCELED'

/** 采集运行记录实体 */
export interface CrawlRun {
  id: string
  sourceId: string
  status: CrawlRunStatus
  startedAt?: string
  completedAt?: string
  /** 已发现页面数 */
  discoveredCount: number
  /** 成功抓取数 */
  successCount: number
  /** 重复数 */
  duplicateCount: number
  /** 失败数 */
  failedCount: number
  /** 已入库数 */
  importedCount: number
  error?: string
}

/** 采集页面状态 */
export type CrawlPageStatus =
  | 'DISCOVERED'
  | 'SUCCESS'
  | 'DUPLICATE'
  | 'FAILED'
  | 'IMPORTED'
  | 'PENDING_REVIEW'
  | 'APPROVED'
  | 'REJECTED'

/** 采集页面实体 */
export interface CrawlPage {
  id: string
  runId: string
  url: string
  title?: string
  status: CrawlPageStatus
  /** 抓取耗时（毫秒） */
  durationMs?: number
  /** HTTP 状态码 */
  httpStatus?: number
  /** 内容摘要 */
  contentSnippet?: string
  /** 错误信息 */
  error?: string
  discoveredAt?: string
  reviewedAt?: string
}

/** 网络资料池状态 */
export type WebMaterialStatus =
  | 'PENDING_REVIEW'
  | 'APPROVED'
  | 'REJECTED'
  | 'IMPORTED'

/** 网络资料实体 */
export interface WebMaterial {
  id: string
  title: string
  sourceUrl: string
  status: WebMaterialStatus
  contentSnippet?: string
  /** 关联采集源 ID */
  sourceId?: string
  reviewedAt?: string
  createdAt: string
}

/** 网络资料列表查询参数 */
export interface ListWebMaterialsParams {
  status?: WebMaterialStatus
}

// === 采集源管理接口 ===

/** 查询采集源列表 */
export function listCrawlSources(params?: ListCrawlSourcesParams) {
  return request.get<CrawlSource[]>('/v1/crawl-sources', { params })
}

/** 查询采集源详情 */
export function getCrawlSource(id: string) {
  return request.get<CrawlSource>(`/v1/crawl-sources/${id}`)
}

/** 创建采集源 */
export function createCrawlSource(payload: CreateCrawlSourcePayload) {
  return request.post<CrawlSource>('/v1/crawl-sources', payload)
}

/** 更新采集源 */
export function updateCrawlSource(id: string, payload: UpdateCrawlSourcePayload) {
  return request.patch<CrawlSource>(`/v1/crawl-sources/${id}`, payload)
}

/** 删除采集源 */
export function deleteCrawlSource(id: string) {
  return request.delete<void>(`/v1/crawl-sources/${id}`)
}

/** 暂停采集源 */
export function pauseCrawlSource(id: string) {
  return request.post<void>(`/v1/crawl-sources/${id}/pause`)
}

/** 恢复采集源 */
export function resumeCrawlSource(id: string) {
  return request.post<void>(`/v1/crawl-sources/${id}/resume`)
}

/** 手动触发一次采集运行 */
export function runCrawlSource(id: string) {
  return request.post<void>(`/v1/crawl-sources/${id}/runs`)
}

/** 查询采集源运行记录列表 */
export function listCrawlRunsBySource(id: string) {
  return request.get<CrawlRun[]>(`/v1/crawl-sources/${id}/runs`)
}

/** 查询采集运行详情 */
export function getCrawlRun(runId: string) {
  return request.get<CrawlRun>(`/v1/crawl-runs/${runId}`)
}

/** 查询采集运行下的页面列表 */
export function listCrawlPages(runId: string) {
  return request.get<CrawlPage[]>(`/v1/crawl-runs/${runId}/pages`)
}

/** 审核通过采集页面（触发入库） */
export function approveCrawlPage(pageId: string) {
  return request.post<void>(`/v1/crawl-pages/${pageId}/approve`)
}

/** 拒绝采集页面 */
export function rejectCrawlPage(pageId: string) {
  return request.post<void>(`/v1/crawl-pages/${pageId}/reject`)
}

// === 网络资料池接口 ===

/** 查询网络资料池列表 */
export function listWebMaterials(params?: ListWebMaterialsParams) {
  return request.get<WebMaterial[]>('/v1/web-materials', { params })
}

/** 采用网络资料（触发入库） */
export function approveWebMaterial(id: string) {
  return request.post<void>(`/v1/web-materials/${id}/approve`)
}

/** 拒绝网络资料 */
export function rejectWebMaterial(id: string) {
  return request.post<void>(`/v1/web-materials/${id}/reject`)
}
