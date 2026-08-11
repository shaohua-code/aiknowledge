import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { message } from 'antd'
import {
  listWebMaterials,
  approveWebMaterial,
  rejectWebMaterial,
  type ListWebMaterialsParams
} from '@/api/crawl-sources'

// TanStack Query 缓存键
const WEB_MATERIALS_KEY = ['webMaterials'] as const

/**
 * 获取网络资料池列表
 * @param params 查询参数（status）
 */
export function useWebMaterials(params?: ListWebMaterialsParams) {
  return useQuery({
    queryKey: [...WEB_MATERIALS_KEY, params],
    queryFn: () => listWebMaterials(params)
  })
}

/** 采用网络资料（触发入库） */
export function useApproveWebMaterial() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => approveWebMaterial(id),
    onSuccess: () => {
      message.success('已采用，已触发入库')
      queryClient.invalidateQueries({ queryKey: WEB_MATERIALS_KEY })
    }
  })
}

/** 拒绝网络资料 */
export function useRejectWebMaterial() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => rejectWebMaterial(id),
    onSuccess: () => {
      message.success('已拒绝该资料')
      queryClient.invalidateQueries({ queryKey: WEB_MATERIALS_KEY })
    }
  })
}
