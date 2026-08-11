import { useEffect } from 'react'
import { Modal, Form, Input } from 'antd'
import type { CreateProjectPayload } from '@/api/projects'

interface ProjectCreateModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 取消回调 */
  onCancel: () => void
  /** 提交回调，返回 Promise 用于按钮 loading */
  onSubmit: (payload: CreateProjectPayload) => Promise<void>
}

/**
 * 创建项目弹窗
 * - 表单字段：code、name、description
 * - 打开时重置表单
 */
export default function ProjectCreateModal({ open, onCancel, onSubmit }: ProjectCreateModalProps) {
  const [form] = Form.useForm<CreateProjectPayload>()

  // 弹窗打开时重置表单
  useEffect(() => {
    if (open) {
      form.resetFields()
    }
  }, [open, form])

  async function handleOk() {
    const values = await form.validateFields()
    await onSubmit(values)
    form.resetFields()
  }

  return (
    <Modal
      title="创建项目"
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
          label="项目编码"
          rules={[
            { required: true, message: '请输入项目编码' },
            { pattern: /^[a-z0-9-]+$/, message: '仅支持小写字母、数字、短横线' }
          ]}
        >
          <Input placeholder="如 ai-fund" />
        </Form.Item>
        <Form.Item
          name="name"
          label="项目名称"
          rules={[{ required: true, message: '请输入项目名称' }]}
        >
          <Input placeholder="如 智能基金研究" />
        </Form.Item>
        <Form.Item name="description" label="项目描述">
          <Input.TextArea placeholder="选填，项目用途说明" rows={3} />
        </Form.Item>
      </Form>
    </Modal>
  )
}
