import { useMutation } from '@tanstack/react-query'
import { retrievalSearch, type RetrievalSearchParams, type RetrievalResult } from '@/api/retrieval'

/**
 * 检索测试 mutation
 * - 调用 /retrieval/search
 * - 失败时由拦截器统一提示
 */
export function useRetrievalSearch() {
  return useMutation<RetrievalResult, Error, RetrievalSearchParams>({
    mutationFn: (params) => retrievalSearch(params),
    onError: (err) => {
      console.error('检索失败:', err.message)
    }
  })
}
