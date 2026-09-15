import type {
  AnswerProfileCreateInput,
  AnswerDebugRequestInput,
  AnswerProfileRevisionDraft,
  ApiKeyCreateInput,
  ApplicationCreateInput,
  ApplicationUpdateInput,
  CollectionCreateInput,
  EvaluationCaseCreateInput,
  EvaluationRunCreateInput,
  EvaluationSetCreateInput,
  ModelBindingUpdate,
  ModelCapability,
  ModelCatalogEntryCreate,
  ModelCatalogEntryUpdate,
  ModelConfigurationUpdate,
  OpenApiOperationResponse,
  RemoteSourcePayload,
  RetrievalProfileCreateInput,
  RetrievalProfileRevisionDraft,
  TextDocumentCreateInput,
  TextRevisionCreateInput
} from '@aik/contracts'
import { apiClient } from './client'

// 浏览器每次发起新的异步写操作都生成独立幂等键，手动重试会得到新的业务意图。
function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `aik-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function idempotencyConfig() {
  // 只发送请求键，不把文件内容或临时输入复制到请求头和日志。
  return { headers: { 'Idempotency-Key': newIdempotencyKey() } }
}

export const sessionApi = {
  // 会话探测属于首屏关键路径。服务离线时应尽快进入可操作的登录页，
  // 而不是让用户面对全屏骨架直到全局 20 秒超时。
  me: () =>
    apiClient.operation('get', '/control/v1/session/me', {
      timeout: 3_000
    }),
  login: (email: string, password: string) =>
    apiClient.operationWithBody('post', '/control/v1/session/login', { email, password }),
  logout: () => apiClient.operation('delete', '/control/v1/session')
}

export const applicationApi = {
  list: () => apiClient.operation('get', '/control/v1/applications'),
  create: (payload: ApplicationCreateInput) =>
    apiClient.operationWithBody('post', '/control/v1/applications', payload),
  update: (id: string, payload: ApplicationUpdateInput) =>
    apiClient.operationAtWithBody('patch', '/control/v1/applications/{application_id}', {
      application_id: id
    }, payload)
}

export const modelConfigurationApi = {
  get: () => apiClient.operation('get', '/control/v1/platform/model-configuration'),
  update: (payload: ModelConfigurationUpdate) =>
    apiClient.operationWithBody('put', '/control/v1/platform/model-configuration', payload),
  test: () =>
    apiClient.operation('post', '/control/v1/platform/model-configuration/test')
}

export const modelCatalogApi = {
  list: (capability?: ModelCapability) =>
    apiClient.operationWithQuery('get', '/control/v1/model-catalog', capability ? { capability } : {}),
  create: (payload: ModelCatalogEntryCreate) =>
    apiClient.operationWithBody('post', '/control/v1/model-catalog', payload),
  update: (code: string, payload: ModelCatalogEntryUpdate) =>
    apiClient.operationAtWithBody('put', '/control/v1/model-catalog/{code}', { code }, payload),
  remove: (code: string) =>
    apiClient.operationAt('delete', '/control/v1/model-catalog/{code}', { code }),
  test: (code: string) =>
    apiClient.operationAt('post', '/control/v1/model-catalog/{code}/test', { code })
}

export const modelBindingApi = {
  get: (applicationId: string) =>
    apiClient.operationAt('get', '/control/v1/applications/{application_id}/model-binding', {
      application_id: applicationId
    }),
  // 绑定更新为全量写：三个能力字段都需携带，传 null 表示解除绑定
  update: (applicationId: string, payload: ModelBindingUpdate) =>
    apiClient.operationAtWithBody(
      'put',
      '/control/v1/applications/{application_id}/model-binding',
      { application_id: applicationId },
      payload
    )
}

export const environmentModelBindingApi = {
  get: (applicationId: string, environmentId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/model-binding',
      { application_id: applicationId, environment_id: environmentId }
    ),
  // 环境写入同样是全量能力快照，空值只移除该环境覆盖。
  update: (applicationId: string, environmentId: string, payload: ModelBindingUpdate) =>
    apiClient.operationAtWithBody(
      'put',
      '/control/v1/applications/{application_id}/environments/{environment_id}/model-binding',
      { application_id: applicationId, environment_id: environmentId },
      payload
    )
}

export const platformModelDefaultApi = {
  get: () => apiClient.operation('get', '/control/v1/platform/model-defaults'),
  update: (payload: ModelBindingUpdate) =>
    apiClient.operationWithBody('put', '/control/v1/platform/model-defaults', payload)
}

export const knowledgeApi = {
  collections: (applicationId: string, environmentId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/collections',
      { application_id: applicationId, environment_id: environmentId }
    ),
  createCollection: (
    applicationId: string,
    environmentId: string,
    payload: CollectionCreateInput
  ) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/collections',
      { application_id: applicationId, environment_id: environmentId },
      payload
    ),
  documents: (applicationId: string, environmentId: string, collectionId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/collections/{collection_id}/documents',
      { application_id: applicationId, environment_id: environmentId, collection_id: collectionId }
    ),
  revisions: (applicationId: string, environmentId: string, documentId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/versions',
      { application_id: applicationId, environment_id: environmentId, document_id: documentId }
    ),
  revisionChunks: (applicationId: string, environmentId: string, documentId: string, revisionId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/versions/{revision_id}/chunks',
      {
        application_id: applicationId,
        environment_id: environmentId,
        document_id: documentId,
        revision_id: revisionId
      }
    ),
  rollbackRevision: (
    applicationId: string,
    environmentId: string,
    documentId: string,
    revisionId: string
  ) =>
    apiClient.operationAt(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/versions/{revision_id}/rollback',
      {
        application_id: applicationId,
        environment_id: environmentId,
        document_id: documentId,
        revision_id: revisionId
      }
    ),
  upload: (
    applicationId: string,
    environmentId: string,
    collectionId: string,
    file: File
  ) => {
    const formData = new FormData()
    formData.append('file', file)
    return apiClient.operationAtWithFormData(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/collections/{collection_id}/documents/upload',
      { application_id: applicationId, environment_id: environmentId, collection_id: collectionId },
      formData,
      idempotencyConfig()
    )
  },
  createText: (
    applicationId: string,
    environmentId: string,
    collectionId: string,
    payload: TextDocumentCreateInput
  ) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/collections/{collection_id}/documents/text',
      { application_id: applicationId, environment_id: environmentId, collection_id: collectionId },
      payload,
      idempotencyConfig()
    ),
  // 手工文本更新沿用服务端的异步入库与幂等保护，不能在浏览器直接改写当前版本。
  createTextRevision: (
    applicationId: string,
    environmentId: string,
    documentId: string,
    payload: TextRevisionCreateInput
  ) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/versions/text',
      { application_id: applicationId, environment_id: environmentId, document_id: documentId },
      payload,
      idempotencyConfig()
    ),
  // 文件更新生成独立修订，旧版本会继续保留，直到新版本完成发布或管理员主动回滚。
  uploadRevision: (
    applicationId: string,
    environmentId: string,
    documentId: string,
    file: File
  ) => {
    const formData = new FormData()
    formData.append('file', file)
    return apiClient.operationAtWithFormData(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/versions/upload',
      { application_id: applicationId, environment_id: environmentId, document_id: documentId },
      formData,
      idempotencyConfig()
    )
  },
  createRemote: (
    applicationId: string,
    environmentId: string,
    collectionId: string,
    payload: RemoteSourcePayload
  ) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/collections/{collection_id}/documents/remote',
      { application_id: applicationId, environment_id: environmentId, collection_id: collectionId },
      payload,
      idempotencyConfig()
    ),
  previewRemote: (
    applicationId: string,
    environmentId: string,
    collectionId: string,
    payload: RemoteSourcePayload
  ) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/collections/{collection_id}/documents/remote-preview',
      { application_id: applicationId, environment_id: environmentId, collection_id: collectionId },
      payload,
      { timeout: 35_000 }
    ),
  retryRun: (applicationId: string, environmentId: string, runId: string) =>
    apiClient.operationAt(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/ingestion-runs/{run_id}/retry',
      { application_id: applicationId, environment_id: environmentId, run_id: runId },
      idempotencyConfig()
    ),
  archiveDocument: (applicationId: string, environmentId: string, documentId: string) =>
    apiClient.operationAt(
      'delete',
      '/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}',
      { application_id: applicationId, environment_id: environmentId, document_id: documentId }
    ),
  refreshRemote: (applicationId: string, environmentId: string, documentId: string) =>
    apiClient.operationAt(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/refresh',
      { application_id: applicationId, environment_id: environmentId, document_id: documentId },
      idempotencyConfig()
    ),
  archiveCollection: (applicationId: string, environmentId: string, collectionId: string) =>
    apiClient.operationAt(
      'delete',
      '/control/v1/applications/{application_id}/environments/{environment_id}/collections/{collection_id}',
      { application_id: applicationId, environment_id: environmentId, collection_id: collectionId }
    ),
  runs: (applicationId: string, environmentId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/ingestion-runs',
      { application_id: applicationId, environment_id: environmentId }
    )
}

// 保持历史页面的导入路径；实际字段来自 OpenAPI 生成类型。
export type { RemoteSourcePayload } from '@aik/contracts'

export const intelligenceApi = {
  retrievalProfiles: (applicationId: string, environmentId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles',
      { application_id: applicationId, environment_id: environmentId }
    ),
  createRetrievalProfile: (applicationId: string, environmentId: string, payload: RetrievalProfileCreateInput) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles',
      { application_id: applicationId, environment_id: environmentId },
      payload
    ),
  answerProfiles: (applicationId: string, environmentId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles',
      { application_id: applicationId, environment_id: environmentId }
    ),
  createAnswerProfile: (applicationId: string, environmentId: string, payload: AnswerProfileCreateInput) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles',
      { application_id: applicationId, environment_id: environmentId },
      payload
    ),
  // 调试沿管理员 Cookie 调用控制面，浏览器不再接收或转发业务 API Key。
  debugAnswerProfile: (
    applicationId: string,
    environmentId: string,
    profileId: string,
    payload: AnswerDebugRequestInput
  ) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/debug',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId },
      payload
    ),
  retrievalRevisions: (applicationId: string, environmentId: string, profileId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles/{profile_id}/revisions',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId }
    ),
  answerRevisions: (applicationId: string, environmentId: string, profileId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/revisions',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId }
    ),
  profileReleases: (applicationId: string, environmentId: string, profileId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/profile-releases/{profile_id}',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId }
    ),
  createRetrievalDraft: (
    applicationId: string,
    environmentId: string,
    profileId: string,
    payload: RetrievalProfileRevisionDraft
  ) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles/{profile_id}/revisions',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId },
      payload
    ),
  createAnswerDraft: (
    applicationId: string,
    environmentId: string,
    profileId: string,
    payload: AnswerProfileRevisionDraft
  ) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/revisions',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId },
      payload
    ),
  updateRetrievalDraft: (
    applicationId: string,
    environmentId: string,
    profileId: string,
    revisionId: string,
    payload: RetrievalProfileRevisionDraft
  ) =>
    apiClient.operationAtWithBody(
      'put',
      '/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles/{profile_id}/revisions/{revision_id}',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId, revision_id: revisionId },
      payload
    ),
  updateAnswerDraft: (
    applicationId: string,
    environmentId: string,
    profileId: string,
    revisionId: string,
    payload: AnswerProfileRevisionDraft
  ) =>
    apiClient.operationAtWithBody(
      'put',
      '/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/revisions/{revision_id}',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId, revision_id: revisionId },
      payload
    ),
  publishRetrievalRevision: (
    applicationId: string,
    environmentId: string,
    profileId: string,
    revisionId: string
  ) =>
    apiClient.operationAt(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles/{profile_id}/revisions/{revision_id}/publish',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId, revision_id: revisionId }
    ),
  publishAnswerRevision: (
    applicationId: string,
    environmentId: string,
    profileId: string,
    revisionId: string
  ) =>
    apiClient.operationAt(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/revisions/{revision_id}/publish',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId, revision_id: revisionId }
    ),
  rollbackRetrievalRevision: (
    applicationId: string,
    environmentId: string,
    profileId: string,
    revisionId: string
  ) =>
    apiClient.operationAt(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles/{profile_id}/revisions/{revision_id}/rollback',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId, revision_id: revisionId }
    ),
  rollbackAnswerRevision: (
    applicationId: string,
    environmentId: string,
    profileId: string,
    revisionId: string
  ) =>
    apiClient.operationAt(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/revisions/{revision_id}/rollback',
      { application_id: applicationId, environment_id: environmentId, profile_id: profileId, revision_id: revisionId }
    )
}

export const evaluationApi = {
  sets: (applicationId: string, environmentId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/sets',
      { application_id: applicationId, environment_id: environmentId }
    ),
  createSet: (applicationId: string, environmentId: string, payload: EvaluationSetCreateInput) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/sets',
      { application_id: applicationId, environment_id: environmentId },
      payload
    ),
  cases: (applicationId: string, environmentId: string, setId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/sets/{set_id}/cases',
      { application_id: applicationId, environment_id: environmentId, set_id: setId }
    ),
  createCase: (applicationId: string, environmentId: string, setId: string, payload: EvaluationCaseCreateInput) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/sets/{set_id}/cases',
      { application_id: applicationId, environment_id: environmentId, set_id: setId },
      payload
    ),
  // 历史运行独立读取，页面刷新后仍能复盘发布门禁的真实依据。
  runs: (applicationId: string, environmentId: string, setId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/sets/{set_id}/runs',
      { application_id: applicationId, environment_id: environmentId, set_id: setId }
    ),
  // 发起运行固定候选策略修订，服务端会拒绝不属于当前环境的策略。
  run: (applicationId: string, environmentId: string, setId: string, payload: EvaluationRunCreateInput) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/sets/{set_id}/runs',
      { application_id: applicationId, environment_id: environmentId, set_id: setId },
      payload
    ),
  // 运行结果包含失败码与确定性检查，不能由前端根据汇总结果推断。
  results: (applicationId: string, environmentId: string, runId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/runs/{run_id}/results',
      { application_id: applicationId, environment_id: environmentId, run_id: runId }
    )
}

export const developerApi = {
  keys: (applicationId: string, environmentId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/api-keys',
      { application_id: applicationId, environment_id: environmentId }
    ),
  createKey: (
    applicationId: string,
    environmentId: string,
    payload: ApiKeyCreateInput
  ) =>
    apiClient.operationAtWithBody(
      'post',
      '/control/v1/applications/{application_id}/environments/{environment_id}/api-keys',
      { application_id: applicationId, environment_id: environmentId },
      payload
    ),
  revokeKey: (applicationId: string, environmentId: string, keyId: string) =>
    apiClient.operationAt(
      'delete',
      '/control/v1/applications/{application_id}/environments/{environment_id}/api-keys/{key_id}',
      { application_id: applicationId, environment_id: environmentId, key_id: keyId }
    )
}

export const operationApi = {
  // 概览窗口由服务端以当前时间截断，调用方只能选择受限小时数以保持统计口径一致。
  summary: (applicationId: string, environmentId: string, windowHours = 24) =>
    apiClient.operationAtWithQuery(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/operations/summary',
      { application_id: applicationId, environment_id: environmentId },
      { windowHours }
    ),
  // 过滤参数均由控制面在当前应用环境内执行，前端只保留关键字的本地模糊匹配。
  traces: (
    applicationId: string,
    environmentId: string,
    filters: { status?: string; minTotalMs?: number } = {}
  ) =>
    apiClient.operationAtWithQuery(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/operations/traces',
      { application_id: applicationId, environment_id: environmentId },
      filters
    ),
  trace: (applicationId: string, environmentId: string, requestId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/operations/traces/{request_id}',
      { application_id: applicationId, environment_id: environmentId, request_id: requestId }
    ),
  // 反馈仅在控制面展示；业务调用方仍只能写入自己的同环境请求反馈。
  feedback: (applicationId: string, environmentId: string) =>
    apiClient.operationAt(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/operations/feedback',
      { application_id: applicationId, environment_id: environmentId }
    ),
  // 入库尝试也纳入运行中心，服务端始终按应用环境过滤并返回脱敏错误摘要。
  ingestionAttempts: (
    applicationId: string,
    environmentId: string,
    filters: { runId?: string; status?: string } = {}
  ) =>
    apiClient.operationAtWithQuery(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/operations/ingestion-attempts',
      { application_id: applicationId, environment_id: environmentId },
      filters
    )
}

/**
 * 读取就绪诊断。
 *
 * 该端点在基础依赖故障时仍以 503 返回结构化 checks，不能交给会把非 2xx 统一抛错的
 * 业务信封客户端；响应类型仍直接来自 OpenAPI，避免维护第二份健康字段定义。
 */
export async function getHealth(): Promise<OpenApiOperationResponse<'/ready', 'get'>> {
  const response = await fetch('/ready', {
    credentials: 'include',
    signal: AbortSignal.timeout(4_000)
  })
  const payload = (await response.json()) as OpenApiOperationResponse<'/ready', 'get'>
  if (!response.ok && !payload.checks) throw new Error('健康检查不可用')
  return payload
}
