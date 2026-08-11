import { useMutation } from '@tanstack/react-query'
import { message } from 'antd'
import { submitFeedback, type ResearchFeedbackPayload } from '@/api/research'

/**
 * 研究反馈 mutation
 * - 入参：requestId + payload
 */
export function useResearchFeedback() {
  return useMutation<void, Error, { requestId: string; payload: ResearchFeedbackPayload }>({
    mutationFn: ({ requestId, payload }) => submitFeedback(requestId, payload),
    onSuccess: () => {
      message.success('反馈已提交，感谢您的评价')
    }
  })
}
