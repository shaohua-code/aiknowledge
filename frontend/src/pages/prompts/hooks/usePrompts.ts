import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { message } from 'antd'
import {
  listPrompts,
  getActivePrompt,
  getPrompt,
  createPrompt,
  updatePrompt,
  activatePrompt,
  deletePrompt,
  type CreatePromptPayload,
  type UpdatePromptPayload
} from '@/api/prompts'

// TanStack Query 缓存键
const PROMPTS_KEY = ['prompts'] as const

/** 查询全部提示词版本列表 */
export function usePrompts() {
  return useQuery({
    queryKey: [...PROMPTS_KEY],
    queryFn: () => listPrompts()
  })
}

/** 查询当前激活版本 */
export function useActivePrompt() {
  return useQuery({
    queryKey: [...PROMPTS_KEY, 'active'],
    queryFn: () => getActivePrompt()
  })
}

/** 查询提示词版本详情 */
export function usePrompt(versionId?: string) {
  return useQuery({
    queryKey: [...PROMPTS_KEY, versionId],
    queryFn: () => getPrompt(versionId!),
    enabled: !!versionId
  })
}

/** 创建新版本 mutation */
export function useCreatePrompt() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CreatePromptPayload) => createPrompt(payload),
    onSuccess: () => {
      message.success('新版本已创建')
      queryClient.invalidateQueries({ queryKey: PROMPTS_KEY })
    }
  })
}

/** 更新版本 mutation（仅非 active 可改） */
export function useUpdatePrompt() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ versionId, payload }: { versionId: string; payload: UpdatePromptPayload }) => {
      return updatePrompt(versionId, payload)
    },
    onSuccess: () => {
      message.success('版本已更新')
      queryClient.invalidateQueries({ queryKey: PROMPTS_KEY })
    }
  })
}

/** 激活版本 mutation */
export function useActivatePrompt() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (versionId: string) => activatePrompt(versionId),
    onSuccess: () => {
      message.success('版本已激活')
      queryClient.invalidateQueries({ queryKey: PROMPTS_KEY })
    }
  })
}

/** 删除版本 mutation（仅非 active 可删） */
export function useDeletePrompt() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (versionId: string) => deletePrompt(versionId),
    onSuccess: () => {
      message.success('版本已删除')
      queryClient.invalidateQueries({ queryKey: PROMPTS_KEY })
    }
  })
}
