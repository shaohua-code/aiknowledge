import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { message } from 'antd'
import {
  listCrawlRunsBySource,
  listCrawlPages,
  approveCrawlPage,
  rejectCrawlPage
} from '@/api/crawl-sources'

// TanStack Query 缓存键
const CRAWL_RUNS_KEY = ['crawlRuns'] as const
const CRAWL_PAGES_KEY = ['crawlPages'] as const

/**
 * 获取某采集源的运行记录列表
 * @param sourceId 采集源 ID（为空时不启用查询）
 */
export function useCrawlRunsBySource(sourceId?: string) {
  return useQuery({
    queryKey: [...CRAWL_RUNS_KEY, sourceId],
    queryFn: () => listCrawlRunsBySource(sourceId as string),
    enabled: !!sourceId
  })
}

/**
 * 获取某次采集运行的页面列表
 * @param runId 采集运行 ID（为空时不启用查询）
 */
export function useCrawlPages(runId?: string) {
  return useQuery({
    queryKey: [...CRAWL_PAGES_KEY, runId],
    queryFn: () => listCrawlPages(runId as string),
    enabled: !!runId
  })
}

/** 审核通过采集页面（触发入库） */
export function useApproveCrawlPage(runId?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (pageId: string) => approveCrawlPage(pageId),
    onSuccess: () => {
      message.success('已通过审核，已触发入库')
      // 刷新页面列表与运行列表（计数可能变化）
      queryClient.invalidateQueries({ queryKey: CRAWL_PAGES_KEY })
      if (runId) queryClient.invalidateQueries({ queryKey: [...CRAWL_RUNS_KEY] })
    }
  })
}

/** 拒绝采集页面 */
export function useRejectCrawlPage(runId?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (pageId: string) => rejectCrawlPage(pageId),
    onSuccess: () => {
      message.success('已拒绝该页面')
      queryClient.invalidateQueries({ queryKey: CRAWL_PAGES_KEY })
      if (runId) queryClient.invalidateQueries({ queryKey: [...CRAWL_RUNS_KEY] })
    }
  })
}
