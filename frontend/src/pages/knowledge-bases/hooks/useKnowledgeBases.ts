import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { message } from 'antd'
import {
  listKnowledgeBases,
  createKnowledgeBase,
  updateKnowledgeBase,
  deleteKnowledgeBase,
  disableKnowledgeBase,
  enableKnowledgeBase,
  type CreateKnowledgeBasePayload,
  type ListKnowledgeBasesParams
} from '@/api/knowledge-bases'

// TanStack Query 缓存键
const KNOWLEDGE_BASES_KEY = ['knowledgeBases'] as const

/**
 * 获取知识库列表查询
 * @param params 查询参数（status 过滤）
 */
export function useKnowledgeBases(params?: ListKnowledgeBasesParams) {
  return useQuery({
    queryKey: [...KNOWLEDGE_BASES_KEY, params],
    queryFn: () => listKnowledgeBases(params)
  })
}

/** 创建知识库 mutation，成功后刷新列表缓存 */
export function useCreateKnowledgeBase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CreateKnowledgeBasePayload) => createKnowledgeBase(payload),
    onSuccess: () => {
      message.success('知识库创建成功')
      queryClient.invalidateQueries({ queryKey: KNOWLEDGE_BASES_KEY })
    }
  })
}

/** 切换知识库状态（停用/启用） */
export function useToggleKnowledgeBase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ code, targetStatus }: { code: string; targetStatus: 'active' | 'disabled' }) => {
      return targetStatus === 'disabled'
        ? disableKnowledgeBase(code)
        : enableKnowledgeBase(code)
    },
    onSuccess: (_data, variables) => {
      message.success(variables.targetStatus === 'disabled' ? '知识库已停用' : '知识库已启用')
      queryClient.invalidateQueries({ queryKey: KNOWLEDGE_BASES_KEY })
    }
  })
}

/** 删除知识库（仅空知识库可删除） */
export function useDeleteKnowledgeBase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (code: string) => deleteKnowledgeBase(code),
    onSuccess: () => {
      message.success('知识库已删除')
      queryClient.invalidateQueries({ queryKey: KNOWLEDGE_BASES_KEY })
    }
  })
}

/** 更新知识库 mutation */
export function useUpdateKnowledgeBase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ code, payload }: { code: string; payload: Parameters<typeof updateKnowledgeBase>[1] }) => {
      return updateKnowledgeBase(code, payload)
    },
    onSuccess: () => {
      message.success('知识库已更新')
      queryClient.invalidateQueries({ queryKey: KNOWLEDGE_BASES_KEY })
    }
  })
}
