import { useEffect } from 'react'
import { Modal, Form, Input } from 'antd'
import type { Prompt, CreatePromptPayload } from '@/api/prompts'

interface PromptEditModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 编辑模式下的初始数据（active 版本只读不允许编辑） */
  initial?: Prompt | null
  /** 取消回调 */
  onCancel: () => void
  /** 提交回调（创建/更新统一入口） */
  onSubmit: (payload: CreatePromptPayload) => Promise<void>
}

/**
 * 创建/编辑提示词版本弹窗
 * - 字段：systemPrompt、evidenceRules(JSON)、outputSchema(JSON)、prohibitions(多行)、riskTemplate
 * - prohibitions 列表以多行文本输入，提交前转为数组
 */
export default function PromptEditModal({
  open,
  initial,
  onCancel,
  onSubmit
}: PromptEditModalProps) {
  const [form] = Form.useForm<CreatePromptPayload & { prohibitionsText: string }>()
  const isEdit = !!initial
  // active 版本禁止编辑（只读查看用 Drawer 即可，此处仅允许非 active 编辑）
  const readOnly = isEdit && initial?.isActive === true

  // 弹窗打开时回填表单
  useEffect(() => {
    if (open) {
      if (initial) {
        // 编辑模式：数组字段转多行文本回填
        form.setFieldsValue({
          systemPrompt: initial.systemPrompt,
          evidenceRules: initial.evidenceRules || '',
          outputSchema: initial.outputSchema || '',
          prohibitionsText: (initial.prohibitions || []).join('\n'),
          riskTemplate: initial.riskTemplate || ''
        })
      } else {
        // 新建模式：重置
        form.resetFields()
      }
    }
  }, [open, initial, form])

  async function handleOk() {
    const raw = await form.validateFields()
    // 多行文本拆分为数组
    const prohibitions = (raw.prohibitionsText || '')
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
    const payload: CreatePromptPayload = {
      systemPrompt: raw.systemPrompt,
      evidenceRules: raw.evidenceRules,
      outputSchema: raw.outputSchema,
      prohibitions,
      riskTemplate: raw.riskTemplate
    }
    await onSubmit(payload)
    form.resetFields()
  }

  return (
    <Modal
      title={isEdit ? (readOnly ? '查看提示词版本' : '编辑提示词版本') : '创建新版本'}
      open={open}
      onOk={readOnly ? onCancel : handleOk}
      onCancel={onCancel}
      okText={readOnly ? '关闭' : (isEdit ? '保存' : '创建')}
      cancelText={readOnly ? '取消' : '取消'}
      okButtonProps={{ disabled: readOnly }}
      destroyOnClose
      width={720}
    >
      <Form form={form} layout="vertical" className="mt-2">
        <Form.Item
          name="systemPrompt"
          label="系统提示词"
          rules={[{ required: true, message: '请输入系统提示词' }]}
        >
          <Input.TextArea rows={6} placeholder="系统提示词内容" disabled={readOnly} />
        </Form.Item>
        <Form.Item
          name="evidenceRules"
          label="证据规则 (JSON)"
          extra='如 {"minScore":0.6,"maxCount":10}'
        >
          <Input.TextArea rows={3} placeholder='{"minScore":0.6,"maxCount":10}' disabled={readOnly} />
        </Form.Item>
        <Form.Item
          name="outputSchema"
          label="输出 Schema (JSON)"
          extra='如 {"type":"object","properties":{}}'
        >
          <Input.TextArea rows={3} placeholder='{"type":"object"}' disabled={readOnly} />
        </Form.Item>
        <Form.Item
          name="prohibitionsText"
          label="禁止事项"
          extra="每行一条"
        >
          <Input.TextArea rows={3} placeholder={'禁止编造数据\n禁止输出敏感信息'} disabled={readOnly} />
        </Form.Item>
        <Form.Item
          name="riskTemplate"
          label="风险提示模板"
        >
          <Input.TextArea rows={2} placeholder="风险提示模板文本" disabled={readOnly} />
        </Form.Item>
      </Form>
    </Modal>
  )
}
