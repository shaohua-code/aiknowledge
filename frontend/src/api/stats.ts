import request from './request'

// === 统计聚合接口类型定义 ===

/** 全局概览统计（聚合后端多个来源） */
export interface OverviewStats {
  /** 项目总数 */
  totalProjects: number
  /** 活跃项目数（status === active） */
  activeProjects: number
  /** 今日总调用（研究任务）数 */
  todayCalls: number
  /** 异常数（失败 + 超时） */
  errorCount: number
}

/** 项目概览统计（聚合项目维度数据） */
export interface ProjectOverviewStats {
  /** 知识库数量 */
  knowledgeBaseCount: number
  /** 文档总数（所有知识库 documentCount 之和） */
  totalDocuments: number
  /** 今日调用数 */
  todayCalls: number
  /** 平均耗时（毫秒） */
  avgDurationMs: number
}

// 统计接口已由后端聚合；保留 null 兜底以保障权限不足或依赖短暂不可用时页面可读。

/**
 * 获取全局概览统计；管理密钥无效或服务临时不可用时返回 null。
 */
export async function getOverviewStats(): Promise<OverviewStats | null> {
  try {
    return await request.get<OverviewStats>('/v1/stats/overview')
  } catch {
    // 容错：密钥无效或服务短暂不可用时，页面继续使用兜底展示。
    return null
  }
}

/**
 * 获取当前 API Key 归属项目的概览统计。
 * 项目身份只能由服务端 ProjectContext 判定，客户端不得传 projectId 覆盖范围。
 */
export async function getProjectOverviewStats(): Promise<ProjectOverviewStats | null> {
  try {
    return await request.get<ProjectOverviewStats>('/v1/stats/project')
  } catch {
    // 容错：权限不足或服务短暂不可用时，页面继续使用兜底展示。
    return null
  }
}
