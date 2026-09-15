import type { components as OpenApiComponents } from './openapi.generated'

// OpenAPI 快照生成的路径、操作和 Schema 类型；修改后应重新导出快照而非手改产物。
export type { components, operations, paths } from './openapi.generated'

/** OpenAPI 中允许由控制台调用的 HTTP 方法集合。 */
export type OpenApiHttpMethod = 'get' | 'post' | 'put' | 'patch' | 'delete'

/** 从 `/resources/{resource_id}` 一类生成路径中提取必须提供的参数名。 */
export type OpenApiPathParameter<Path extends string> =
  Path extends `${string}{${infer Parameter}}${infer Rest}`
    ? Parameter | OpenApiPathParameter<Rest>
    : never

/** 从生成路径表定位一个操作；不存在的方法会收敛为 never，避免伪造请求契约。 */
type OpenApiOperation<
  Path extends keyof import('./openapi.generated').paths,
  Method extends OpenApiHttpMethod
> = Method extends keyof import('./openapi.generated').paths[Path]
  ? import('./openapi.generated').paths[Path][Method]
  : never

/** 从 200、201 或 202 中提取原始 JSON 成功响应，兼容非业务信封的诊断端点。 */
export type OpenApiOperationResponse<
  Path extends keyof import('./openapi.generated').paths,
  Method extends OpenApiHttpMethod
> = OpenApiOperation<Path, Method> extends {
  responses: { 200: { content: { 'application/json': infer Response } } }
}
  ? Response
  : OpenApiOperation<Path, Method> extends {
        responses: { 201: { content: { 'application/json': infer Response } } }
      }
    ? Response
    : OpenApiOperation<Path, Method> extends {
          responses: { 202: { content: { 'application/json': infer Response } } }
        }
      ? Response
      : never

/** 从业务成功信封中提取控制台真正消费的 data，Axios 层仍统一处理 requestId 与错误。 */
export type OpenApiOperationData<
  Path extends keyof import('./openapi.generated').paths,
  Method extends OpenApiHttpMethod
> = OpenApiOperationResponse<Path, Method> extends { data: infer Data } ? Data : never

/** 从生成操作中提取 JSON 请求体；无请求体的操作返回 never。 */
export type OpenApiOperationBody<
  Path extends keyof import('./openapi.generated').paths,
  Method extends OpenApiHttpMethod
> = OpenApiOperation<Path, Method> extends {
  requestBody: { content: { 'application/json': infer Body } }
}
  ? Body
  : never

/** 从生成操作提取 query 参数，避免筛选字段与服务端约束分别维护。 */
export type OpenApiOperationQuery<
  Path extends keyof import('./openapi.generated').paths,
  Method extends OpenApiHttpMethod
> = OpenApiOperation<Path, Method> extends { parameters: { query?: infer Query } }
  ? Query
  : never

/** 标记操作是否声明 multipart 请求体，供文件上传专用客户端约束模板和方法。 */
export type OpenApiOperationMultipartBody<
  Path extends keyof import('./openapi.generated').paths,
  Method extends OpenApiHttpMethod
> = OpenApiOperation<Path, Method> extends {
  requestBody: { content: { 'multipart/form-data': infer Body } }
}
  ? Body
  : never

export interface ApiErrorBody {
  code: string
  title: string
  message: string
  retryable: boolean
  suggestion?: string | null
  details: Record<string, unknown>
}

export type ApiEnvelope<T> =
  | { success: true; requestId: string; data: T; meta: Record<string, unknown> }
  | { success: false; requestId: string; error: ApiErrorBody }

// 与服务端 Pydantic 视图同名的只读类型全部来自生成快照，避免字段演进后前后端静默漂移。
export type Environment = OpenApiComponents['schemas']['EnvironmentView']
export type Application = OpenApiComponents['schemas']['ApplicationView']
export type ApplicationCreateInput = OpenApiComponents['schemas']['ApplicationCreate']
export type ApplicationUpdateInput = OpenApiComponents['schemas']['ApplicationUpdate']
export type KnowledgeCollection = OpenApiComponents['schemas']['CollectionView']
export type CollectionCreateInput = OpenApiComponents['schemas']['CollectionCreate']
export type KnowledgeDocument = OpenApiComponents['schemas']['DocumentView']
export type DocumentRevision = OpenApiComponents['schemas']['DocumentRevisionView']
export type DocumentChunk = OpenApiComponents['schemas']['DocumentChunkView']
export type IngestionRun = OpenApiComponents['schemas']['IngestionRunView']
export type RemoteSourcePreview = OpenApiComponents['schemas']['RemotePreviewView']
export type RemoteSourcePayload = OpenApiComponents['schemas']['RemoteDocumentCreate']
export type TextDocumentCreateInput = OpenApiComponents['schemas']['TextDocumentCreate']
// 手工文本更新只提交新版正文；文档归属和版本号始终由服务端按当前上下文确定。
export type TextRevisionCreateInput = OpenApiComponents['schemas']['TextRevisionCreate']
export type RetrievalProfile = OpenApiComponents['schemas']['RetrievalProfileView']
export type RetrievalProfileCreateInput = OpenApiComponents['schemas']['RetrievalProfileCreate']
export type AnswerProfile = OpenApiComponents['schemas']['AnswerProfileView']
export type AnswerProfileCreateInput = OpenApiComponents['schemas']['AnswerProfileCreate']
export type AnswerDebugRequestInput = OpenApiComponents['schemas']['AnswerDebugRequest']
export type RetrievalProfileRevisionDraft =
  OpenApiComponents['schemas']['RetrievalProfileRevisionDraft']
