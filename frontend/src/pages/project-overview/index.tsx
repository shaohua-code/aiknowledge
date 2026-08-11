import { Spin } from 'antd'
import ProjectStatCard from './components/ProjectStatCard'
import RecentJobsList from './components/RecentJobsList'
import KnowledgeBaseMiniList from './components/KnowledgeBaseMiniList'
import { useProjectOverview } from './hooks/useProjectOverview'

/**
 * 项目概览页（项目内路由 /projects/:projectId/overview）
 * - 4 个统计卡片：知识库数量、文档总数、今日调用、平均耗时
 * - 最近研究任务列表（5 条）
 * - 知识库迷你列表
 * - 容错：聚合接口失败时分别调用已有接口拼装数据
 */
export default function ProjectOverviewPage() {
  const { stats, statsLoading, knowledgeBases, kbsLoading, recentJobs, jobsLoading } =
    useProjectOverview()

  return (
    <Spin spinning={statsLoading && !stats}>
      <div className="flex h-full w-full flex-col">
        {/* 项目首页聚焦当前项目的知识、智能执行与待处理状态。 */}
        <div className="mb-7 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-500">
              Project workspace
            </div>
            <h1 className="m-0 text-2xl font-semibold tracking-tight text-slate-900">项目概览</h1>
            <p className="mb-0 mt-2 text-sm text-slate-500">
              从知识资产、研究执行与自动化运行情况开始管理当前项目。
            </p>
          </div>
          <div className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-sm">
            当前项目空间
          </div>
        </div>

        {/* 统计卡片网格：4 列布局，响应式 sm/md 适配 */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <ProjectStatCard
            title="知识库数量"
            value={stats?.knowledgeBaseCount}
            loading={statsLoading}
            suffix="个"
          />
          <ProjectStatCard
            title="文档总数"
            value={stats?.totalDocuments}
            loading={statsLoading}
            suffix="篇"
            valueStyle={{ color: '#1677ff' }}
          />
          <ProjectStatCard
            title="今日调用"
            value={stats?.todayCalls}
            loading={statsLoading}
            suffix="次"
            valueStyle={{ color: '#52c41a' }}
          />
          <ProjectStatCard
            title="平均耗时"
            value={stats?.avgDurationMs}
            loading={statsLoading}
            suffix="ms"
          />
        </div>

        {/* 最近研究任务列表 */}
        <div className="mt-7">
          <RecentJobsList jobs={recentJobs} loading={jobsLoading} />
        </div>

        {/* 知识库迷你列表 */}
        <div className="mt-7">
          <KnowledgeBaseMiniList knowledgeBases={knowledgeBases} loading={kbsLoading} />
        </div>
      </div>
    </Spin>
  )
}
