import { useQuery } from '@tanstack/react-query'
import { listProjects } from '@/api/projects'
import { getOverviewStats, type OverviewStats } from '@/api/stats'

// TanStack Query 缓存键
const OVERVIEW_STATS_KEY = ['overviewStats'] as const
const OVERVIEW_PROJECTS_KEY = ['overviewProjects'] as const

/**
 * 聚合全局概览统计
 * - 优先调用 /v1/stats/overview
 * - 容错：接口未实现时基于 listProjects 估算（活跃数 = active 项目数，其余为 0）
 * - 这样保证即使后端聚合接口缺失，页面仍可展示项目总数与活跃数
 */
export function useOverviewStats() {
  // 查询项目列表（管理密钥接口，用于估算兜底）
  const projectsQuery = useQuery({
    queryKey: OVERVIEW_PROJECTS_KEY,
    queryFn: () => listProjects()
  })

  // 查询聚合统计（容错：失败返回 null）
  const statsQuery = useQuery<OverviewStats | null>({
    queryKey: OVERVIEW_STATS_KEY,
    queryFn: () => getOverviewStats()
  })

  // 兜底数据：聚合接口失败时，基于项目列表估算
  const fallbackStats: OverviewStats | null = (() => {
    if (!projectsQuery.data) return null
    const list = projectsQuery.data
    return {
      totalProjects: list.length,
      activeProjects: list.filter((p) => p.status === 'active').length,
      // 项目列表接口无法提供调用与异常统计，置 0
      todayCalls: 0,
      errorCount: 0
    }
  })()

  // 优先用聚合接口的数据，失败时使用兜底估算
  const stats = statsQuery.data ?? fallbackStats
  const loading = statsQuery.isLoading && projectsQuery.isLoading

  return {
    stats,
    loading,
    // 暴露项目列表给迷你列表组件（取最近 5 个，按创建时间倒序）
    recentProjects: (projectsQuery.data || [])
      .slice()
      .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
      .slice(0, 5),
    projectsLoading: projectsQuery.isLoading
  }
}
