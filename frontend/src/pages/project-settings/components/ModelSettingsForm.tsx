import { Form, Input } from 'antd'
import type { ModelSettings } from '@/api/project-settings'

interface ModelSettingsFormProps {
  /** 表单实例（由父组件创建并传入，便于统一收集） */
  form: ReturnType<typeof Form.useForm<ModelSettings>>[0]
  /** 初始值 */
  initial?: ModelSettings
}

/**
 * 模型设置表单
 * - chatModel：对话模型标识
 * - embeddingModel：嵌入模型标识
 */
export default function ModelSettingsForm({ form, initial }: ModelSettingsFormProps) {
  return (
    <Form form={form} layout="vertical" initialValues={initial} preserve={false}>
      <div className="flex gap-4">
        <Form.Item name="chatModel" label="对话模型" className="flex-1">
          <Input placeholder="如 gpt-4o / glm-4" />
        </Form.Item>
        <Form.Item name="embeddingModel" label="嵌入模型" className="flex-1">
          <Input placeholder="如 bge-m3 / text-embedding-3-large" />
        </Form.Item>
      </div>
    </Form>
  )
}
