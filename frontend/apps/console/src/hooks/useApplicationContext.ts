// 从嵌套路由和真实应用列表解析当前应用环境，页面不自行拼接隔离上下文。
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { applicationApi } from '@/api/platform'

export function useApplicationContext() {
  const { applicationId = '', environmentId = '' } = useParams()
  const query = useQuery({ queryKey: ['applications'], queryFn: applicationApi.list })
  const application = query.data?.find((item) => item.id === applicationId)
  const environment = application?.environments.find((item) => item.id === environmentId)
  return { applicationId, environmentId, application, environment, applicationsQuery: query }
}
