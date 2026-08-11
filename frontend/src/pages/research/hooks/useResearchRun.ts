import { useMutation } from '@tanstack/react-query'
import { researchRun, type ResearchRunPayload, type ResearchResult } from '@/api/research'

/**
 * 同步研究 mutation
 * - 调用 /research/run 同步返回完整结果
 * - 失败时统一提示
 */
export function useResearchRun() {
  return useMutation<ResearchResult, Error, ResearchRunPayload>({
    mutationFn: (payload) => researchRun(payload),
    onError: (err) => {
      // 错误提示已由 axios 拦截器统一处理，这里兜底日志
      console.error('研究执行失败:', err.message)
    }
  })
}
