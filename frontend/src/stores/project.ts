import { create } from 'zustand'

/** 当前项目信息 */
export interface ProjectInfo {
  id: string
  code: string
  name: string
}

interface ProjectState {
  /** 当前选中项目 */
  currentProject: ProjectInfo | null
  /** 当前项目 API Key（业务接口鉴权用） */
  apiKey: string | null
  /** 设置当前项目，同步写入 localStorage（current_api_key、current_project） */
  setCurrentProject: (project: ProjectInfo, apiKey: string) => void
  /** 清空当前项目（退出项目时调用） */
  clearCurrentProject: () => void
}

// localStorage 存储键
const CURRENT_PROJECT_STORAGE = 'current_project'
const CURRENT_API_KEY_STORAGE = 'current_api_key'

/** 从 localStorage 初始化当前项目（刷新页面后保持登录态） */
function loadCurrentProject(): ProjectInfo | null {
  try {
    const raw = localStorage.getItem(CURRENT_PROJECT_STORAGE)
    return raw ? (JSON.parse(raw) as ProjectInfo) : null
  } catch {
    return null
  }
}

function loadApiKey(): string | null {
  return localStorage.getItem(CURRENT_API_KEY_STORAGE)
}

// 项目上下文 store：保存当前项目与 API Key，刷新后自动恢复
export const useProjectStore = create<ProjectState>((set) => ({
  currentProject: loadCurrentProject(),
  apiKey: loadApiKey(),
  setCurrentProject: (project, apiKey) => {
    // 同步持久化到 localStorage，便于 axios 拦截器读取
    localStorage.setItem(CURRENT_PROJECT_STORAGE, JSON.stringify(project))
    localStorage.setItem(CURRENT_API_KEY_STORAGE, apiKey)
    set({ currentProject: project, apiKey })
  },
  clearCurrentProject: () => {
    localStorage.removeItem(CURRENT_PROJECT_STORAGE)
    localStorage.removeItem(CURRENT_API_KEY_STORAGE)
    set({ currentProject: null, apiKey: null })
  }
}))

/** selector hook：获取当前项目（便于组件订阅） */
export function useCurrentProject(): ProjectInfo | null {
  return useProjectStore((state) => state.currentProject)
}
