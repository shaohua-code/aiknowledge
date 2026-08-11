// 统一接口响应格式类型定义（对齐后端 {success, requestId, data, meta, error}）

/** 后端响应 meta 信息 */
export interface ApiMeta {
  projectCode?: string
  apiVersion?: string
  generatedAt?: string
}

/** 后端响应错误对象 */
export interface ApiError {
  code: string
  message: string
  retryable: boolean
  details: Record<string, unknown>
}

/** 后端统一返回结构 */
export interface ApiResponse<T = unknown> {
  success: boolean
  requestId: string
  data: T
  meta?: ApiMeta
  error?: ApiError
}

/** 分页响应数据结构 */
export interface PageResult<T> {
  list: T[]
  total: number
  page: number
  pageSize: number
}

/** 分页查询参数 */
export interface PageParams {
  page: number
  pageSize: number
  keyword?: string
}
