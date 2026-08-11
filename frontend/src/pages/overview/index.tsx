import { Spin } from 'antd'
import StatCard from './components/StatCard'
import ProjectMiniList from './components/ProjectMiniList'
import { useOverviewStats } from './hooks/useOverviewStats'

/**
 * 全局概览页（/overview）
 * - 4 个统计卡片：项目总数、活跃项目数、今日总调用、异常数
 * - 最近活跃项目迷你列表（点击进入项目内概览）
 * - 容错：统计接口失败时显示"—"占位，不阻塞页面
 */
export default function OverviewPage() {
  const { stats, loading, recentProjects, projectsLoading } = useOverviewStats()

  return (
    <Spin spinning={loading && !stats}>
      <div className="flex h-full w-full flex-col">
        {/* 概览优先提示平台当前可行动的信息，而不是只展示静态统计。 */}
        <div className="mb-7 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-500">
              Platform overview
            </div>
            <h1 className="m-0 text-2xl font-semibold tracking-tight text-slate-900">平台总览</h1>
            <p className="mb-0 mt-2 text-sm text-slate-500">
              查看所有项目空间的运行状态、调用规模与需要处理的异常。
            </p>
          </div>
          <div className="rounded-full border border-indigo-100 bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700">
            多项目 AI 能力控制台
          </div>
        </div>

        {/* 统计卡片网格：4 列布局，响应式 sm/md 适配 */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard title="项目总数" value={stats?.totalProjects} loading={loading} suffix="个" />
          <StatCard
            title="活跃项目数"
            value={stats?.activeProjects}
            loading={loading}
            suffix="个"
            valueStyle={{ color: '#52c41a' }}
          />
          <StatCard title="今日总调用" value={stats?.todayCalls} loading={loading} suffix="次" />
          <StatCard
            title="异常数"
            value={stats?.errorCount}
            loading={loading}
            suffix="次"
            valueStyle={{ color: '#f5222d' }}
          />
        </div>

        {/* 最近活跃项目列表 */}
        <div className="mt-7">
          <ProjectMiniList projects={recentProjects} loading={projectsLoading} />
        </div>
      </div>
    </Spin>
  )
}
