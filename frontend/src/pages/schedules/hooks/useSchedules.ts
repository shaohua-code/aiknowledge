import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { message } from 'antd'
import {
  listSchedules,
  createSchedule,
  updateSchedule,
  deleteSchedule,
  pauseSchedule,
  resumeSchedule,
  runSchedule,
  type CreateSchedulePayload,
  type UpdateSchedulePayload,
  type ListSchedulesParams
} from '@/api/schedules'

// TanStack Query 缓存键
const SCHEDULES_KEY = ['schedules'] as const

/**
 * 获取定时任务列表
 * @param params 查询参数（taskType、enabled）
 */
export function useSchedules(params?: ListSchedulesParams) {
  return useQuery({
    queryKey: [...SCHEDULES_KEY, params],
    queryFn: () => listSchedules(params)
  })
}

/** 创建定时任务 mutation */
export function useCreateSchedule() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CreateSchedulePayload) => createSchedule(payload),
    onSuccess: () => {
      message.success('定时任务创建成功')
      queryClient.invalidateQueries({ queryKey: SCHEDULES_KEY })
    }
  })
}

/** 更新定时任务 mutation */
export function useUpdateSchedule() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: UpdateSchedulePayload }) => {
      return updateSchedule(id, payload)
    },
    onSuccess: () => {
      message.success('定时任务已更新')
      queryClient.invalidateQueries({ queryKey: SCHEDULES_KEY })
    }
  })
}

/** 删除定时任务 mutation */
export function useDeleteSchedule() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteSchedule(id),
    onSuccess: () => {
      message.success('定时任务已删除')
      queryClient.invalidateQueries({ queryKey: SCHEDULES_KEY })
    }
  })
}

/**
 * 切换定时任务状态（暂停/恢复）
 * - enabled=true 时调用 pause，反之调用 resume
 */
export function useToggleSchedule() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => {
      return enabled ? pauseSchedule(id) : resumeSchedule(id)
    },
    onSuccess: (_data, variables) => {
      message.success(variables.enabled ? '定时任务已暂停' : '定时任务已恢复')
      queryClient.invalidateQueries({ queryKey: SCHEDULES_KEY })
    }
  })
}

/** 手动触发定时任务运行一次 */
export function useRunSchedule() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => runSchedule(id),
    onSuccess: () => {
      message.success('已触发运行，请稍后查看运行记录')
      queryClient.invalidateQueries({ queryKey: SCHEDULES_KEY })
    }
  })
}
