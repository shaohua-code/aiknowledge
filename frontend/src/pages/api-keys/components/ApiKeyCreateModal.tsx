import { useEffect, useState } from 'react'
import { Modal, Form, Input, Select, DatePicker } from 'antd'
import type { CreateApiKeyPayload } from '@/api/api-keys'

interface ApiKeyCreateModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 取消回调 */
  onCancel: () => void
  /** 提交回调，返回包含明文密钥的实体 */
  onSubmit: (payload: CreateApiKeyPayload) => Promise<void>
}

// 环境选项
const ENVIRONMENT_OPTIONS = [
  { label: '开发 (development)', value: 'development' },
  { label: '测试 (staging)', value: 'staging' },
  { label: '生产 (production)', value: 'production' }
]

// 常用 scope 选项（与后端约定，可多选也可自定义输入）
const SCOPE_OPTIONS = [
  { label: 'research:run', value: 'research:run' },
  { label: 'research:feedback', value: 'research:feedback' },
  { label: 'knowledge:read', value: 'knowledge:read' },
  { label: 'knowledge:write', value: 'knowledge:write' },
  { label: 'documents:read', value: 'documents:read' },
  { label: 'documents:write', value: 'documents:write' },
  { label: 'retrieval:search', value: 'retrieval:search' }
]

/**
 * 创建 API Key 弹窗
 * - 字段：name、environment、scopes(多选)、expiresAt(可选)
 * - 提交后由父组件展示明文密钥
 */
export default function ApiKeyCreateModal({
  open,
  onCancel,
  onSubmit
}: ApiKeyCreateModalProps) {
  const [form] = Form.useForm<CreateApiKeyPayload & { expiresAtPicker?: string }>()
  // scopes 支持自定义输入
  const [scopes, setScopes] = useState<string[]>([])

  // 弹窗打开时重置
  useEffect(() => {
    if (open) {
      form.resetFields()
      setScopes([])
    }
  }, [open, form])

  async function handleOk() {
    const raw = await form.validateFields()
    const payload: CreateApiKeyPayload = {
      name: raw.name,
      environment: raw.environment,
      scopes,
      expiresAt: raw.expiresAtPicker
        ? new Date(raw.expiresAtPicker).toISOString()
        : undefined
    }
    await onSubmit(payload)
    form.resetFields()
    setScopes([])
  }

  return (
    <Modal
      title="创建 API Key"
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      okText="创建"
      cancelText="取消"
      destroyOnClose
      width={520}
    >
      <Form form={form} layout="vertical" className="mt-2">
        <Form.Item
          name="name"
          label="名称"
          rules={[{ required: true, message: '请输入名称' }]}
        >
          <Input placeholder="如 生产环境调用密钥" />
        </Form.Item>
        <Form.Item
          name="environment"
          label="环境"
          rules={[{ required: true, message: '请选择环境' }]}
        >
          <Select options={ENVIRONMENT_OPTIONS} placeholder="选择环境" />
        </Form.Item>
        <Form.Item
          label="权限范围 (scopes)"
          required
          help="可多选或自定义输入（回车添加）"
        >
          <Select
            mode="tags"
            placeholder="选择或输入 scope"
            value={scopes}
            onChange={setScopes}
            options={SCOPE_OPTIONS}
            tokenSeparators={[',', ' ']}
          />
        </Form.Item>
        <Form.Item
          name="expiresAtPicker"
          label="过期时间（可选）"
          help="留空则永不过期"
        >
          <DatePicker
            showTime
            style={{ width: '100%' }}
            placeholder="选择过期时间"
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}
