import { useEffect } from 'react'
import { Modal, Form, Input, Select, InputNumber } from 'antd'
import type {
  CreateSchedulePayload,
  ScheduleTaskType,
  ConcurrencyPolicy,
  Schedule
} from '@/api/schedules'

interface ScheduleCreateModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 编辑模式下的初始数据 */
  initial?: Schedule | null
  /** 取消回调 */
  onCancel: () => void
  /** 提交回调（创建或更新统一入口） */
  onSubmit: (payload: CreateSchedulePayload) => Promise<void>
}

// 任务类型选项（与后端枚举对齐）
const TASK_TYPE_OPTIONS: { label: string; value: ScheduleTaskType }[] = [
  { label: '知识库同步', value: 'KNOWLEDGE_SYNC' },
  { label: '采集源运行', value: 'CRAWL_SOURCE' },
  { label: '智能研究', value: 'RESEARCH' },
  { label: '向量刷新', value: 'EMBEDDING_REFRESH' },
  { label: '网络资料审核', value: 'WEB_MATERIAL_REVIEW' }
]

// 并发策略选项
const CONCURRENCY_OPTIONS: { label: string; value: ConcurrencyPolicy }[] = [
  { label: '允许并发', value: 'ALLOW' },
  { label: '禁止并发', value: 'FORBID' },
  { label: '替换上次', value: 'REPLACE' }
]

// 常用时区预设
const TIMEZONE_OPTIONS = [
  { label: 'Asia/Shanghai (UTC+8)', value: 'Asia/Shanghai' },
  { label: 'Asia/Hong_Kong (UTC+8)', value: 'Asia/Hong_Kong' },
  { label: 'Asia/Tokyo (UTC+9)', value: 'Asia/Tokyo' },
  { label: 'Asia/Singapore (UTC+8)', value: 'Asia/Singapore' },
  { label: 'UTC', value: 'UTC' },
  { label: 'America/New_York (UTC-5)', value: 'America/New_York' },
  { label: 'America/Los_Angeles (UTC-8)', value: 'America/Los_Angeles' },
  { label: 'Europe/London (UTC+0)', value: 'Europe/London' }
]

/**
 * 创建/编辑定时任务弹窗
 * - 字段：name、taskType、cronExpression、timezone、config(JSON)、concurrencyPolicy、timeoutSeconds、maxRetries
 * - 编辑模式下回填初始数据
 */
export default function ScheduleCreateModal({ open, initial, onCancel, onSubmit }: ScheduleCreateModalProps) {
  const [form] = Form.useForm<CreateSchedulePayload>()
  const isEdit = !!initial

  // 弹窗打开时回填表单
  useEffect(() => {
    if (open) {
      if (initial) {
        // 编辑模式：回填已有字段
        form.setFieldsValue({
          name: initial.name,
          taskType: initial.taskType,
          cronExpression: initial.cronExpression,
          timezone: initial.timezone,
          config: initial.config,
          concurrencyPolicy: initial.concurrencyPolicy,
          timeoutSeconds: initial.timeoutSeconds,
          maxRetries: initial.maxRetries
        })
      } else {
        // 新建模式：重置并填默认值
        form.resetFields()
        form.setFieldsValue({
          timezone: 'Asia/Shanghai',
          concurrencyPolicy: 'FORBID',
          maxRetries: 3
        })
      }
    }
  }, [open, initial, form])

  async function handleOk() {
    const values = await form.validateFields()
    await onSubmit(values)
    form.resetFields()
  }

  return (
    <Modal
      title={isEdit ? '编辑定时任务' : '创建定时任务'}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      okText={isEdit ? '保存' : '创建'}
      cancelText="取消"
      destroyOnClose
      width={560}
    >
      <Form form={form} layout="vertical" className="mt-2">
        <Form.Item
          name="name"
          label="任务名称"
          rules={[{ required: true, message: '请输入任务名称' }]}
        >
          <Input placeholder="如 每日基金资讯采集" />
        </Form.Item>
        <Form.Item
          name="taskType"
          label="任务类型"
          rules={[{ required: true, message: '请选择任务类型' }]}
        >
          <Select placeholder="选择任务类型" options={TASK_TYPE_OPTIONS} />
        </Form.Item>
        <Form.Item
          name="cronExpression"
          label="Cron 表达式"
          rules={[
            { required: true, message: '请输入 Cron 表达式' },
            { pattern: /^[\d*/,\-\s]+$/, message: 'Cron 表达式格式不正确' }
          ]}
          extra="示例：0 0 9 * * ?（每天 9 点）、0 */30 * * * ?（每 30 分钟）"
        >
          <Input placeholder="如 0 0 9 * * ?" />
        </Form.Item>
        <Form.Item
          name="timezone"
          label="时区"
          rules={[{ required: true, message: '请选择时区' }]}
        >
          <Select placeholder="选择时区" options={TIMEZONE_OPTIONS} showSearch optionFilterProp="label" />
        </Form.Item>
        <Form.Item
          name="config"
          label="任务配置 (JSON)"
          extra="可选，按任务类型传递参数，如 {&quot;knowledgeBaseId&quot;:&quot;xxx&quot;}"
        >
          <Input.TextArea placeholder='{"key":"value"}' rows={3} />
        </Form.Item>
        <Form.Item
          name="concurrencyPolicy"
          label="并发策略"
          rules={[{ required: true, message: '请选择并发策略' }]}
        >
          <Select options={CONCURRENCY_OPTIONS} />
        </Form.Item>
        <div className="flex gap-4">
          <Form.Item name="timeoutSeconds" label="超时(秒)" className="flex-1">
            <InputNumber placeholder="如 3600" min={1} className="!w-full" />
          </Form.Item>
          <Form.Item name="maxRetries" label="最大重试" className="flex-1">
            <InputNumber placeholder="如 3" min={0} className="!w-full" />
          </Form.Item>
        </div>
      </Form>
    </Modal>
  )
}
