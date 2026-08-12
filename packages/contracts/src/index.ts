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

export interface Environment {
  id: string
  applicationId: string
  code: 'development' | 'testing' | 'production'
  name: string
  status: string
  createdAt: string
}

export interface Application {
  id: string
  code: string
  name: string
  description?: string | null
  applicationType: string
  status: string
  createdAt: string
  updatedAt: string
  environments: Environment[]
}

export interface KnowledgeCollection {
  id: string
  applicationId: string
  environmentId: string
  code: string
  name: string
  description?: string | null
  status: string
  documentCount: number
  chunkCount: number
  lastPublishedAt?: string | null
  createdAt: string
}

export interface KnowledgeDocument {
  id: string
  collectionId: string
  title: string
  mimeType?: string | null
  status: string
  currentVersion?: number | null
  sourceUrl?: string | null
  archivedAt?: string | null
  createdAt: string
  updatedAt: string
}

export interface IngestionRun {
  id: string
  documentId: string
  revisionId: string
  status: string
  stage: string
  progress: number
  errorCode?: string | null
  errorMessage?: string | null
  retryCount: number
  requestId: string
  startedAt?: string | null
  completedAt?: string | null
  createdAt: string
}

export interface RetrievalProfile {
  id: string
  code: string
  name: string
  collectionIds: string[]
  topK: number
  minimumScore: number
  vectorWeight: number
  lexicalWeight: number
  metadataFilters: Record<string, string | number | boolean>
  status: string
}

export interface AnswerProfile {
  id: string
  code: string
  name: string
  retrievalProfileId: string
  systemPrompt: string
  outputSchema: Record<string, unknown>
  toolCodes: string[]
  knowledgeRequired: boolean
  modelFallbackAllowed: boolean
  webFallbackAllowed: boolean
  minimumEvidenceCount: number
  minimumEvidenceScore: number
  requireFreshData: boolean
  maximumDataAgeSeconds?: number | null
  status: string
}

export interface ApiKeyRecord {
  id: string
  name: string
  keyPrefix: string
  scopes: string[]
  status: string
  expiresAt?: string | null
  lastUsedAt?: string | null
  createdAt: string
}

export interface ApiKeyCreated extends ApiKeyRecord {
  secret: string
}

export interface OperationSummary {
  totalRequests: number
  failedRequests: number
  successRate: number
  averageDurationMs: number
  modelFallbackRate: number
}

export interface RequestTrace {
  id: string
  requestId: string
  operation: string
  profileCode?: string | null
  status: string
  answerMode?: string | null
  confidence?: number | null
  evidenceCount: number
  degraded: boolean
  degradedReasons: string[]
  totalMs?: number | null
  inputTokens: number
  outputTokens: number
  errorCode?: string | null
  createdAt: string
}

export interface RequestTraceDetail extends Omit<RequestTrace, 'id' | 'evidenceCount'> {
  evidence: Array<{
    sourceType: string
    title: string
    excerpt: string
    score: number
    citation: Record<string, unknown>
  }>
}

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'error'
  ready: boolean
  checks: Record<string, { status: string }>
}

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

export interface AnswerResult {
  requestId: string
  answerMode: string
  answer: string
  structuredOutput: Record<string, unknown>
  confidence: number
  warnings: string[]
  knowledge: {
    used: boolean
    hitCount: number
    citations: Array<Record<string, unknown>>
    evidence: Array<Record<string, unknown>>
  }
  modelSupplement: { used: boolean; reason: string }
  web: {
    used: boolean
    hitCount: number
    citations: Array<Record<string, unknown>>
  }
  degraded: boolean
  degradedReasons: string[]
  usage: { inputTokens: number; outputTokens: number }
  timing: { retrievalMs: number; totalMs: number }
}
