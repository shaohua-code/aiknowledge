import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { message } from 'antd'
import {
  getProjectSettings,
  updateProjectSettings,
  type UpdateProjectSettingsPayload
} from '@/api/project-settings'

// TanStack Query 缓存键（带 projectId，确保切换项目时缓存隔离）
const PROJECT_SETTINGS_KEY = ['projectSettings'] as const

/**
 * 查询项目设置（含 settings）
 * @param projectId 项目 ID
 */
export function useProjectSettings(projectId?: string) {
  return useQuery({
    queryKey: [...PROJECT_SETTINGS_KEY, projectId],
    queryFn: () => getProjectSettings(projectId!),
    enabled: !!projectId
  })
}

/** 更新项目设置 mutation（PATCH 整体 settings） */
export function useUpdateProjectSettings(projectId?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: UpdateProjectSettingsPayload) => updateProjectSettings(projectId!, payload),
    onSuccess: () => {
      message.success('项目设置已保存')
      queryClient.invalidateQueries({ queryKey: [...PROJECT_SETTINGS_KEY, projectId] })
    }
  })
}
