import { describe, expect, it } from 'vitest'
import { healthCheckDescription } from '@/features/dashboard/healthCheck'

describe('healthCheckDescription', () => {
  it('不将仅完成凭证配置的 Provider 呈现为已连通', () => {
    expect(healthCheckDescription('configured')).toContain('尚未执行连通测试')
    expect(healthCheckDescription('development')).toContain('未验证外部服务')
  })
})
