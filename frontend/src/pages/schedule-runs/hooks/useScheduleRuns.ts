import { useQuery } from '@tanstack/react-query'
import { listScheduleRuns } from '@/api/schedules'

// TanStack Query 缓存键
const SCHEDULE_RUNS_KEY = ['scheduleRuns'] as const

/**
 * 获取某定时任务的运行记录列表
 * @param scheduleId 定时任务 ID（为空时不启用查询）
 */
export function useScheduleRuns(scheduleId?: string) {
  return useQuery({
    queryKey: [...SCHEDULE_RUNS_KEY, scheduleId],
    queryFn: () => listScheduleRuns(scheduleId as string),
    enabled: !!scheduleId
  })
}