export type AnswerProfileRevisionDraft = OpenApiComponents['schemas']['AnswerProfileRevisionDraft']
export type ProfileRevision = OpenApiComponents['schemas']['ProfileRevisionView']
export type ProfileRelease = OpenApiComponents['schemas']['ProfileReleaseView']
export type EvaluationSet = OpenApiComponents['schemas']['EvaluationSetView']
export type EvaluationSetCreateInput = OpenApiComponents['schemas']['EvaluationSetCreate']
export type EvaluationCaseRevision = OpenApiComponents['schemas']['EvaluationCaseRevisionView']
export type EvaluationCaseCreateInput = OpenApiComponents['schemas']['EvaluationCaseCreate']
// 单样本结果仍直接映射生成契约，防止手写字段遗漏失败检查细节。
export type EvaluationCaseResult = OpenApiComponents['schemas']['EvaluationCaseResultView']
export type EvaluationRun = OpenApiComponents['schemas']['EvaluationRunView']
export type EvaluationRunCreateInput = OpenApiComponents['schemas']['EvaluationRunCreate']

// 这些显示模型与公开响应 Schema 一一对应，禁止再在此维护第二份字段清单。
export type ApiKeyRecord = OpenApiComponents['schemas']['ApiKeyView']
export type ApiKeyCreated = OpenApiComponents['schemas']['ApiKeyCreated']
export type ApiKeyCreateInput = OpenApiComponents['schemas']['ApiKeyCreate']
export type ApiKeyRevocation = OpenApiComponents['schemas']['ApiKeyRevocationResponse']
export type OperationSummary = OpenApiComponents['schemas']['OperationSummaryResponse']
export type RequestTrace = OpenApiComponents['schemas']['OperationTraceView']
export type RequestTraceDetail = OpenApiComponents['schemas']['OperationTraceDetailResponse']
// 反馈只在管理员控制面读取，展示类型保持与生成的脱敏公开契约一致。
export type OperationFeedback = OpenApiComponents['schemas']['OperationFeedbackView']
// 入库尝试由运行中心统一展示，便于从失败任务定位到具体 Worker 阶段。
export type IngestionAttempt = OpenApiComponents['schemas']['IngestionAttemptView']
export type AdminSession = OpenApiComponents['schemas']['AdminSessionResponse']
export type LogoutResult = OpenApiComponents['schemas']['LogoutResponse']
export type ArchiveResult = OpenApiComponents['schemas']['ArchiveResponse']
export type DocumentIngestionQueued =
  OpenApiComponents['schemas']['DocumentIngestionQueuedResponse']

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'error'
  ready: boolean
  checks: Record<string, { status: string }>
}

// 模型读取接口为历史 snake_case 数据，生成快照已精确表达这一兼容约束。
export type ModelConfiguration = OpenApiComponents['schemas']['ModelConfigurationResponse']
export type ModelConfigurationTestResult = OpenApiComponents['schemas']['ChatModelTestResponse']

export type ModelConfigurationUpdate = OpenApiComponents['schemas']['ModelConfigurationUpdate']

export type ModelCapability = OpenApiComponents['schemas']['ModelCatalogEntryResponse']['capability']

export type ModelCatalogEntry = OpenApiComponents['schemas']['ModelCatalogEntryResponse']
export type ModelCatalogList = OpenApiComponents['schemas']['ModelCatalogListResponse']
export type ModelCatalogTestResult = OpenApiComponents['schemas']['ModelCatalogTestResponse']
export type ModelCatalogDeletionResponse =
  OpenApiComponents['schemas']['ModelCatalogDeletionResponse']

export type ModelCatalogEntryCreate = OpenApiComponents['schemas']['ModelCatalogEntryCreate']
export type ModelCatalogEntryUpdate = OpenApiComponents['schemas']['ModelCatalogEntryUpdate']

export type ModelBinding = OpenApiComponents['schemas']['ModelBindingResponse']

// 绑定更新要求三个能力都显式携带，避免部分写入遗留旧绑定。
export type ModelBindingUpdate = Required<OpenApiComponents['schemas']['ModelBindingUpdate']>

export interface RetrievalHit {
  chunkId: string
  documentId: string
  title: string
  content: string
  score: number
  vectorScore: number
  lexicalScore: number
  citation: Record<string, unknown>
}

export interface RetrieveResult {
  query: string
  hits: RetrievalHit[]
  totalHits: number
  elapsedMs: number
}

export type AnswerResult = OpenApiComponents['schemas']['AnswerResponse']
export type AnswerRequestInput = OpenApiComponents['schemas']['AnswerRequest']
