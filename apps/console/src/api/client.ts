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

async function request<T>(config: AxiosRequestConfig): Promise<T> {
  try {
    const response = await http.request<ApiEnvelope<T>>(config)
    const envelope = response.data
    if (!envelope.success) {
      throw new PlatformError(envelope.error, {
        requestId: envelope.requestId,
        status: response.status
      })
    }
    return envelope.data
  } catch (error) {
    if (error instanceof PlatformError) throw error
    if (axios.isAxiosError<ApiEnvelope<unknown>>(error)) {
      const envelope = error.response?.data
      if (envelope && !envelope.success) {
        throw new PlatformError(envelope.error, {
          requestId: envelope.requestId,
          status: error.response?.status
        })
      }
      throw new PlatformError(
        {
          code: error.code ?? 'NETWORK_ERROR',
          title: '无法连接平台服务',
          message: error.message || '网络请求失败',
          retryable: true,
          suggestion: '请检查 API 服务和网络连接后重试',
          details: {}
        },
        { status: error.response?.status }
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

