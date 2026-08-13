import { describe, expect, it } from 'vitest'
import { asPlatformError, parseHttpError, PlatformError } from './client'

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
})
