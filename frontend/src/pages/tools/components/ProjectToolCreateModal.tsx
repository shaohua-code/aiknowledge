import { useEffect, useMemo } from 'react'
import { Modal, Form, Select, Switch, Input, Alert } from 'antd'
import type { Tool, ProjectTool, UpsertProjectToolPayload } from '@/api/tools'

interface ProjectToolCreateModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 编辑模式下的初始数据 */
  initial?: ProjectTool | null
  /** 全局工具列表（只读供选择） */
  tools: Tool[]
  /** 已被绑定的工具编码（编辑时排除自身），用于禁用已选项 */
  existingToolCodes: string[]
  /** 取消回调 */
  onCancel: () => void
  /** 提交回调（创建/更新统一入口） */
  onSubmit: (payload: UpsertProjectToolPayload & { toolCode: string }) => Promise<void>
}

/**
 * 添加/编辑项目工具弹窗
 * - 创建模式：从全局工具列表选择 toolCode，并填写 config JSON
 * - 编辑模式：toolCode 不可改，仅可改 enabled 与 config
 */
export default function ProjectToolCreateModal({
  open,
  initial,
  tools,
  existingToolCodes,
  onCancel,
  onSubmit
}: ProjectToolCreateModalProps) {
  const [form] = Form.useForm<UpsertProjectToolPayload & { toolCode: string }>()
  const isEdit = !!initial

  // 弹窗打开时回填表单
  useEffect(() => {
    if (open) {
      if (initial) {
        // 编辑模式：回填现有配置
        form.setFieldsValue({
          toolCode: initial.toolCode,
          enabled: initial.enabled,
          config: initial.config || ''
        })
      } else {
        // 新建模式：重置并填默认值
        form.resetFields()
        form.setFieldsValue({ enabled: true, config: '' })
      }
    }
  }, [open, initial, form])

  // 当前选中的工具，用于展示描述与是否需要密钥提示
  const selectedToolCode = Form.useWatch('toolCode', form)
  const selectedTool = useMemo(
    () => tools.find((t) => t.code === selectedToolCode),
    [tools, selectedToolCode]
  )

  async function handleOk() {
    const raw = await form.validateFields()
    await onSubmit({
      toolCode: raw.toolCode,
      enabled: raw.enabled,
      config: raw.config
    })
    form.resetFields()
  }

  return (
    <Modal
      title={isEdit ? '编辑工具配置' : '添加工具'}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      okText={isEdit ? '保存' : '添加'}
      cancelText="取消"
      destroyOnClose
      width={560}
    >
      <Form form={form} layout="vertical" className="mt-2">
        <Form.Item
          name="toolCode"
          label="选择工具"
          rules={[{ required: true, message: '请选择工具' }]}
        >
          <Select
            placeholder="从全局工具列表选择"
            disabled={isEdit}
            showSearch
            optionFilterProp="label"
            options={tools.map((t) => ({
              label: `${t.name} (${t.code})${existingToolCodes.includes(t.code) && !isEdit ? ' - 已添加' : ''}`,
              value: t.code,
              disabled: !isEdit && existingToolCodes.includes(t.code)
            }))}
          />
        </Form.Item>

        {/* 选中工具的描述与密钥提示 */}
        {selectedTool && (
          <Alert
            type="info"
            showIcon
            className="!mb-4"
            message={selectedTool.description || selectedTool.name}
            description={
              selectedTool.requiresSecret
                ? `该工具需要密钥${selectedTool.secretConfigured ? '（管理端已配置）' : '（管理端尚未配置密钥，测试可能失败）'}`
                : '该工具无需密钥'
            }
          />
        )}

        <Form.Item name="enabled" label="启用" valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="停用" />
        </Form.Item>

        <Form.Item
          name="config"
          label="工具配置 (JSON)"
          extra='可选，如 {"apiKey":"xxx","timeout":30}；密钥类配置请由管理端统一管理'
        >
          <Input.TextArea placeholder='{"timeout":30}' rows={4} />
        </Form.Item>
      </Form>
    </Modal>
  )
}
