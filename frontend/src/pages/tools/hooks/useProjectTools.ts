import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { message } from 'antd'
import {
  listTools,
  listProjectTools,
  createProjectTool,
  updateProjectTool,
  deleteProjectTool,
  testProjectTool,
  type UpsertProjectToolPayload,
  type ToolTestPayload
} from '@/api/tools'

// TanStack Query 缓存键
const TOOLS_KEY = ['tools'] as const
const PROJECT_TOOLS_KEY = ['projectTools'] as const

/** 查询全局工具列表（只读，用于选择） */
export function useTools() {
  return useQuery({
    queryKey: [...TOOLS_KEY],
    queryFn: () => listTools()
  })
}

/** 查询项目工具配置列表 */
export function useProjectTools() {
  return useQuery({
    queryKey: [...PROJECT_TOOLS_KEY],
    queryFn: () => listProjectTools()
  })
}

/** 创建项目工具配置 mutation */
export function useCreateProjectTool() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: UpsertProjectToolPayload & { toolCode: string }) => createProjectTool(payload),
    onSuccess: () => {
      message.success('工具已添加')
      queryClient.invalidateQueries({ queryKey: PROJECT_TOOLS_KEY })
    }
  })
}

/** 更新项目工具配置 mutation */
export function useUpdateProjectTool() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ toolCode, payload }: { toolCode: string; payload: UpsertProjectToolPayload }) => {
      return updateProjectTool(toolCode, payload)
    },
    onSuccess: () => {
      message.success('工具配置已更新')
      queryClient.invalidateQueries({ queryKey: PROJECT_TOOLS_KEY })
    }
  })
}

/** 删除项目工具配置 mutation */
export function useDeleteProjectTool() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (toolCode: string) => deleteProjectTool(toolCode),
    onSuccess: () => {
      message.success('工具已删除')
      queryClient.invalidateQueries({ queryKey: PROJECT_TOOLS_KEY })
    }
  })
}

/** 启停项目工具 mutation（复用 update） */
export function useToggleProjectTool() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ toolCode, enabled }: { toolCode: string; enabled: boolean }) => {
      return updateProjectTool(toolCode, { enabled })
    },
    onSuccess: (_data, variables) => {
      message.success(variables.enabled ? '工具已启用' : '工具已停用')
      queryClient.invalidateQueries({ queryKey: PROJECT_TOOLS_KEY })
    }
  })
}

/** 测试工具执行 mutation */
export function useTestProjectTool() {
  return useMutation({
    mutationFn: ({ toolCode, payload }: { toolCode: string; payload: ToolTestPayload }) => {
      return testProjectTool(toolCode, payload)
    }
  })
}
