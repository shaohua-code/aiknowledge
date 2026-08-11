import { useState } from 'react'
import { Button, Space, Table, Popconfirm, Tag, Select, Input, Empty } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import type { ApiKey, ApiKeyStatus, CreateApiKeyPayload } from '@/api/api-keys'
import {
  useApiKeys,
  useCreateApiKey,
  useRevokeApiKey,
  useRotateApiKey
} from './hooks/useApiKeys'
import ApiKeyCreateModal from './components/ApiKeyCreateModal'
import ApiKeyRevealModal from './components/ApiKeyRevealModal'
import { useCurrentProject } from '@/stores/project'

// 状态文案与颜色映射
const STATUS_META: Record<ApiKeyStatus, { color: string; label: string }> = {
  active: { color: 'green', label: '正常' },
  revoked: { color: 'red', label: '已停用' },
  expired: { color: 'default', label: '已过期' }
}

/**
 * API Key 管理页
 * - 表格：name、environment、keyPrefix、scopes、lastUsedAt、expiresAt、status
 * - 行操作：停用、轮换（弹窗显示新明文）
 * - 顶部"创建 API Key"按钮
 * - 查询条件集中在 useState 对象（status、keyword）
 * - 从 project store 获取当前 projectId 用于路径
 */
export default function ApiKeysPage() {
  const currentProject = useCurrentProject()
  const projectId = currentProject?.id

  // 查询条件集中管理
  const [formData, setFormData] = useState<{ status: string; keyword: string }>({
    status: '',
    keyword: ''
  })

  // 创建弹窗与明文展示弹窗
  const [createOpen, setCreateOpen] = useState(false)
  const [revealOpen, setRevealOpen] = useState(false)
  const [revealKey, setRevealKey] = useState<string>('')
  const [revealScene, setRevealScene] = useState<'create' | 'rotate'>('create')

  const { data: apiKeys = [], isLoading } = useApiKeys(
    projectId,
    formData.status ? { status: formData.status as ApiKeyStatus } : undefined
  )
  const createMutation = useCreateApiKey(projectId)
  const revokeMutation = useRevokeApiKey(projectId)
  const rotateMutation = useRotateApiKey(projectId)

  // 前端本地 keyword 过滤（按 name / keyPrefix 模糊匹配）
  const filteredData = apiKeys.filter((item) => {
    if (!formData.keyword) return true
    const kw = formData.keyword.toLowerCase()
    return (
      item.name.toLowerCase().includes(kw) ||
      (item.keyPrefix || '').toLowerCase().includes(kw)
    )
  })

  /** 打开创建弹窗 */
  function handleOpenCreate() {
    setCreateOpen(true)
  }

  /** 创建提交，返回明文密钥后展示 */
  async function handleCreate(payload: CreateApiKeyPayload) {
    const data = await createMutation.mutateAsync(payload)
    setCreateOpen(false)
    if (data?.plaintextKey) {
      setRevealKey(data.plaintextKey)
      setRevealScene('create')
      setRevealOpen(true)
    }
  }

  /** 轮换，返回新明文密钥后展示 */
  async function handleRotate(keyId: string) {
    const data = await rotateMutation.mutateAsync(keyId)
    if (data?.plaintextKey) {
      setRevealKey(data.plaintextKey)
      setRevealScene('rotate')
      setRevealOpen(true)
    }
  }

  // 表格列定义
  const columns: ColumnsType<ApiKey> = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 160 },
    {
      title: '环境',
      dataIndex: 'environment',
      key: 'environment',
      width: 120,
      render: (v: string) => <Tag color="blue" className="!m-0">{v}</Tag>
    },
    {
      title: 'Key 前缀',
      dataIndex: 'keyPrefix',
      key: 'keyPrefix',
      width: 160,
      render: (v?: string) => (v ? <span className="font-mono text-xs">{v}...</span> : '-')
    },
    {
      title: '权限范围',
      dataIndex: 'scopes',
      key: 'scopes',
      render: (scopes: string[]) => (
        <div className="flex flex-wrap gap-1">
          {(scopes || []).map((s) => (
            <Tag key={s} className="!m-0" color="default">{s}</Tag>
          ))}
        </div>
      )
    },
    {
      title: '上次使用',
      dataIndex: 'lastUsedAt',
      key: 'lastUsedAt',
      width: 170,
      render: (v?: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '从未使用')
    },
    {
      title: '过期时间',
      dataIndex: 'expiresAt',
      key: 'expiresAt',
      width: 170,
      render: (v?: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '永不过期')
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: ApiKeyStatus) => (
        <Tag color={STATUS_META[status]?.color || 'default'} className="!m-0">
          {STATUS_META[status]?.label || status}
        </Tag>
      )
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      fixed: 'right',
      render: (_v, record) => (
        <Space>
          {record.status === 'active' && (
            <>
              <Popconfirm
                title="确认轮换该 Key？轮换后旧 Key 立即失效，新明文仅展示一次"
                onConfirm={() => handleRotate(record.id)}
              >
                <Button type="link" size="small">轮换</Button>
              </Popconfirm>
              <Popconfirm
                title="确认停用该 Key？停用后无法恢复"
                onConfirm={() => revokeMutation.mutate(record.id)}
              >
                <Button type="link" size="small" danger>停用</Button>
              </Popconfirm>
            </>
          )}
          {record.status !== 'active' && <span className="text-xs text-gray-400">-</span>}
        </Space>
      )
    }
  ]

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
          <span className="text-xl font-semibold text-gray-800">API Key 管理</span>
          {currentProject && (
            <span className="text-sm text-gray-400">
              当前项目：{currentProject.name}（{currentProject.code}）
            </span>
          )}
        </div>
        <Button type="primary" onClick={handleOpenCreate}>创建 API Key</Button>
      </div>

      {/* 查询条件区 */}
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Select
          placeholder="状态筛选"
          allowClear
          value={formData.status || undefined}
          onChange={(v) => setFormData((f) => ({ ...f, status: v || '' }))}
          style={{ width: 160 }}
          options={[
            { label: '正常', value: 'active' },
            { label: '已停用', value: 'revoked' },
            { label: '已过期', value: 'expired' }
          ]}
        />
        <Input
          placeholder="按名称/Key 前缀搜索"
          allowClear
          value={formData.keyword}
          onChange={(e) => setFormData((f) => ({ ...f, keyword: e.target.value }))}
          style={{ width: 260 }}
        />
      </div>

      {/* API Key 表格 */}
      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={filteredData}
        pagination={{ pageSize: 10, showSizeChanger: true }}
        scroll={{ x: 1200 }}
      />

      {/* 创建弹窗 */}
      <ApiKeyCreateModal
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onSubmit={handleCreate}
      />

      {/* 明文展示弹窗（仅展示一次） */}
      <ApiKeyRevealModal
        open={revealOpen}
        scene={revealScene}
        plaintextKey={revealKey}
        onCancel={() => { setRevealOpen(false); setRevealKey('') }}
      />
    </div>
  )
}
