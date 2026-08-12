import { describe, expect, it } from 'vitest'
import { asPlatformError, PlatformError } from './client'

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
})
