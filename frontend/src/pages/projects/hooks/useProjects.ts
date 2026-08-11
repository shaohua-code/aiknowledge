import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { message } from 'antd'
import {
  listProjects,
  createProject,
  disableProject,
  enableProject,
  type CreateProjectPayload,
  type ListProjectsParams
} from '@/api/projects'

// TanStack Query 缓存键
const PROJECTS_KEY = ['projects'] as const

/**
 * 获取项目列表查询
 * @param params 查询参数（status 过滤）
 */
export function useProjects(params?: ListProjectsParams) {
  return useQuery({
    queryKey: [...PROJECTS_KEY, params],
    queryFn: () => listProjects(params)
  })
}

/** 创建项目 mutation，成功后刷新列表缓存 */
export function useCreateProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CreateProjectPayload) => createProject(payload),
    onSuccess: () => {
      message.success('项目创建成功')
      queryClient.invalidateQueries({ queryKey: PROJECTS_KEY })
    }
  })
}

/**
 * 切换项目状态（停用/启用）
 * @param projectId 项目 ID
 * @param targetStatus 目标状态
 */
export function useToggleProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ projectId, targetStatus }: { projectId: string; targetStatus: 'active' | 'disabled' }) => {
      return targetStatus === 'disabled'
        ? disableProject(projectId)
        : enableProject(projectId)
    },
    onSuccess: (_data, variables) => {
      message.success(variables.targetStatus === 'disabled' ? '项目已停用' : '项目已启用')
      queryClient.invalidateQueries({ queryKey: PROJECTS_KEY })
    }
  })
}
