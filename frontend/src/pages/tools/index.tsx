import { useState } from 'react'
import { Button, Space, Table, Popconfirm, Tag, Input, Empty } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import type { ProjectTool, Tool, ToolTestResult } from '@/api/tools'
import {
  useTools,
  useProjectTools,
  useCreateProjectTool,
  useUpdateProjectTool,
  useDeleteProjectTool,
  useToggleProjectTool,
  useTestProjectTool
} from './hooks/useProjectTools'
import ProjectToolCreateModal from './components/ProjectToolCreateModal'
import ToolTestModal from './components/ToolTestModal'
import { useCurrentProject } from '@/stores/project'

/**
 * 工具配置页
 * - 顶部展示全局工具列表（只读，管理密钥维护）
 * - 项目工具配置表格：toolCode、enabled、config、createdAt
 * - 行操作：编辑、测试、删除、启停
 * - 顶部"添加工具"按钮
 * - 查询条件集中在 useState 对象（keyword 本地过滤）
 */
export default function ToolsPage() {
  const currentProject = useCurrentProject()

  // 查询条件集中管理
  const [formData, setFormData] = useState<{ keyword: string }>({ keyword: '' })

  // 创建/编辑弹窗状态
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<ProjectTool | null>(null)
  // 测试弹窗状态
  const [testOpen, setTestOpen] = useState(false)
  const [testTool, setTestTool] = useState<ProjectTool | null>(null)

  // 全局工具（只读）与项目工具配置
  const { data: globalTools = [], isLoading: toolsLoading } = useTools()
  const { data: projectTools = [], isLoading } = useProjectTools()

  const createMutation = useCreateProjectTool()
  const updateMutation = useUpdateProjectTool()
  const deleteMutation = useDeleteProjectTool()
  const toggleMutation = useToggleProjectTool()
  const testMutation = useTestProjectTool()

  // 已绑定的工具编码（用于禁用已选项）
  const existingToolCodes = projectTools.map((t) => t.toolCode)

  // 工具编码 → 全局工具映射，便于展示名称与 schema
  const toolMap = new Map<string, Tool>(globalTools.map((t) => [t.code, t]))

  // 前端本地 keyword 过滤（按 toolCode / toolName 模糊匹配）
  const filteredData = projectTools.filter((item) => {
    if (!formData.keyword) return true
    const kw = formData.keyword.toLowerCase()
    const name = toolMap.get(item.toolCode)?.name || item.toolName || ''
    return (
      item.toolCode.toLowerCase().includes(kw) ||
      name.toLowerCase().includes(kw)
    )
  })

  /** 打开新建弹窗 */
  function handleOpenCreate() {
    setEditing(null)
    setCreateOpen(true)
  }

  /** 打开编辑弹窗 */
  function handleEdit(record: ProjectTool) {
    setEditing(record)
    setCreateOpen(true)
  }

  /** 弹窗提交统一入口（区分创建/更新） */
  async function handleSubmit(payload: { toolCode: string; enabled?: boolean; config?: string }) {
    if (editing) {
      await updateMutation.mutateAsync({
        toolCode: editing.toolCode,
        payload: { enabled: payload.enabled, config: payload.config }
      })
    } else {
      await createMutation.mutateAsync(payload)
    }
    setCreateOpen(false)
    setEditing(null)
  }

  /** 打开测试弹窗 */
  function handleTest(record: ProjectTool) {
    setTestTool(record)
    setTestOpen(true)
  }

  /** 执行测试 */
  async function handleTestRun(payload: { inputs: Record<string, unknown> }): Promise<ToolTestResult> {
    return await testMutation.mutateAsync({ toolCode: testTool!.toolCode, payload })
  }

  // 表格列定义
  const columns: ColumnsType<ProjectTool> = [
    {
      title: '工具编码',
      dataIndex: 'toolCode',
      key: 'toolCode',
      width: 180,
      render: (v: string) => <Tag color="blue" className="!m-0">{v}</Tag>
    },
    {
      title: '工具名称',
      key: 'toolName',
      width: 200,
      render: (_v, record) => toolMap.get(record.toolCode)?.name || record.toolName || '-'
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 90,
      render: (enabled: boolean) => (
        <Tag color={enabled ? 'green' : 'default'} className="!m-0">
          {enabled ? '启用' : '停用'}
        </Tag>
      )
    },
    {
      title: '配置 (JSON)',
      dataIndex: 'config',
      key: 'config',
      ellipsis: true,
      render: (v?: string) => (v ? <span className="text-xs text-gray-500">{v}</span> : '-')
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 170,
      render: (v?: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-')
    },
    {
      title: '操作',
      key: 'action',
      width: 280,
      fixed: 'right',
      render: (_v, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => handleEdit(record)}>编辑</Button>
          <Button type="link" size="small" onClick={() => handleTest(record)}>测试</Button>
          <Popconfirm
            title={record.enabled ? '确认停用该工具？' : '确认启用该工具？'}
            onConfirm={() => toggleMutation.mutate({ toolCode: record.toolCode, enabled: record.enabled })}
          >
            <Button type="link" size="small" danger={record.enabled}>
              {record.enabled ? '停用' : '启用'}
            </Button>
          </Popconfirm>
          <Popconfirm
            title="确认删除该工具配置？"
            onConfirm={() => deleteMutation.mutate(record.toolCode)}
          >
            <Button type="link" size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      )
    }
  ]

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部标题 */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex flex-col leading-tight">
          <span className="text-xl font-semibold text-gray-800">工具配置</span>
          {currentProject && (
            <span className="text-sm text-gray-400">
              当前项目：{currentProject.name}（{currentProject.code}）
            </span>
          )}
        </div>
        <Button type="primary" onClick={handleOpenCreate} disabled={globalTools.length === 0}>
          添加工具
        </Button>
      </div>

      {/* 全局工具列表（只读展示） */}
      <div className="mb-4 rounded border border-gray-200 bg-white p-3">
        <div className="mb-2 text-sm font-medium text-gray-700">全局可用工具（密钥由管理端统一维护，此处只读）</div>
        {toolsLoading ? (
          <div className="text-sm text-gray-400">加载中...</div>
        ) : globalTools.length === 0 ? (
          <Empty description="暂无全局工具" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <div className="flex flex-wrap gap-2">
            {globalTools.map((t) => (
              <Tag key={t.code} color="blue" className="!m-0">
                {t.name}（{t.code}）
                {t.requiresSecret && (
                  <span className="ml-1 text-xs">
                    {t.secretConfigured ? '✓密钥' : '✗密钥'}
                  </span>
                )}
              </Tag>
            ))}
          </div>
        )}
      </div>

      {/* 查询条件区 */}
      <div className="mb-4 flex items-center gap-4">
        <Input
          placeholder="按工具编码/名称搜索"
          allowClear
          value={formData.keyword}
          onChange={(e) => setFormData((f) => ({ ...f, keyword: e.target.value }))}
          style={{ width: 260 }}
        />
      </div>

      {/* 项目工具配置表格 */}
      <Table
        rowKey="toolCode"
        loading={isLoading}
        columns={columns}
        dataSource={filteredData}
        pagination={{ pageSize: 10, showSizeChanger: true }}
        scroll={{ x: 1100 }}
      />

      {/* 创建/编辑弹窗 */}
      <ProjectToolCreateModal
        open={createOpen}
        initial={editing}
        tools={globalTools}
        existingToolCodes={existingToolCodes}
        onCancel={() => { setCreateOpen(false); setEditing(null) }}
        onSubmit={handleSubmit}
      />

      {/* 测试弹窗 */}
      <ToolTestModal
        open={testOpen}
        toolCode={testTool?.toolCode || ''}
        toolName={testTool ? toolMap.get(testTool.toolCode)?.name || testTool.toolName : ''}
        inputSchema={testTool ? toolMap.get(testTool.toolCode)?.inputSchema : undefined}
        onCancel={() => { setTestOpen(false); setTestTool(null) }}
        onTest={handleTestRun}
      />
    </div>
  )
}
