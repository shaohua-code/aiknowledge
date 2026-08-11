import { useEffect } from 'react'
import { Modal, Input, Alert, Typography, Space, message } from 'antd'

const { Paragraph, Text } = Typography

interface ApiKeyRevealModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 标题场景：create / rotate */
  scene?: 'create' | 'rotate'
  /** 明文密钥（仅展示一次） */
  plaintextKey?: string
  /** 取消回调 */
  onCancel: () => void
}

/**
 * API Key 明文展示弹窗
 * - 强调"仅展示一次，请妥善保存"
 * - 提供一键复制
 */
export default function ApiKeyRevealModal({
  open,
  scene = 'create',
  plaintextKey,
  onCancel
}: ApiKeyRevealModalProps) {
  // 打开时提示用户保存
  useEffect(() => {
    if (open && plaintextKey) {
      message.warning('明文密钥仅展示一次，请妥善保存')
    }
  }, [open, plaintextKey])

  /** 复制到剪贴板 */
  async function handleCopy() {
    if (!plaintextKey) return
    try {
      await navigator.clipboard.writeText(plaintextKey)
      message.success('已复制到剪贴板')
    } catch {
      message.error('复制失败，请手动选择复制')
    }
  }

  return (
    <Modal
      title={scene === 'rotate' ? '轮换后的 API Key' : '创建成功'}
      open={open}
      onCancel={onCancel}
      footer={null}
      destroyOnClose
      width={560}
      maskClosable={false}
      keyboard={false}
    >
      <div className="mt-2 flex flex-col gap-3">
        <Alert
          type="warning"
          showIcon
          message="明文密钥仅展示一次，请立即复制保存！"
          description="关闭后将无法再次查看，如遗失请轮换或重建密钥。"
        />
        <div>
          <div className="mb-1 text-sm text-gray-600">API Key</div>
          <Input.Password
            value={plaintextKey || ''}
            readOnly
            visibilityToggle
          />
        </div>
        <Space>
          <a onClick={handleCopy} className="text-sm">复制密钥</a>
        </Space>
        <Paragraph className="!mb-0 !text-xs !text-gray-400">
          <Text type="secondary">提示：调用业务接口时将该 Key 作为 Bearer Token 放入 Authorization 头。</Text>
        </Paragraph>
      </div>
    </Modal>
  )
}
