import { describe, expect, it, vi } from 'vitest'

// 统一替身让操作级客户端只验证请求组装，不向真实服务发出网络访问。
const axiosRequest = vi.hoisted(() => vi.fn())

vi.mock('axios', () => ({
  default: {
    create: () => ({ request: axiosRequest }),
    isAxiosError: () => false
  }
}))

import { apiClient, asPlatformError, parseHttpError, PlatformError } from './client'
import { knowledgeApi } from './platform'

describe('platform errors', () => {
  it('preserves request id and recovery metadata', () => {
    const error = new PlatformError(
      {
        code: 'PROVIDER_UNAVAILABLE',
        title: 'AI 服务暂不可用',
        message: '模型连接失败',
        retryable: true,
        suggestion: '稍后重试',
        details: { provider: 'chat' }
      },
      { requestId: 'req_test_001', status: 503 }
    )

    expect(error.requestId).toBe('req_test_001')
    expect(error.retryable).toBe(true)
    expect(error.suggestion).toBe('稍后重试')
    expect(error.details).toEqual({ provider: 'chat' })
  })

  it('turns unknown errors into a stable visible error', () => {
    const error = asPlatformError(new Error('unexpected'))
    expect(error.code).toBe('UNKNOWN_ERROR')
    expect(error.message).toBe('unexpected')
  })

  it('shows a clear message for a plain FastAPI 404 response', () => {
    const error = parseHttpError(404, { detail: 'Not Found' }, 'Request failed')

    expect(error.code).toBe('HTTP_404')
    expect(error.title).toBe('后端接口不存在')
    expect(error.message).toBe('当前后端没有提供这个接口')
    expect(error.suggestion).toBe('请重启最新后端服务后刷新页面')
  })

  it('preserves the platform error envelope returned by the backend', () => {
    const error = parseHttpError(
      503,
      {
        success: false,
        requestId: 'req_503',
        error: {
          code: 'PROVIDER_UNAVAILABLE',
          title: '模型不可用',
          message: '连接超时',
          retryable: true,
          details: {}
        }
      },
      'Request failed'
    )

    expect(error.code).toBe('PROVIDER_UNAVAILABLE')
    expect(error.requestId).toBe('req_503')
    expect(error.message).toBe('连接超时')
  })

  it('uses generated operation paths while retaining the shared success-envelope parser', async () => {
    // 此路径和响应字段来自 OpenAPI 快照，测试防止新入口退化成未解包的 Axios 响应。
    axiosRequest.mockResolvedValueOnce({
      status: 200,
      data: {
        success: true,
        requestId: 'req_generated_001',
        meta: {},
        data: { connected: true, inputTokens: 1, outputTokens: 2 }
      }
    })

    await expect(
      apiClient.operation('post', '/control/v1/platform/model-configuration/test')
    ).resolves.toEqual({ connected: true, inputTokens: 1, outputTokens: 2 })
    expect(axiosRequest).toHaveBeenLastCalledWith({
      method: 'POST',
      url: '/control/v1/platform/model-configuration/test'
    })
  })

  it('encodes dynamic OpenAPI path parameters before issuing the request', async () => {
    // 编码防止模型 code 或资源标识中的保留字符改变既定的路径层级。
    axiosRequest.mockResolvedValueOnce({
      status: 200,
      data: {
        success: true,
        requestId: 'req_generated_002',
        meta: {},
        data: { code: 'demo/model', name: '测试', capability: 'chat', provider: 'openai', model: '', base_url: 'https://example.invalid/v1', api_key_env: 'DEMO_KEY', enabled: true, note: null, api_key_configured: true }
      }
    })

    await apiClient.operationAt('delete', '/control/v1/model-catalog/{code}', { code: 'demo/model' })
    expect(axiosRequest).toHaveBeenLastCalledWith({
      method: 'DELETE',
      url: '/control/v1/model-catalog/demo%2Fmodel'
    })
  })

  it('passes generated query fields through Axios params instead of manually concatenating URLs', async () => {
    // 参数对象保持给 Axios 编码，避免手写 query 字符串遗漏空值和转义边界。
    axiosRequest.mockResolvedValueOnce({
      status: 200,
      data: { success: true, requestId: 'req_generated_003', meta: {}, data: [] }
    })

    await apiClient.operationAtWithQuery(
      'get',
      '/control/v1/applications/{application_id}/environments/{environment_id}/operations/traces',
      { application_id: 'app-001', environment_id: 'env-001' },
      { status: 'failed', minTotalMs: 1500 }
    )
    expect(axiosRequest).toHaveBeenLastCalledWith({
      method: 'GET',
      url: '/control/v1/applications/app-001/environments/env-001/operations/traces',
      params: { status: 'failed', minTotalMs: 1500 }
    })
  })

  it('unwraps a 201 generated success envelope for create operations', async () => {
    // 控制面创建集合/应用等端点使用 201，不能被客户端错误推导为 never。
    axiosRequest.mockResolvedValueOnce({
      status: 201,
      data: { success: true, requestId: 'req_generated_004', meta: {}, data: { id: 'app-001' } }
    })

    await expect(
      apiClient.operationWithBody('post', '/control/v1/applications', {
        code: 'demo',
        name: '演示应用',
        applicationType: 'general'
      })
    ).resolves.toEqual({ id: 'app-001' })
    expect(axiosRequest).toHaveBeenLastCalledWith({
      method: 'POST',
      url: '/control/v1/applications',
      data: { code: 'demo', name: '演示应用', applicationType: 'general' }
    })
  })

  it('keeps multipart uploads as FormData and unwraps asynchronous 202 responses', async () => {
    // 上传由浏览器附加 multipart boundary；客户端只约束 OpenAPI 模板、参数和成功信封。
    const formData = new FormData()
    formData.append('file', 'demo.txt')
    axiosRequest.mockResolvedValueOnce({
      status: 202,
      data: { success: true, requestId: 'req_generated_005', meta: {}, data: { runId: 'run-001' } }
    })

    await expect(
      apiClient.operationAtWithFormData(
        'post',
        '/control/v1/applications/{application_id}/environments/{environment_id}/collections/{collection_id}/documents/upload',
        { application_id: 'app-001', environment_id: 'env-001', collection_id: 'collection-001' },
        formData
      )
    ).resolves.toEqual({ runId: 'run-001' })
    expect(axiosRequest).toHaveBeenLastCalledWith({
      method: 'POST',
      url: '/control/v1/applications/app-001/environments/env-001/collections/collection-001/documents/upload',
      data: formData
    })
  })

  it('creates a text revision through the version path with an independent idempotency key', async () => {
    // 新版本必须进入异步入库链路，不能被前端误实现为对当前文档的覆盖更新。
    axiosRequest.mockResolvedValueOnce({
      status: 202,
      data: { success: true, requestId: 'req_revision_001', meta: {}, data: { runId: 'run-002' } }
    })

    await knowledgeApi.createTextRevision('app-001', 'env-001', 'document-001', {
      content: '无敏感的新版本正文'
    })

    expect(axiosRequest).toHaveBeenLastCalledWith({
      method: 'POST',
      url: '/control/v1/applications/app-001/environments/env-001/documents/document-001/versions/text',
      data: { content: '无敏感的新版本正文' },
      headers: { 'Idempotency-Key': expect.any(String) }
    })
  })
})
