import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { message } from 'antd'
import {
  listApiKeys,
  createApiKey,
  revokeApiKey,
  rotateApiKey,
  type CreateApiKeyPayload,
  type ListApiKeysParams,
  type ApiKey
} from '@/api/api-keys'

// TanStack Query 缓存键（带 projectId，确保切换项目时缓存隔离）
const API_KEYS_KEY = ['apiKeys'] as const

/**
 * 查询项目 API Key 列表
 * @param projectId 项目 ID（必填，路径参数）
 * @param params 查询参数（status）
 */
export function useApiKeys(projectId?: string, params?: ListApiKeysParams) {
  return useQuery({
    queryKey: [...API_KEYS_KEY, projectId, params],
    queryFn: () => listApiKeys(projectId!, params),
    enabled: !!projectId
  })
}

/** 创建 API Key mutation（返回明文密钥仅展示一次） */
export function useCreateApiKey(projectId?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CreateApiKeyPayload) => createApiKey(projectId!, payload),
    onSuccess: () => {
      message.success('API Key 已创建')
      queryClient.invalidateQueries({ queryKey: [...API_KEYS_KEY, projectId] })
    }
  })
}

/** 吊销（删除）API Key mutation */
export function useRevokeApiKey(projectId?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (keyId: string) => revokeApiKey(projectId!, keyId),
    onSuccess: () => {
      message.success('API Key 已停用')
      queryClient.invalidateQueries({ queryKey: [...API_KEYS_KEY, projectId] })
    }
  })
}

/** 轮换 API Key mutation（返回新明文密钥仅展示一次） */
export function useRotateApiKey(projectId?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (keyId: string) => rotateApiKey(projectId!, keyId),
    onSuccess: () => {
      message.success('API Key 已轮换')
      queryClient.invalidateQueries({ queryKey: [...API_KEYS_KEY, projectId] })
    }
  })
}

/** 便于组件直接消费明文密钥的别名类型 */
export type { ApiKey }
