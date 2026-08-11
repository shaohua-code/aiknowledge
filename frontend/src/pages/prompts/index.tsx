import { useState } from 'react'
import { Button, Space, Table, Popconfirm, Tag, Card, Typography, Empty } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import type { Prompt, CreatePromptPayload } from '@/api/prompts'
import {
  usePrompts,
  useActivePrompt,
  useCreatePrompt,
  useUpdatePrompt,
  useActivatePrompt,
  useDeletePrompt
} from './hooks/usePrompts'
import PromptEditModal from './components/PromptEditModal'
import { useCurrentProject } from '@/stores/project'

const { Paragraph } = Typography

/** 截断 systemPrompt 用于表格展示 */
function truncate(text: string, len = 60): string {
  if (!text) return '-'
  return text.length > len ? text.slice(0, len) + '...' : text
}

/**
 * 提示词管理页
 * - 顶部显示当前 active 版本卡片
 * - 版本列表表格：version、isActive、systemPrompt 截断、createdAt
 * - 行操作：查看详情、激活、编辑（非 active）、删除（非 active）
 * - "创建新版本"按钮
 */
export default function PromptsPage() {
  const currentProject = useCurrentProject()

  // 弹窗状态
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Prompt | null>(null)

  const { data: activePrompt } = useActivePrompt()
  const { data: prompts = [], isLoading } = usePrompts()
  const createMutation = useCreatePrompt()
  const updateMutation = useUpdatePrompt()
  const activateMutation = useActivatePrompt()
  const deleteMutation = useDeletePrompt()

  /** 打开新建弹窗 */
  function handleOpenCreate() {
    setEditing(null)
    setModalOpen(true)
  }

  /** 打开查看/编辑弹窗 */
  function handleView(record: Prompt) {
    setEditing(record)
    setModalOpen(true)
  }

  /** 弹窗提交统一入口（区分创建/更新） */
  async function handleSubmit(payload: CreatePromptPayload) {
    if (editing && !editing.isActive) {
      await updateMutation.mutateAsync({ versionId: editing.versionId, payload })
    } else {
      await createMutation.mutateAsync(payload)
    }
    setModalOpen(false)
    setEditing(null)
  }

  // 表格列定义
  const columns: ColumnsType<Prompt> = [
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 90,
      render: (v: number) => <span className="font-medium">v{v}</span>
    },
    {
      title: '状态',
      dataIndex: 'isActive',
      key: 'isActive',
      width: 100,
      render: (isActive: boolean) => (
        <Tag color={isActive ? 'green' : 'default'} className="!m-0">
          {isActive ? '当前激活' : '未激活'}
        </Tag>
      )
    },
    {
      title: '系统提示词',
      dataIndex: 'systemPrompt',
      key: 'systemPrompt',
      ellipsis: true,
      render: (v: string) => <span className="text-xs text-gray-600">{truncate(v)}</span>
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 170,
      render: (v: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-')
    },
    {
      title: '操作',
      key: 'action',
      width: 280,
      fixed: 'right',
      render: (_v, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => handleView(record)}>
            {record.isActive ? '查看详情' : '查看/编辑'}
          </Button>
          {!record.isActive && (
            <Popconfirm
              title="确认激活该版本？激活后原 active 版本将变为未激活"
              onConfirm={() => activateMutation.mutate(record.versionId)}
            >
              <Button type="link" size="small">激活</Button>
            </Popconfirm>
          )}
          {!record.isActive && (
            <Popconfirm
              title="确认删除该版本？"
              onConfirm={() => deleteMutation.mutate(record.versionId)}
            >
              <Button type="link" size="small" danger>删除</Button>
            </Popconfirm>
          )}
        </Space>
      )
    }
  ]

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部标题 */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex flex-col leading-tight">
          <span className="text-xl font-semibold text-gray-800">提示词管理</span>
          {currentProject && (
            <span className="text-sm text-gray-400">
              当前项目：{currentProject.name}（{currentProject.code}）
            </span>
          )}
        </div>
        <Button type="primary" onClick={handleOpenCreate}>创建新版本</Button>
      </div>

      {/* 当前 active 版本卡片 */}
      <Card
        size="small"
        className="mb-4 !border-green-200 !bg-green-50"
        title={
          <div className="flex items-center gap-2">
            <Tag color="green" className="!m-0">当前激活</Tag>
            {activePrompt && <span className="text-sm">v{activePrompt.version}</span>}
          </div>
        }
      >
        {activePrompt ? (
          <Paragraph className="!mb-0 !whitespace-pre-wrap !text-xs !text-gray-700">
            {activePrompt.systemPrompt}
          </Paragraph>
        ) : (
          <Empty description="暂无激活版本" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Card>

      {/* 版本列表表格 */}
      <Table
        rowKey="versionId"
        loading={isLoading}
        columns={columns}
        dataSource={prompts}
        pagination={{ pageSize: 10, showSizeChanger: true }}
        scroll={{ x: 900 }}
      />

      {/* 创建/编辑弹窗 */}
      <PromptEditModal
        open={modalOpen}
        initial={editing}
        onCancel={() => { setModalOpen(false); setEditing(null) }}
        onSubmit={handleSubmit}
      />
    </div>
  )
}
