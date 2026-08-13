import axios, { type AxiosRequestConfig } from 'axios'
import type { ApiEnvelope, ApiErrorBody } from '@aik/contracts'

export class PlatformError extends Error {
  readonly code: string
  readonly title: string
  readonly requestId?: string
  readonly retryable: boolean
  readonly suggestion?: string | null
  readonly details: Record<string, unknown>
  readonly status?: number

  constructor(
    error: ApiErrorBody,
    options: { requestId?: string; status?: number } = {}
  ) {
    super(error.message)
    this.name = 'PlatformError'
    this.code = error.code
    this.title = error.title
    this.requestId = options.requestId
    this.retryable = error.retryable
    this.suggestion = error.suggestion
    this.details = error.details
    this.status = options.status
  }
}

const http = axios.create({
  baseURL: '',
  timeout: 20_000,
  withCredentials: true
})

function isFailureEnvelope(value: unknown): value is Extract<ApiEnvelope<unknown>, { success: false }> {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Record<string, unknown>
  return candidate.success === false && Boolean(candidate.error) && typeof candidate.error === 'object'
}

function isSuccessEnvelope<T>(value: unknown): value is Extract<ApiEnvelope<T>, { success: true }> {
  return Boolean(value && typeof value === 'object' && (value as Record<string, unknown>).success === true)
}

export function parseHttpError(status: number, payload: unknown, fallbackMessage: string): PlatformError {
  if (isFailureEnvelope(payload)) {
    return new PlatformError(payload.error, { requestId: payload.requestId, status })
  }

  const detail =
    payload && typeof payload === 'object' && typeof (payload as Record<string, unknown>).detail === 'string'
      ? String((payload as Record<string, unknown>).detail)
      : undefined
  const statusMessages: Record<number, { title: string; message: string; suggestion?: string }> = {
    401: { title: '登录状态已失效', message: detail ?? '请重新登录后继续操作' },
    403: { title: '没有操作权限', message: detail ?? '当前账号无权执行此操作' },
    404: {
      title: '后端接口不存在',
      message: detail === 'Not Found' || !detail ? '当前后端没有提供这个接口' : detail,
      suggestion: '请重启最新后端服务后刷新页面'
    },
    422: { title: '提交内容不正确', message: detail ?? '请检查填写内容后重试' },
    500: { title: '后端服务异常', message: detail ?? '服务器处理请求时发生错误' }
  }
  const known = statusMessages[status]
  return new PlatformError(
    {
      code: `HTTP_${status}`,
      title: known?.title ?? '请求失败',
      message: known?.message ?? detail ?? fallbackMessage,
      retryable: status >= 500,
      suggestion: known?.suggestion ?? (status >= 500 ? '请稍后重试或查看后端日志' : undefined),
      details: { status }
    },
    { status }
  )
}

async function request<T>(config: AxiosRequestConfig): Promise<T> {
  try {
    const response = await http.request<ApiEnvelope<T>>(config)
    const envelope = response.data
    if (!isSuccessEnvelope<T>(envelope)) {
      throw parseHttpError(response.status, envelope, '后端返回了无法识别的数据格式')
    }
    return envelope.data
  } catch (error) {
    if (error instanceof PlatformError) throw error
    if (axios.isAxiosError<ApiEnvelope<unknown>>(error)) {
      if (error.response) {
        throw parseHttpError(
          error.response.status,
          error.response.data,
          error.message || '请求失败'
        )
      }
      throw new PlatformError(
        {
          code: error.code ?? 'NETWORK_ERROR',
          title: '无法连接平台服务',
          message: error.message || '网络请求失败',
          retryable: true,
          suggestion: '请检查 API 服务和网络连接后重试',
          details: {}
        }
      )
    }
    throw error
  }
}

export const apiClient = {
  get<T>(url: string, config?: AxiosRequestConfig) {
    return request<T>({ ...config, method: 'GET', url })
  },
  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig) {
    return request<T>({ ...config, method: 'POST', url, data })
  },
  patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig) {
    return request<T>({ ...config, method: 'PATCH', url, data })
  },
  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig) {
    return request<T>({ ...config, method: 'PUT', url, data })
  },
  delete<T>(url: string, config?: AxiosRequestConfig) {
    return request<T>({ ...config, method: 'DELETE', url })
  }
}

export function asPlatformError(error: unknown): PlatformError {
  if (error instanceof PlatformError) return error
  return new PlatformError({
    code: 'UNKNOWN_ERROR',
    title: '发生未知错误',
    message: error instanceof Error ? error.message : '未知错误',
    retryable: false,
    details: {}
  })
}
