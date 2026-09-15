import { describe, expect, it } from 'vitest'
import { hasActiveIngestionRuns } from './ingestionPolling'

describe('hasActiveIngestionRuns', () => {
  it('仅在服务端定义的入库非终态存在时继续轮询', () => {
    expect(hasActiveIngestionRuns([{ status: 'queued' }])).toBe(true)
    expect(hasActiveIngestionRuns([{ status: 'running' }])).toBe(true)
  })

  it('成功、失败或空列表不会保持无意义的五秒轮询', () => {
    expect(hasActiveIngestionRuns([{ status: 'succeeded' }, { status: 'failed' }])).toBe(false)
    expect(hasActiveIngestionRuns([])).toBe(false)
  })
})
