import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, Select, Space, Table, Popconfirm, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQueryClient } from '@tanstack/react-query'
import type { Project, CreateProjectPayload } from '@/api/projects'
import { useProjects, useCreateProject, useToggleProject } from './hooks/useProjects'
import ProjectCreateModal from './components/ProjectCreateModal'
import ProjectStatusTag from './components/ProjectStatusTag'
import ApiKeySetup from '@/components/ApiKeySetup'
import ProjectApiKeyInputModal from '@/components/ProjectApiKeyInputModal'
import { useProjectStore } from '@/stores/project'

/**
 * 项目列表页
 * - 表格展示项目（code、name、status、createdAt）
 * - 顶部"创建项目"按钮 + 管理密钥设置
 * - 行操作：进入项目（输入 API Key）、停用/启用
 * - 查询条件集中：keyword、status
 */
export default function ProjectsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const setCurrentProject = useProjectStore((s) => s.setCurrentProject)

  // 查询条件集中在一个对象
  const [formData, setFormData] = useState<{ keyword: string; status: string }>({
    keyword: '',
    status: ''
  })

  // 创建弹窗状态
  const [createOpen, setCreateOpen] = useState(false)
  // 进入项目时的 API Key 输入弹窗
  const [apiKeyModalOpen, setApiKeyModalOpen] = useState(false)
  // 待进入的项目（暂存用户点击"进入"的项目）
  const [pendingProject, setPendingProject] = useState<Project | null>(null)

  // 调用 listProjects，仅以 status 作为后端过滤参数；keyword 在前端本地过滤
  const { data, isLoading } = useProjects({ status: formData.status || undefined })
  const createMutation = useCreateProject()
  const toggleMutation = useToggleProject()

  // 前端本地 keyword 过滤（后端列表接口未提供 keyword 参数）
  const filteredData = (data || []).filter((item) => {
    if (!formData.keyword) return true
    const kw = formData.keyword.toLowerCase()
    return (
      item.code.toLowerCase().includes(kw) ||
      item.name.toLowerCase().includes(kw) ||
      (item.description || '').toLowerCase().includes(kw)
    )
  })

  // 表格列定义
  const columns: ColumnsType<Project> = [
    { title: '项目编码', dataIndex: 'code', key: 'code', width: 180 },
    { title: '项目名称', dataIndex: 'name', key: 'name' },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: Project['status']) => <ProjectStatusTag status={status} />
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 180,
      render: (v: string) => (v ? new Date(v).toLocaleString() : '-')
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_v, record) => (
        <Space>
          <Button
            type="link"
            size="small"
            disabled={record.status === 'disabled'}
            onClick={() => handleEnterProject(record)}
          >
            进入项目
          </Button>
          <Popconfirm
            title={record.status === 'active' ? '确认停用该项目？' : '确认启用该项目？'}
            onConfirm={() => handleToggle(record)}
          >
            <Button type="link" size="small" danger={record.status === 'active'}>
              {record.status === 'active' ? '停用' : '启用'}
            </Button>
          </Popconfirm>
        </Space>
      )
    }
  ]

  /** 点击"进入项目"：暂存项目并弹出 API Key 输入框 */
  function handleEnterProject(project: Project) {
    setPendingProject(project)
    setApiKeyModalOpen(true)
  }

  /** 确认 API Key：写入 store + localStorage，清空缓存并跳转项目内页 */
  function handleConfirmApiKey(apiKey: string) {
    if (!pendingProject) return
    // 切换项目前清空 TanStack Query 项目相关缓存
    queryClient.clear()
    // 写入项目上下文（同步 localStorage）
    setCurrentProject(
      { id: pendingProject.id, code: pendingProject.code, name: pendingProject.name },
      apiKey
    )
    setApiKeyModalOpen(false)
    setPendingProject(null)
    message.success(`已进入项目：${pendingProject.name}`)
    navigate(`/projects/${pendingProject.id}/knowledge-bases`)
  }

  /** 停用/启用项目 */
  function handleToggle(project: Project) {
    const targetStatus = project.status === 'active' ? 'disabled' : 'active'
    toggleMutation.mutate({ projectId: project.id, targetStatus })
  }

  /** 创建项目提交 */
  async function handleCreate(payload: CreateProjectPayload) {
    await createMutation.mutateAsync(payload)
    setCreateOpen(false)
  }

  return (
    <div className="flex h-full w-full flex-col p-6">
      {/* 顶部操作区：标题 + 创建项目 + 管理密钥设置 */}
      <div className="mb-4 flex items-center justify-between">
        <div className="text-xl font-semibold text-gray-800">项目管理</div>
        <Space>
          <ApiKeySetup triggerText="设置管理密钥" />
          <Button type="primary" onClick={() => setCreateOpen(true)}>创建项目</Button>
        </Space>
      </div>

      {/* 查询条件区 */}
      <div className="mb-4 flex items-center gap-4">
        <Input
          placeholder="按编码/名称/描述搜索"
          allowClear
          value={formData.keyword}
          onChange={(e) => setFormData((f) => ({ ...f, keyword: e.target.value }))}
          style={{ width: 240 }}
        />
        <Select
          placeholder="状态筛选"
          allowClear
          value={formData.status || undefined}
          onChange={(v) => setFormData((f) => ({ ...f, status: v || '' }))}
          style={{ width: 160 }}
          options={[
            { label: '启用', value: 'active' },
            { label: '停用', value: 'disabled' }
          ]}
        />
      </div>

      {/* 项目表格 */}
      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={filteredData}
        pagination={{ pageSize: 10, showSizeChanger: true }}
      />

      {/* 创建项目弹窗 */}
      <ProjectCreateModal
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onSubmit={handleCreate}
      />

      {/* 进入项目时的 API Key 输入弹窗 */}
      <ProjectApiKeyInputModal
        open={apiKeyModalOpen}
        projectName={pendingProject?.name}
        onCancel={() => {
          setApiKeyModalOpen(false)
          setPendingProject(null)
        }}
        onConfirm={handleConfirmApiKey}
      />
    </div>
  )
}
