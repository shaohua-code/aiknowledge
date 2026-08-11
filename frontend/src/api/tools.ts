import request from './request'

// === 工具配置相关类型定义 ===

/** 全局工具实体（由管理端维护密钥，前端只读展示） */
export interface Tool {
  /** 工具编码（唯一） */
  code: string
  /** 工具名称 */
  name: string
  /** 工具描述 */
  description?: string
  /** 入参 schema（JSON 字符串） */
  inputSchema?: string
  /** 是否需要密钥 */
  requiresSecret?: boolean
  /** 是否已配置密钥 */
  secretConfigured?: boolean
}

/** 项目级工具配置实体 */
export interface ProjectTool {
  /** 工具编码 */
  toolCode: string
  /** 工具名称（来自全局工具，便于展示） */
  toolName?: string
  /** 是否启用 */
  enabled: boolean
  /** 工具配置（JSON 字符串，如 API Key 占位、默认参数） */
  config?: string
  createdAt?: string
  updatedAt?: string
}

/** 创建/更新项目工具配置入参 */
export interface UpsertProjectToolPayload {
  enabled?: boolean
  config?: string
}

/** 工具测试入参 */
export interface ToolTestPayload {
  /** 测试参数（JSON 对象） */
  inputs: Record<string, unknown>
}

/** 工具测试结果 */
export interface ToolTestResult {
  /** 是否成功 */
  success: boolean
  /** 执行输出（JSON 字符串或文本） */
  output?: string
  /** 错误信息 */
  error?: string
  /** 耗时（毫秒） */
  durationMs?: number
}

// === 全局工具接口（管理密钥保护，前端只读） ===

/** 查询全局工具列表（用于项目工具选择） */
export function listTools() {
  return request.get<Tool[]>('/v1/tools')
}

// === 项目工具配置接口 ===

/** 查询项目工具配置列表 */
export function listProjectTools() {
  return request.get<ProjectTool[]>('/v1/project-tools')
}

/** 创建项目工具配置（绑定全局工具到项目） */
export function createProjectTool(payload: UpsertProjectToolPayload & { toolCode: string }) {
  return request.post<ProjectTool>('/v1/project-tools', payload)
}

/** 更新项目工具配置 */
export function updateProjectTool(toolCode: string, payload: UpsertProjectToolPayload) {
  return request.patch<ProjectTool>(`/v1/project-tools/${toolCode}`, payload)
}

/** 删除项目工具配置 */
export function deleteProjectTool(toolCode: string) {
  return request.delete<void>(`/v1/project-tools/${toolCode}`)
}

/** 测试项目工具执行 */
export function testProjectTool(toolCode: string, payload: ToolTestPayload) {
  return request.post<ToolTestResult>(`/v1/project-tools/${toolCode}/test`, payload)
}
