import axios, { type AxiosRequestConfig } from 'axios'
import type {
  ApiEnvelope,
  ApiErrorBody,
  OpenApiHttpMethod,
  OpenApiOperationBody,
  OpenApiOperationData,
  OpenApiOperationMultipartBody,
  OpenApiPathParameter,
  OpenApiOperationQuery,
  paths
} from '@aik/contracts'

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

/**
 * 将 OpenAPI 模板路径替换为已编码的路径参数。
 *
 * 仅由调用方提供参数值，模板本身只能来自生成的 paths，避免将任意 URL 当作受契约保护的路径。
 */
function resolveOperationPath<Path extends keyof paths>(
  path: Path,
  parameters: Record<OpenApiPathParameter<Path>, string>
): string {
  return String(path).replace(/\{([^}]+)\}/g, (placeholder, parameter: string) => {
    const value = parameters[parameter as OpenApiPathParameter<Path>]
    if (value === undefined) {
      throw new Error(`缺少 OpenAPI 路径参数：${parameter}`)
    }
    return encodeURIComponent(value)
  })
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
  },
  /**
   * 调用 OpenAPI 路径表中的无 JSON 请求体操作。
   *
   * 该入口只负责类型收敛；运行时仍复用既有 Axios、Cookie、超时和统一错误解析，
   * 避免为了生成客户端改变已验证的会话边界。
   */
  operation<Path extends keyof paths, Method extends OpenApiHttpMethod>(
    method: Method,
    path: Path,
    config?: AxiosRequestConfig
  ) {
    return request<OpenApiOperationData<Path, Method>>({ ...config, method: method.toUpperCase(), url: path })
  },
  /** 调用 OpenAPI 路径表中的 JSON 写操作，请求体与响应数据均由快照推导。 */
  operationWithBody<Path extends keyof paths, Method extends OpenApiHttpMethod>(
    method: Method,
    path: Path,
    data: OpenApiOperationBody<Path, Method>,
    config?: AxiosRequestConfig
  ) {
    return request<OpenApiOperationData<Path, Method>>({
      ...config,
      method: method.toUpperCase(),
      url: path,
      data
    })
  },
  /** 调用带路径参数、没有 JSON Body 的 OpenAPI 操作。 */
  operationAt<Path extends keyof paths, Method extends OpenApiHttpMethod>(
    method: Method,
    path: Path,
    parameters: Record<OpenApiPathParameter<Path>, string>,
    config?: AxiosRequestConfig
  ) {
    return request<OpenApiOperationData<Path, Method>>({
      ...config,
      method: method.toUpperCase(),
      url: resolveOperationPath(path, parameters)
    })
  },
  /** 调用同时具有路径参数和 JSON Body 的 OpenAPI 操作。 */
  operationAtWithBody<Path extends keyof paths, Method extends OpenApiHttpMethod>(
    method: Method,
    path: Path,
    parameters: Record<OpenApiPathParameter<Path>, string>,
    data: OpenApiOperationBody<Path, Method>,
    config?: AxiosRequestConfig
  ) {
    return request<OpenApiOperationData<Path, Method>>({
      ...config,
      method: method.toUpperCase(),
      url: resolveOperationPath(path, parameters),
      data
    })
  },
  /** 调用带 query 参数的静态路径操作，筛选字段直接由 OpenAPI 操作推导。 */
  operationWithQuery<Path extends keyof paths, Method extends OpenApiHttpMethod>(
    method: Method,
    path: Path,
    query: OpenApiOperationQuery<Path, Method>,
    config?: AxiosRequestConfig
  ) {
    return request<OpenApiOperationData<Path, Method>>({
      ...config,
      method: method.toUpperCase(),
      url: path,
      params: query
    })
  },
  /** 调用同时具有路径与 query 参数的操作，二者均受生成契约约束。 */
  operationAtWithQuery<Path extends keyof paths, Method extends OpenApiHttpMethod>(
    method: Method,
    path: Path,
    parameters: Record<OpenApiPathParameter<Path>, string>,
    query: OpenApiOperationQuery<Path, Method>,
    config?: AxiosRequestConfig
  ) {
    return request<OpenApiOperationData<Path, Method>>({
      ...config,
      method: method.toUpperCase(),
      url: resolveOperationPath(path, parameters),
      params: query
    })
  },
  /**
   * 调用 OpenAPI 声明为 multipart 的文件操作。
   *
   * FormData 的二进制构造仍留在页面适配层，但模板、方法和成功响应由生成契约限制；
   * 不手设 Content-Type，让浏览器附加正确 boundary。
   */
  operationAtWithFormData<Path extends keyof paths, Method extends OpenApiHttpMethod>(
    method: Method,
    path: Path,
    parameters: Record<OpenApiPathParameter<Path>, string>,
    data: OpenApiOperationMultipartBody<Path, Method> extends never ? never : FormData,
    config?: AxiosRequestConfig
  ) {
    return request<OpenApiOperationData<Path, Method>>({
      ...config,
      method: method.toUpperCase(),
      url: resolveOperationPath(path, parameters),
      data
    })
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
