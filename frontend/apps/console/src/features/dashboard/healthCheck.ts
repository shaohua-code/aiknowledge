/**
 * 控制台健康状态文案工具，负责把依赖检查状态转换为管理员可理解的提示。
 * 页面组件不导出辅助函数，避免 Fast Refresh 将非组件导出视为热更新边界。
 */
export function healthCheckDescription(status: string): string {
  /** 将基础依赖就绪、Provider 配置和真实连通性明确区分给管理员。 */
  if (['ok', 'ready'].includes(status)) return '连接正常'
  if (status === 'configured') return '已配置，尚未执行连通测试'
  if (status === 'development') return '本地开发实现，未验证外部服务'
  if (status === 'checking') return '正在检测'
  return '需要配置或检查'
}
