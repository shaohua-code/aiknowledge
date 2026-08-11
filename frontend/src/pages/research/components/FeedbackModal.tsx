import { useEffect } from 'react'
import { Modal, Form, Input, Switch, Rate, message } from 'antd'
import { useResearchFeedback } from '../hooks/useResearchFeedback'

interface FeedbackModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 研究请求 ID */
  requestId: string
  /** 取消回调 */
  onCancel: () => void
  /** 提交成功回调 */
  onSuccess?: () => void
}

interface FeedbackFormValues {
  /** 评分 1-5 */
  rating: number
  /** 是否采纳 */
  accepted: boolean
  /** 文本评论 */
  comment?: string
}

/**
 * 研究反馈弹窗
 * - 评分（Rate 1-5）
 * - 是否采纳（Switch）
 * - 评论（TextArea）
 * - 提交成功后关闭弹窗
 */
export default function FeedbackModal({ open, requestId, onCancel, onSuccess }: FeedbackModalProps) {
  const [form] = Form.useForm<FeedbackFormValues>()
  const feedbackMutation = useResearchFeedback()

  // 弹窗打开时重置表单
  useEffect(() => {
    if (open) {
      form.resetFields()
      // 默认 4 星 + 采纳
      form.setFieldsValue({ rating: 4, accepted: true, comment: '' })
    }
  }, [open, form])

  async function handleOk() {
    const values = await form.validateFields()
    try {
      await feedbackMutation.mutateAsync({
        requestId,
        payload: {
          rating: values.rating,
          accepted: values.accepted,
          comment: values.comment || undefined
        }
      })
      onSuccess?.()
      onCancel()
    } catch {
      // 错误信息已由拦截器/全局提示展示
      message.error('反馈提交失败，请稍后重试')
    }
  }

  return (
    <Modal
      title="研究反馈"
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      okText="提交"
      cancelText="取消"
      confirmLoading={feedbackMutation.isPending}
      destroyOnClose
    >
      <Form form={form} layout="vertical" className="mt-2">
        <Form.Item
          name="rating"
          label="评分"
          rules={[{ required: true, message: '请选择评分' }]}
        >
          <Rate />
        </Form.Item>
        <Form.Item
          name="accepted"
          label="是否采纳"
          valuePropName="checked"
        >
          <Switch checkedChildren="采纳" unCheckedChildren="未采纳" />
        </Form.Item>
        <Form.Item name="comment" label="评论">
          <Input.TextArea placeholder="选填，补充您对本次研究结果的看法" rows={3} />
        </Form.Item>
      </Form>
    </Modal>
  )
}
