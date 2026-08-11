import { Form, Switch, Input } from 'antd'
import type { WebSearchSettings } from '@/api/project-settings'

interface WebSearchSettingsFormProps {
  /** 表单实例（由父组件创建并传入） */
  form: ReturnType<typeof Form.useForm<WebSearchSettings & { allowedDomainsText: string; blockedDomainsText: string }>>[0]
  /** 初始值 */
  initial?: WebSearchSettings
}

/**
 * Web 搜索设置表单
 * - webSearchEnabled：是否启用
 * - allowedDomains：允许域名（多行文本输入，提交前转数组）
 * - blockedDomains：屏蔽域名（多行文本输入，提交前转数组）
 */
export default function WebSearchSettingsForm({ form, initial }: WebSearchSettingsFormProps) {
  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={{
        webSearchEnabled: initial?.webSearchEnabled ?? false,
        allowedDomainsText: (initial?.allowedDomains || []).join('\n'),
        blockedDomainsText: (initial?.blockedDomains || []).join('\n')
      }}
      preserve={false}
    >
      <Form.Item name="webSearchEnabled" label="启用 Web 搜索" valuePropName="checked">
        <Switch checkedChildren="启用" unCheckedChildren="关闭" />
      </Form.Item>
      <Form.Item
        name="allowedDomainsText"
        label="允许域名白名单"
        extra="每行一个域名，留空表示不限制"
      >
        <Input.TextArea rows={3} placeholder={'example.com\nsub.example.com'} />
      </Form.Item>
      <Form.Item
        name="blockedDomainsText"
        label="屏蔽域名黑名单"
        extra="每行一个域名"
      >
        <Input.TextArea rows={3} placeholder={'spam.example.com'} />
      </Form.Item>
    </Form>
  )
}
