import { useState } from 'react'
import { Modal, Input, message } from 'antd'

interface ProjectApiKeyInputModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 项目名称（用于弹窗展示） */
  projectName?: string
  /** 取消回调 */
  onCancel: () => void
  /** 确认回调，传入用户输入的 API Key 明文 */
  onConfirm: (apiKey: string) => void
}

/**
 * 项目 API Key 输入弹窗
 * - 进入项目时，若未配置 API Key 则弹窗引导用户输入明文
 * - 用户需在创建 Key 时手动保存明文，此处粘贴使用
 */
export default function ProjectApiKeyInputModal({
  open,
  projectName,
  onCancel,
  onConfirm
}: ProjectApiKeyInputModalProps) {
  const [key, setKey] = useState('')
  const [loading, setLoading] = useState(false)

  function handleOk() {
    const trimmed = key.trim()
    if (!trimmed) {
      message.warning('请输入项目 API Key')
      return
    }
    setLoading(true)
    try {
      onConfirm(trimmed)
      setKey('')
    } finally {
      setLoading(false)
    }
  }

  function handleCancel() {
    setKey('')
    onCancel()
  }

  return (
    <Modal
      title="输入项目 API Key"
      open={open}
      onOk={handleOk}
      onCancel={handleCancel}
      confirmLoading={loading}
      okText="进入项目"
      cancelText="取消"
    >
      <div className="mb-3 text-sm text-gray-500">
        进入项目「{projectName || '-'}」需要项目 API Key 用于业务接口鉴权。
        请粘贴在创建 API Key 时保存的明文密钥。
      </div>
      <Input.Password
        placeholder="请输入项目 API Key 明文"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        onPressEnter={handleOk}
      />
    </Modal>
  )
}
