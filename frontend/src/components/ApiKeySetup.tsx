import { useState } from 'react'
import { Modal, Input, Button, message } from 'antd'
import { getManagementKey, setManagementKey } from '@/api/request'

/**
 * 管理密钥设置组件
 * - 首次访问时若 localStorage 无 management_api_key，引导用户输入
 * - 提供"重新设置"入口，便于更换密钥
 */
interface ApiKeySetupProps {
  /** 触发按钮文案，默认"设置管理密钥" */
  triggerText?: string
  /** 是否以按钮形式展示（false 时仅作为受控弹窗） */
  asButton?: boolean
  /** 子元素触发（自定义触发器） */
  children?: React.ReactNode
  /** 设置成功后回调 */
  onSuccess?: () => void
}

export default function ApiKeySetup({
  triggerText = '设置管理密钥',
  asButton = true,
  children,
  onSuccess
}: ApiKeySetupProps) {
  const [open, setOpen] = useState(false)
  const [key, setKey] = useState('')
  const [confirmLoading, setConfirmLoading] = useState(false)

  function handleOpen() {
    // 打开时回填已存在的管理密钥，便于查看/修改
    setKey(getManagementKey() || '')
    setOpen(true)
  }

  function handleOk() {
    const trimmed = key.trim()
    if (!trimmed) {
      message.warning('请输入管理密钥')
      return
    }
    setConfirmLoading(true)
    try {
      // 写入 localStorage，axios 拦截器会自动读取
      setManagementKey(trimmed)
      message.success('管理密钥已保存')
      setOpen(false)
      onSuccess?.()
    } finally {
      setConfirmLoading(false)
    }
  }

  return (
    <>
      {asButton ? (
        <Button onClick={handleOpen}>{triggerText}</Button>
      ) : (
        <span onClick={handleOpen}>{children}</span>
      )}
      <Modal
        title="设置管理密钥"
        open={open}
        onOk={handleOk}
        onCancel={() => setOpen(false)}
        confirmLoading={confirmLoading}
        okText="保存"
        cancelText="取消"
      >
        <div className="mb-2 text-sm text-gray-500">
          管理密钥用于访问项目管理接口（创建/停用项目、管理 API Key）。密钥将保存在浏览器本地，请妥善保管。
        </div>
        <Input.Password
          placeholder="请输入管理密钥（Management API Key）"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          onPressEnter={handleOk}
        />
      </Modal>
    </>
  )
}
