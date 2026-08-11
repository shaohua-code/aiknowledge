import { createBrowserRouter, Navigate } from 'react-router-dom'
import MainLayout from '@/layouts/MainLayout'
import ProjectLayout from '@/layouts/ProjectLayout'
import ProjectsPage from '@/pages/projects'
import KnowledgeBasesPage from '@/pages/knowledge-bases'
import ResearchPage from '@/pages/research'
import DocumentsPage from '@/pages/documents'
import RetrievalTestPage from '@/pages/retrieval-test'
// Task 20 新增页面：定时任务 / 定时运行记录 / 采集源 / 采集记录 / 网络资料池
import SchedulesPage from '@/pages/schedules'
import ScheduleRunsPage from '@/pages/schedule-runs'
import CrawlSourcesPage from '@/pages/crawl-sources'
import CrawlRunsPage from '@/pages/crawl-runs'
import WebMaterialsPage from '@/pages/web-materials'
// Task 21 新增页面：工具配置 / 提示词 / API Key / 执行记录 / 项目设置
import ToolsPage from '@/pages/tools'
import PromptsPage from '@/pages/prompts'
import ApiKeysPage from '@/pages/api-keys'
import ExecutionLogsPage from '@/pages/execution-logs'
import ProjectSettingsPage from '@/pages/project-settings'
// Task 22 新增页面：全局概览 / 项目概览
import OverviewPage from '@/pages/overview'
import ProjectOverviewPage from '@/pages/project-overview'

// 路由配置：
// - / 主布局，默认跳转全局概览
// - /overview 全局概览（与 projects 同级）
// - /projects 项目列表（管理密钥保护）
// - /projects/:projectId 项目内布局，进入项目默认跳转到项目概览
export const router = createBrowserRouter([
  {
    path: '/',
    element: <MainLayout />,
    children: [
      // 进入系统默认跳转全局概览
      { index: true, element: <Navigate to="/overview" replace /> },
      // Task 22 新增：全局概览页
      { path: 'overview', element: <OverviewPage /> },
      { path: 'projects', element: <ProjectsPage /> },
      {
        path: 'projects/:projectId',
        element: <ProjectLayout />,
        children: [
          // 进入项目默认跳转到项目概览（Task 22 调整：原 knowledge-bases 改为 overview）
          { index: true, element: <Navigate to="overview" replace /> },
          // Task 22 新增：项目概览页作为项目内首页
          { path: 'overview', element: <ProjectOverviewPage /> },
          { path: 'knowledge-bases', element: <KnowledgeBasesPage /> },
          // Task 17 新增页面：智能研究台 / 文档管理 / 检索测试
          { path: 'research', element: <ResearchPage /> },
          { path: 'documents', element: <DocumentsPage /> },
          { path: 'retrieval-test', element: <RetrievalTestPage /> },
          // Task 20 新增页面：定时任务 / 定时运行记录 / 采集源 / 采集记录 / 网络资料池
          { path: 'schedules', element: <SchedulesPage /> },
          { path: 'schedule-runs', element: <ScheduleRunsPage /> },
          { path: 'crawl-sources', element: <CrawlSourcesPage /> },
          { path: 'crawl-runs', element: <CrawlRunsPage /> },
          { path: 'web-materials', element: <WebMaterialsPage /> },
          // Task 21 新增页面：工具配置 / 提示词 / API Key / 执行记录 / 项目设置
          { path: 'tools', element: <ToolsPage /> },
          { path: 'prompts', element: <PromptsPage /> },
          { path: 'api-keys', element: <ApiKeysPage /> },
          { path: 'execution-logs', element: <ExecutionLogsPage /> },
          { path: 'project-settings', element: <ProjectSettingsPage /> }
        ]
      }
    ]
  }
])
