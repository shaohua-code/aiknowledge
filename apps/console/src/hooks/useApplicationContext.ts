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

