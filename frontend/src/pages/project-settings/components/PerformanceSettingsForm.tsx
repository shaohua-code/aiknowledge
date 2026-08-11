import { Form, InputNumber } from 'antd'
import type { PerformanceSettings } from '@/api/project-settings'

interface PerformanceSettingsFormProps {
  /** 表单实例（由父组件创建并传入） */
  form: ReturnType<typeof Form.useForm<PerformanceSettings>>[0]
  /** 初始值 */
  initial?: PerformanceSettings
}

/**
 * 性能设置表单
 * - maxEvidence：最大证据条数
 * - maxTokens：最大 token 数
 * - timeoutSeconds：超时秒数
 */
export default function PerformanceSettingsForm({ form, initial }: PerformanceSettingsFormProps) {
  return (
    <Form form={form} layout="vertical" initialValues={initial} preserve={false}>
      <div className="flex gap-4">
        <Form.Item name="maxEvidence" label="最大证据条数" className="flex-1">
          <InputNumber min={1} max={50} style={{ width: '100%' }} placeholder="如 10" />
        </Form.Item>
        <Form.Item name="maxTokens" label="最大 Token 数" className="flex-1">
          <InputNumber min={256} max={32768} style={{ width: '100%' }} placeholder="如 4096" />
        </Form.Item>
        <Form.Item name="timeoutSeconds" label="超时秒数" className="flex-1">
          <InputNumber min={5} max={600} style={{ width: '100%' }} placeholder="如 60" />
        </Form.Item>
      </div>
    </Form>
  )
}
