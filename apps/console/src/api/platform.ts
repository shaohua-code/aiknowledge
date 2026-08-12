import type {
  AnswerProfile,
  AnswerResult,
  ApiKeyCreated,
  ApiKeyRecord,
  Application,
  HealthStatus,
  IngestionRun,
  KnowledgeCollection,
  KnowledgeDocument,
  OperationSummary,
  RequestTrace,
  RequestTraceDetail,
  RetrievalProfile
} from '@aik/contracts'
import { apiClient } from './client'

export const sessionApi = {
  // 会话探测属于首屏关键路径。服务离线时应尽快进入可操作的登录页，
  // 而不是让用户面对全屏骨架直到全局 20 秒超时。
  me: () =>
    apiClient.get<{ email: string; role: string }>('/control/v1/session/me', {
      timeout: 3_000
    }),
  login: (email: string, password: string) =>
    apiClient.post<{ email: string; role: string }>('/control/v1/session/login', {
      email,
      password
    }),
  logout: () => apiClient.delete<{ loggedOut: boolean }>('/control/v1/session')
}

export const applicationApi = {
  list: () => apiClient.get<Application[]>('/control/v1/applications'),
  create: (payload: {
    code: string
    name: string
    description?: string
    applicationType: string
  }) => apiClient.post<Application>('/control/v1/applications', payload),
  update: (id: string, payload: Partial<Pick<Application, 'name' | 'description' | 'status'>>) =>
    apiClient.patch<Application>(`/control/v1/applications/${id}`, payload)
}

function environmentBase(applicationId: string, environmentId: string) {
  return `/control/v1/applications/${applicationId}/environments/${environmentId}`
}

export const knowledgeApi = {
  collections: (applicationId: string, environmentId: string) =>
    apiClient.get<KnowledgeCollection[]>(`${environmentBase(applicationId, environmentId)}/collections`),
  createCollection: (
    applicationId: string,
    environmentId: string,
    payload: { code: string; name: string; description?: string }
  ) =>
    apiClient.post<KnowledgeCollection>(
      `${environmentBase(applicationId, environmentId)}/collections`,
      payload
    ),
  documents: (applicationId: string, environmentId: string, collectionId: string) =>
    apiClient.get<KnowledgeDocument[]>(
      `${environmentBase(applicationId, environmentId)}/collections/${collectionId}/documents`
    ),
  upload: (
    applicationId: string,
    environmentId: string,
    collectionId: string,
    file: File
  ) => {
    const formData = new FormData()
    formData.append('file', file)
    return apiClient.post<{ document: KnowledgeDocument; ingestionRun: IngestionRun }>(
      `${environmentBase(applicationId, environmentId)}/collections/${collectionId}/documents/upload`,
      formData
    )
  },
  createText: (
    applicationId: string,
    environmentId: string,
    collectionId: string,
    payload: { title: string; content: string }
  ) =>
    apiClient.post<{ document: KnowledgeDocument; ingestionRun: IngestionRun }>(
      `${environmentBase(applicationId, environmentId)}/collections/${collectionId}/documents/text`,
      payload
    ),
  createRemote: (
    applicationId: string,
    environmentId: string,
    collectionId: string,
    payload: { title: string; url: string; sourceType: 'web' | 'api' }
  ) =>
    apiClient.post<{ document: KnowledgeDocument; ingestionRun: IngestionRun }>(
      `${environmentBase(applicationId, environmentId)}/collections/${collectionId}/documents/remote`,
      payload
    ),
  retryRun: (applicationId: string, environmentId: string, runId: string) =>
    apiClient.post<IngestionRun>(
      `${environmentBase(applicationId, environmentId)}/ingestion-runs/${runId}/retry`,
      {}
    ),
  archiveDocument: (applicationId: string, environmentId: string, documentId: string) =>
    apiClient.delete<{ archived: boolean }>(
      `${environmentBase(applicationId, environmentId)}/documents/${documentId}`
    ),
  archiveCollection: (applicationId: string, environmentId: string, collectionId: string) =>
    apiClient.delete<{ archived: boolean }>(
      `${environmentBase(applicationId, environmentId)}/collections/${collectionId}`
    ),
  runs: (applicationId: string, environmentId: string) =>
    apiClient.get<IngestionRun[]>(`${environmentBase(applicationId, environmentId)}/ingestion-runs`)
}

export const intelligenceApi = {
  retrievalProfiles: (applicationId: string, environmentId: string) =>
    apiClient.get<RetrievalProfile[]>(
      `${environmentBase(applicationId, environmentId)}/retrieval-profiles`
    ),
  createRetrievalProfile: (
    applicationId: string,
    environmentId: string,
    payload: Record<string, unknown>
  ) =>
    apiClient.post<RetrievalProfile>(
      `${environmentBase(applicationId, environmentId)}/retrieval-profiles`,
      payload
    ),
  answerProfiles: (applicationId: string, environmentId: string) =>
    apiClient.get<AnswerProfile[]>(
      `${environmentBase(applicationId, environmentId)}/answer-profiles`
    ),
  createAnswerProfile: (
    applicationId: string,
    environmentId: string,
    payload: Record<string, unknown>
  ) =>
    apiClient.post<AnswerProfile>(
      `${environmentBase(applicationId, environmentId)}/answer-profiles`,
      payload
    ),
  answer: (
    apiKey: string,
    payload: {
      profile: string
      query: string
      inputs: Record<string, unknown>
      options: { includeCitations: boolean; includeEvidence: boolean }
    }
  ) =>
    apiClient.post<AnswerResult>('/runtime/v1/answer', payload, {
      headers: { Authorization: `Bearer ${apiKey}` }
    })
}

export const developerApi = {
  keys: (applicationId: string, environmentId: string) =>
    apiClient.get<ApiKeyRecord[]>(
      `${environmentBase(applicationId, environmentId)}/api-keys`
    ),
  createKey: (
    applicationId: string,
    environmentId: string,
    payload: { name: string; scopes: string[] }
  ) =>
    apiClient.post<ApiKeyCreated>(
      `${environmentBase(applicationId, environmentId)}/api-keys`,
      payload
    ),
  revokeKey: (applicationId: string, environmentId: string, keyId: string) =>
    apiClient.delete<{ revoked: boolean }>(
      `${environmentBase(applicationId, environmentId)}/api-keys/${keyId}`
    )
}

export const operationApi = {
  summary: (applicationId: string, environmentId: string) =>
    apiClient.get<OperationSummary>(
      `${environmentBase(applicationId, environmentId)}/operations/summary`
    ),
  traces: (applicationId: string, environmentId: string) =>
    apiClient.get<RequestTrace[]>(
      `${environmentBase(applicationId, environmentId)}/operations/traces`
    ),
  trace: (applicationId: string, environmentId: string, requestId: string) =>
    apiClient.get<RequestTraceDetail>(
      `${environmentBase(applicationId, environmentId)}/operations/traces/${requestId}`
    )
}

export async function getHealth(): Promise<HealthStatus> {
  const response = await fetch('/ready', {
    credentials: 'include',
    signal: AbortSignal.timeout(4_000)
  })
  const payload = (await response.json()) as HealthStatus
  if (!response.ok && !payload.checks) throw new Error('健康检查不可用')
  return payload
}
