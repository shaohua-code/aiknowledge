import { useEffect } from 'react'
import { Button, Card, Spin, Empty, Space, Form } from 'antd'
import type {
  ModelSettings,
  WebSearchSettings,
  PerformanceSettings,
  ProjectSettings
} from '@/api/project-settings'
import { useProjectSettings, useUpdateProjectSettings } from './hooks/useProjectSettings'
import ModelSettingsForm from './components/ModelSettingsForm'
import WebSearchSettingsForm from './components/WebSearchSettingsForm'
import PerformanceSettingsForm from './components/PerformanceSettingsForm'
import { useCurrentProject } from '@/stores/project'

/** 多行文本转数组（过滤空行与首尾空白） */
function textToArr(text?: string): string[] {
  return (text || '')
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
}

/**
 * 项目设置页
 * - 三个分区表单：模型设置 / Web 搜索设置 / 性能设置
 * - 从 GET /projects/{id} 加载，PATCH 保存
 * - 各表单独立 form 实例，保存时统一收集
 */
export default function ProjectSettingsPage() {
  const currentProject = useCurrentProject()
  const projectId = currentProject?.id

  const { data: project, isLoading } = useProjectSettings(projectId)
  const updateMutation = useUpdateProjectSettings(projectId)

  // 三个分区表单实例
  const [modelForm] = Form.useForm<ModelSettings>()
  const [webForm] = Form.useForm<
    WebSearchSettings & { allowedDomainsText: string; blockedDomainsText: string }
  >()
  const [perfForm] = Form.useForm<PerformanceSettings>()

  // 项目数据加载后回填三个表单
  useEffect(() => {
    if (project?.settings) {
      modelForm.setFieldsValue(project.settings.models || {})
      webForm.setFieldsValue({
        webSearchEnabled: project.settings.webSearch?.webSearchEnabled ?? false,
        allowedDomainsText: (project.settings.webSearch?.allowedDomains || []).join('\n'),
        blockedDomainsText: (project.settings.webSearch?.blockedDomains || []).join('\n')
      })
      perfForm.setFieldsValue(project.settings.performance || {})
    }
  }, [project, modelForm, webForm, perfForm])

  /** 回填表单为最近一次加载的值 */
  function resetForms() {
    if (project?.settings) {
      modelForm.setFieldsValue(project.settings.models || {})
      webForm.setFieldsValue({
        webSearchEnabled: project.settings.webSearch?.webSearchEnabled ?? false,
        allowedDomainsText: (project.settings.webSearch?.allowedDomains || []).join('\n'),
        blockedDomainsText: (project.settings.webSearch?.blockedDomains || []).join('\n')
      })
      perfForm.setFieldsValue(project.settings.performance || {})
    }
  }

  /** 保存：收集三个表单值并整体 PATCH settings */
  async function handleSave() {
    // 同步校验三个表单
    const [modelValues, webValues, perfValues] = await Promise.all([
      modelForm.validateFields(),
      webForm.validateFields(),
      perfForm.validateFields()
    ])

    // 组装 settings
    const settings: ProjectSettings = {
      models: modelValues as ModelSettings,
      webSearch: {
        webSearchEnabled: webValues.webSearchEnabled,
        allowedDomains: textToArr(webValues.allowedDomainsText),
        blockedDomains: textToArr(webValues.blockedDomainsText)
      },
      performance: perfValues as PerformanceSettings
    }

    await updateMutation.mutateAsync({ settings })
  }

  // 未选择项目时提示
  if (!projectId) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <Empty description="尚未选择项目，请返回项目列表选择" />
      </div>
    )
  }

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部标题 */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex flex-col leading-tight">
          <span className="text-xl font-semibold text-gray-800">项目设置</span>
          {currentProject && (
            <span className="text-sm text-gray-400">
              当前项目：{currentProject.name}（{currentProject.code}）
            </span>
          )}
        </div>
        <Space>
          <Button onClick={resetForms}>重置</Button>
          <Button type="primary" loading={updateMutation.isPending} onClick={handleSave}>
            保存设置
          </Button>
        </Space>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <Spin tip="加载设置..." />
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {/* 模型设置分区 */}
          <Card size="small" title="模型设置">
            <ModelSettingsForm form={modelForm} initial={project?.settings?.models} />
          </Card>

          {/* Web 搜索设置分区 */}
          <Card size="small" title="Web 搜索设置">
            <WebSearchSettingsForm form={webForm} initial={project?.settings?.webSearch} />
          </Card>

          {/* 性能设置分区 */}
          <Card size="small" title="性能设置">
            <PerformanceSettingsForm form={perfForm} initial={project?.settings?.performance} />
          </Card>
        </div>
      )}
    </div>
  )
}
