import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  listExecutionJobs,
  getExecutionJob,
  type ListExecutionJobsParams
} from '@/api/execution-logs'

// TanStack Query 缓存键（带 projectId，确保切换项目时缓存隔离）
const EXECUTION_JOBS_KEY = ['executionJobs'] as const

/**
 * 查询执行记录列表
 * @param projectId 项目 ID（用于缓存隔离，切换项目时自动重新加载）
 * @param params 查询参数（status、keyword）
 */
export function useExecutionJobs(projectId?: string, params?: ListExecutionJobsParams) {
  const queryClient = useQueryClient()

  // 切换项目时清空所有执行记录缓存，确保不串数据
  useEffect(() => {
    return () => {
      // 组件卸载或 projectId 变化时清空执行记录缓存
      queryClient.invalidateQueries({ queryKey: EXECUTION_JOBS_KEY })
    }
  }, [projectId, queryClient])

  return useQuery({
    queryKey: [...EXECUTION_JOBS_KEY, projectId, params],
    queryFn: () => listExecutionJobs(params),
    enabled: !!projectId
  })
}

/** 查询执行记录详情 */
export function useExecutionJobDetail(projectId?: string, jobId?: string) {
  return useQuery({
    queryKey: [...EXECUTION_JOBS_KEY, projectId, 'detail', jobId],
    queryFn: () => getExecutionJob(jobId!),
    enabled: !!projectId && !!jobId
  })
}
