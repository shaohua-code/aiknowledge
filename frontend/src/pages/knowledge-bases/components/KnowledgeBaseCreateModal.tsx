import { useEffect } from 'react'
import { Modal, Form, Input, InputNumber, Select } from 'antd'
import type { CreateKnowledgeBasePayload } from '@/api/knowledge-bases'

interface KnowledgeBaseCreateModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 取消回调 */
  onCancel: () => void
  /** 提交回调 */
  onSubmit: (payload: CreateKnowledgeBasePayload) => Promise<void>
}

// 常用 embedding 模型预设（可选）
const EMBEDDING_MODEL_OPTIONS = [
  { label: 'text-embedding-3-small', value: 'text-embedding-3-small' },
  { label: 'text-embedding-3-large', value: 'text-embedding-3-large' },
  { label: 'text-embedding-ada-002', value: 'text-embedding-ada-002' }
]

/**
 * 创建知识库弹窗
 * - 表单字段：code、name、description、embeddingModel、embeddingDimension
 * - 打开时重置表单
 */
export default function KnowledgeBaseCreateModal({ open, onCancel, onSubmit }: KnowledgeBaseCreateModalProps) {
  const [form] = Form.useForm<CreateKnowledgeBasePayload>()

  // 弹窗打开时重置表单
  useEffect(() => {
    if (open) {
      form.resetFields()
      // 默认维度 1536（ada-002 / 3-small 常用）
      form.setFieldsValue({ embeddingDimension: 1536 })
    }
  }, [open, form])

  async function handleOk() {
    const values = await form.validateFields()
    await onSubmit(values)
    form.resetFields()
  }

  return (
    <Modal
      title="创建知识库"
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      okText="创建"
      cancelText="取消"
      destroyOnClose
    >
      <Form form={form} layout="vertical" className="mt-2">
        <Form.Item
          name="code"
          label="知识库编码"
          rules={[
            { required: true, message: '请输入知识库编码' },
            { pattern: /^[a-z0-9-]+$/, message: '仅支持小写字母、数字、短横线' }
          ]}
        >
          <Input placeholder="如 fund-research" />
        </Form.Item>
        <Form.Item
          name="name"
          label="知识库名称"
          rules={[{ required: true, message: '请输入知识库名称' }]}
        >
          <Input placeholder="如 基金研究知识库" />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input.TextArea placeholder="选填，知识库用途说明" rows={2} />
        </Form.Item>
        <Form.Item name="embeddingModel" label="Embedding 模型">
          <Select
            placeholder="选填，默认走后端配置"
            allowClear
            options={EMBEDDING_MODEL_OPTIONS}
          />
        </Form.Item>
        <Form.Item name="embeddingDimension" label="向量维度">
          <InputNumber
            placeholder="选填，如 1536"
            min={1}
            max={8192}
            className="!w-full"
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}
