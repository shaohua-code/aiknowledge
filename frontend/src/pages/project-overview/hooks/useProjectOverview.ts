import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { listKnowledgeBases, type KnowledgeBase } from '@/api/knowledge-bases'
import { listExecutionJobs, type ExecutionJobSummary } from '@/api/execution-logs'
import { getProjectOverviewStats, type ProjectOverviewStats } from '@/api/stats'

// TanStack Query 缓存键（带 projectId，确保切换项目时缓存隔离）
const PROJECT_OVERVIEW_STATS_KEY = ['projectOverviewStats'] as const
const PROJECT_OVERVIEW_KBS_KEY = ['projectOverviewKbs'] as const
const PROJECT_OVERVIEW_JOBS_KEY = ['projectOverviewJobs'] as const

/**
 * 聚合项目概览数据
 * - 优先调用 /v1/stats/project 获取服务端按 ProjectContext 隔离的聚合统计
 * - 容错：聚合接口失败时分别调用 knowledge-bases 与 research/jobs 拼装数据
 *   - 知识库数量 = 知识库列表长度
 *   - 文档总数 = 各知识库 documentCount 之和
 *   - 今日调用 = 今日 createdAt 的任务数
 *   - 平均耗时 = 任务 totalDurationMs 平均值
 * - 这样即使后端聚合接口缺失，前端仍可展示完整概览
 */
export function useProjectOverview() {
  const { projectId = '' } = useParams()

  // 查询聚合统计（容错：失败返回 null）
  const statsQuery = useQuery<ProjectOverviewStats | null>({
    queryKey: [...PROJECT_OVERVIEW_STATS_KEY, projectId],
    queryFn: () => getProjectOverviewStats(),
    enabled: !!projectId
  })

  // 知识库列表（用于兜底拼装统计 + 迷你列表展示）
  const kbsQuery = useQuery<KnowledgeBase[]>({
    queryKey: [...PROJECT_OVERVIEW_KBS_KEY, projectId],
    queryFn: () => listKnowledgeBases(),
    enabled: !!projectId
  })

  // 最近研究任务（用于兜底拼装统计 + 最近任务列表展示）
  // limit=5 取最近 5 条
  const jobsQuery = useQuery<ExecutionJobSummary[]>({
    queryKey: [...PROJECT_OVERVIEW_JOBS_KEY, projectId],
    queryFn: () => listExecutionJobs(),
    enabled: !!projectId
  })

  // 兜底统计：聚合接口失败时基于知识库与任务列表拼装
  const fallbackStats: ProjectOverviewStats | null = (() => {
    // 聚合接口已有数据则不计算兜底
    if (statsQuery.data) return statsQuery.data
    // 知识库未加载完成则不计算
    if (!kbsQuery.data || !jobsQuery.data) return null

    const kbList = kbsQuery.data
    const jobList = jobsQuery.data

    // 今日调用：createdAt 落在今天的任务数
    const today = new Date()
    const todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
    const todayCalls = jobList.filter((j) => {
      if (!j.createdAt) return false
      return new Date(j.createdAt).getTime() >= todayStart
    }).length

    // 平均耗时：仅统计有 totalDurationMs 的任务
    const durations = jobList.filter(
      (j) => j.totalDurationMs !== undefined && j.totalDurationMs !== null
    )
    const avgDurationMs =
      durations.length > 0
        ? Math.round(
            durations.reduce((sum, j) => sum + (j.totalDurationMs as number), 0) / durations.length
          )
        : 0

    return {
      knowledgeBaseCount: kbList.length,
      totalDocuments: kbList.reduce((sum, kb) => sum + (kb.documentCount ?? 0), 0),
      todayCalls,
      avgDurationMs
    }
  })()

  // 最近 5 条任务（按 createdAt 倒序）
  const recentJobs = (jobsQuery.data || [])
    .slice()
    .sort((a, b) => {
      const ta = a.createdAt ? new Date(a.createdAt).getTime() : 0
      const tb = b.createdAt ? new Date(b.createdAt).getTime() : 0
      return tb - ta
    })
    .slice(0, 5)

  return {
    stats: fallbackStats,
    statsLoading: statsQuery.isLoading && !fallbackStats,
    knowledgeBases: kbsQuery.data,
    kbsLoading: kbsQuery.isLoading,
    recentJobs,
    jobsLoading: jobsQuery.isLoading
  }
}
