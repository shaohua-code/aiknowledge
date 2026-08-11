import axios, {
  type AxiosInstance,
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig
} from 'axios'
import { message } from 'antd'
import type { ApiResponse } from '@/types/api'

// localStorage 中两套密钥的存储键名
const MANAGEMENT_KEY_STORAGE = 'management_api_key'
const CURRENT_API_KEY_STORAGE = 'current_api_key'
const CURRENT_PROJECT_STORAGE = 'current_project'

/** 读取管理密钥（用于项目管理接口） */
export function getManagementKey(): string | null {
  return localStorage.getItem(MANAGEMENT_KEY_STORAGE)
}

/** 写入管理密钥 */
export function setManagementKey(key: string): void {
  localStorage.setItem(MANAGEMENT_KEY_STORAGE, key)
}

/** 读取当前项目 API Key（用于业务接口） */
export function getCurrentProjectApiKey(): string | null {
  return localStorage.getItem(CURRENT_API_KEY_STORAGE)
}

/** 写入当前项目 API Key */
export function setCurrentProjectApiKey(key: string): void {
  localStorage.setItem(CURRENT_API_KEY_STORAGE, key)
}

/** 清除当前项目 API Key */
export function clearCurrentProjectApiKey(): void {
  localStorage.removeItem(CURRENT_API_KEY_STORAGE)
}

/** 读取当前项目信息（用于注入 X-Project-Code） */
function getCurrentProjectCode(): string | null {
  try {
    const raw = localStorage.getItem(CURRENT_PROJECT_STORAGE)
    if (!raw) return null
    const project = JSON.parse(raw) as { code?: string }
    return project.code ?? null
  } catch {
    return null
  }
}

// 创建 axios 实例，统一配置 baseURL / 超时 / 请求头
const http: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 15000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器：根据请求路径自动注入两套 Token 与项目编码头
// - 项目管理接口（/v1/projects）使用 management_api_key
// - 业务接口（知识库等）使用 current_api_key + X-Project-Code
http.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const url = config.url ?? ''
    // 平台统计与项目管理同属管理控制台，必须使用管理密钥而非当前项目 Key。
    const isManagementApi = url.includes('/v1/projects') || url === '/v1/stats/overview'

    if (isManagementApi) {
      // 管理密钥保护接口
      const managementKey = getManagementKey()
      if (managementKey) {
        config.headers.Authorization = `Bearer ${managementKey}`
      }
    } else {
      // 业务接口使用项目 API Key
      const apiKey = getCurrentProjectApiKey()
      if (apiKey) {
        config.headers.Authorization = `Bearer ${apiKey}`
      }
      // 注入当前项目编码（业务接口必需）
      const projectCode = getCurrentProjectCode()
      if (projectCode) {
        config.headers['X-Project-Code'] = projectCode
      }
    }
    return config
  },
  (error) => Promise.reject(error)
)

/**
 * 将 HTTP 响应转换为业务数据。
 *
 * Axios 拦截器无法改变 AxiosInstance 的泛型返回类型，过去虽然在运行时剥离了
 * `ApiResponse`，但 TypeScript 仍把调用结果推断为 AxiosResponse，污染所有查询与表单。
 * 此处在唯一出口显式完成转换，使 API 层始终返回 `Promise<T>`。
 */
async function unwrap<T>(request: Promise<AxiosResponse<ApiResponse<T>>>): Promise<T> {
  try {
    const response = await request
    const result = response.data
    if (!result.success) {
      const errorMessage = result.error?.message || '请求失败'
      message.error(errorMessage)
      throw new Error(errorMessage)
    }
    return result.data
  } catch (error) {
    // 业务错误已在上方提示；HTTP 失败则尝试读取后端统一错误结构。
    if (axios.isAxiosError<ApiResponse>(error)) {
      const errorMessage = error.response?.data?.error?.message || error.message || '网络异常'
      message.error(errorMessage)
    }
    throw error
  }
}

/**
 * 平台 API 客户端：统一返回业务 payload，而非泄漏 AxiosResponse 给页面层。
 * 页面、TanStack Query 与业务 hooks 因此可以直接使用精确的领域类型。
 */
const request = {
  get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return unwrap(http.get<ApiResponse<T>>(url, config))
  },
  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return unwrap(http.post<ApiResponse<T>>(url, data, config))
  },
  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return unwrap(http.put<ApiResponse<T>>(url, data, config))
  },
  patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return unwrap(http.patch<ApiResponse<T>>(url, data, config))
  },
  delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return unwrap(http.delete<ApiResponse<T>>(url, config))
  }
}

export default request
