import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { message } from 'antd'
import {
  listCrawlSources,
  createCrawlSource,
  updateCrawlSource,
  deleteCrawlSource,
  pauseCrawlSource,
  resumeCrawlSource,
  runCrawlSource,
  type CreateCrawlSourcePayload,
  type UpdateCrawlSourcePayload,
  type ListCrawlSourcesParams
} from '@/api/crawl-sources'

// TanStack Query 缓存键
const CRAWL_SOURCES_KEY = ['crawlSources'] as const

/**
 * 获取采集源列表
 * @param params 查询参数（type、enabled）
 */
export function useCrawlSources(params?: ListCrawlSourcesParams) {
  return useQuery({
    queryKey: [...CRAWL_SOURCES_KEY, params],
    queryFn: () => listCrawlSources(params)
  })
}

/** 创建采集源 mutation */
export function useCreateCrawlSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CreateCrawlSourcePayload) => createCrawlSource(payload),
    onSuccess: () => {
      message.success('采集源创建成功')
      queryClient.invalidateQueries({ queryKey: CRAWL_SOURCES_KEY })
    }
  })
}

/** 更新采集源 mutation */
export function useUpdateCrawlSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: UpdateCrawlSourcePayload }) => {
      return updateCrawlSource(id, payload)
    },
    onSuccess: () => {
      message.success('采集源已更新')
      queryClient.invalidateQueries({ queryKey: CRAWL_SOURCES_KEY })
    }
  })
}

/** 删除采集源 mutation */
export function useDeleteCrawlSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteCrawlSource(id),
    onSuccess: () => {
      message.success('采集源已删除')
      queryClient.invalidateQueries({ queryKey: CRAWL_SOURCES_KEY })
    }
  })
}

/**
 * 切换采集源状态（暂停/恢复）
 * - enabled=true 时调用 pause，反之调用 resume
 */
export function useToggleCrawlSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => {
      return enabled ? pauseCrawlSource(id) : resumeCrawlSource(id)
    },
    onSuccess: (_data, variables) => {
      message.success(variables.enabled ? '采集源已暂停' : '采集源已恢复')
      queryClient.invalidateQueries({ queryKey: CRAWL_SOURCES_KEY })
    }
  })
}

/** 手动触发一次采集运行 */
export function useRunCrawlSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => runCrawlSource(id),
    onSuccess: () => {
      message.success('已触发采集，请稍后查看采集记录')
      queryClient.invalidateQueries({ queryKey: CRAWL_SOURCES_KEY })
    }
  })
}
