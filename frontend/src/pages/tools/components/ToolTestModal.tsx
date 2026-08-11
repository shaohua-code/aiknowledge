import { useState } from 'react'
import { Modal, Input, Button, Alert, Spin, Tag, Typography } from 'antd'
import type { ToolTestResult } from '@/api/tools'

const { Paragraph, Text } = Typography

interface ToolTestModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 工具编码 */
  toolCode: string
  /** 工具名称（展示用） */
  toolName?: string
  /** 入参 schema（JSON 字符串，用于提示） */
  inputSchema?: string
  /** 取消回调 */
  onCancel: () => void
  /** 测试执行回调 */
  onTest: (payload: { inputs: Record<string, unknown> }) => Promise<ToolTestResult>
}

/**
 * 工具测试弹窗
 * - 输入 JSON 参数
 * - 调用测试接口，展示成功/失败、输出、耗时
 */
export default function ToolTestModal({
  open,
  toolCode,
  toolName,
  inputSchema,
  onCancel,
  onTest
}: ToolTestModalProps) {
  // 参数 JSON 文本（默认空对象）
  const [inputsText, setInputsText] = useState('{}')
  // 测试结果
  const [result, setResult] = useState<ToolTestResult | null>(null)
  // 加载态
  const [loading, setLoading] = useState(false)
  // 参数解析错误
  const [parseError, setParseError] = useState('')

  /** 执行测试 */
  async function handleRun() {
    setParseError('')
    let inputs: Record<string, unknown>
    try {
      // 解析参数 JSON
      inputs = JSON.parse(inputsText || '{}')
    } catch {
      setParseError('参数必须为合法 JSON')
      return
    }
    setLoading(true)
    setResult(null)
    try {
      const data = await onTest({ inputs })
      setResult(data)
    } catch {
      // 错误已由拦截器提示
    } finally {
      setLoading(false)
    }
  }

  /** 关闭弹窗时重置状态 */
  function handleCancel() {
    setInputsText('{}')
    setResult(null)
    setLoading(false)
    setParseError('')
    onCancel()
  }

  return (
    <Modal
      title={`测试工具：${toolName || toolCode}`}
      open={open}
      onCancel={handleCancel}
      width={620}
      footer={[
        <Button key="cancel" onClick={handleCancel}>关闭</Button>,
        <Button key="run" type="primary" loading={loading} onClick={handleRun}>执行测试</Button>
      ]}
      destroyOnClose
    >
      <div className="mt-2 flex flex-col gap-3">
        {/* 入参 schema 提示 */}
        {inputSchema && (
          <Alert
            type="info"
            showIcon
            message="入参 Schema"
            description={<Text code className="!break-all !text-xs">{inputSchema}</Text>}
          />
        )}

        <div>
          <div className="mb-1 text-sm text-gray-600">测试参数 (JSON)</div>
          <Input.TextArea
            value={inputsText}
            onChange={(e) => setInputsText(e.target.value)}
            rows={5}
            placeholder='{"query":"示例查询","limit":5}'
          />
          {parseError && <div className="mt-1 text-xs text-red-500">{parseError}</div>}
        </div>

        {/* 加载态 */}
        {loading && (
          <div className="flex items-center justify-center py-4">
            <Spin tip="执行中..." />
          </div>
        )}

        {/* 测试结果 */}
        {result && (
          <div className="rounded border border-gray-200 bg-gray-50 p-3">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-sm text-gray-600">结果：</span>
              <Tag color={result.success ? 'green' : 'red'}>
                {result.success ? '成功' : '失败'}
              </Tag>
              {typeof result.durationMs === 'number' && (
                <Tag color="blue">{result.durationMs} ms</Tag>
              )}
            </div>
            {result.error && (
              <Alert type="error" showIcon className="!mb-2" message="错误信息" description={result.error} />
            )}
            {result.output && (
              <div>
                <div className="mb-1 text-xs text-gray-500">输出</div>
                <Paragraph className="!mb-0 !whitespace-pre-wrap !rounded !bg-white !p-2 !text-xs">
                  {result.output}
                </Paragraph>
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  )
}
