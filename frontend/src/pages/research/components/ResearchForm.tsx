import { Form, Input, Select, Button, Card, Space } from 'antd'
import type {
  ResearchOutputType,
  ResearchStrategy
} from '@/api/research'
import type { KnowledgeBase } from '@/api/knowledge-bases'

// 研究表单数据结构
export interface ResearchFormValues {
  question: string
  outputType: ResearchOutputType
  strategy: ResearchStrategy
  knowledgeBaseIds: string[]
  toolCodes: string[]
  context: string
}

interface ResearchFormProps {
  /** 表单值（受控） */
  value: ResearchFormValues
  /** 表单值更新回调 */
  onChange: (patch: Partial<ResearchFormValues>) => void
  /** 知识库列表（从父级加载） */
  knowledgeBases: KnowledgeBase[]
  /** 提交回调 */
  onSubmit: () => void
  /** 加载中 */
  loading: boolean
  /** 重置回调 */
  onReset: () => void
}

// 输出类型选项
const OUTPUT_TYPE_OPTIONS = [
  { label: '叙述', value: 'narrative' },
  { label: 'JSON', value: 'json' },
  { label: '列表', value: 'bullet_points' }
]

// 研究策略选项
const STRATEGY_OPTIONS = [
  { label: '仅知识库', value: 'knowledge_only' },
  { label: '知识库 + Web', value: 'knowledge_web' },
  { label: '知识库 + 工具', value: 'knowledge_tools' },
  { label: '全量（知识库 + Web + 工具）', value: 'full' }
]

// 工具选项（占位，后续接入工具列表接口后替换）
const TOOL_OPTIONS = [
  { label: 'web_search', value: 'web_search' },
  { label: 'rag_tool', value: 'rag_tool' }
]

/**
 * 研究表单
 * - 问题、输出类型、策略、知识库多选、工具多选、上下文 JSON
 * - 受控组件，值与状态由父级管理
 */
export default function ResearchForm({
  value,
  onChange,
  knowledgeBases,
  onSubmit,
  loading,
  onReset
}: ResearchFormProps) {
  return (
    <Card
      title="研究表单"
      size="small"
      className="!h-full"
      styles={{ body: { padding: 16 } }}
    >
      <Form layout="vertical" className="!h-full">
        <Form.Item label="研究问题" required>
          <Input.TextArea
            placeholder="请输入要研究的问题，例如：近一年新能源汽车出口情况？"
            value={value.question}
            onChange={(e) => onChange({ question: e.target.value })}
            rows={4}
          />
        </Form.Item>

        <div className="grid grid-cols-2 gap-3">
          <Form.Item label="输出类型">
            <Select
              value={value.outputType}
              onChange={(v) => onChange({ outputType: v })}
              options={OUTPUT_TYPE_OPTIONS}
            />
          </Form.Item>
          <Form.Item label="研究策略">
            <Select
              value={value.strategy}
              onChange={(v) => onChange({ strategy: v })}
              options={STRATEGY_OPTIONS}
            />
          </Form.Item>
        </div>

        <Form.Item label="知识库（多选）">
          <Select
            mode="multiple"
            placeholder="选择参与检索的知识库"
            value={value.knowledgeBaseIds}
            onChange={(v) => onChange({ knowledgeBaseIds: v })}
            options={knowledgeBases.map((kb) => ({ label: kb.name, value: kb.id }))}
            optionFilterProp="label"
          />
        </Form.Item>

        <Form.Item label="工具（多选，可选）">
          <Select
            mode="multiple"
            placeholder="选填，可调用的工具编码"
            value={value.toolCodes}
            onChange={(v) => onChange({ toolCodes: v })}
            options={TOOL_OPTIONS}
            allowClear
          />
        </Form.Item>

        <Form.Item label="上下文（JSON，可选）">
          <Input.TextArea
            placeholder='选填，如 {"focus":"中国市场"}'
            value={value.context}
            onChange={(e) => onChange({ context: e.target.value })}
            rows={2}
          />
        </Form.Item>

        <Form.Item className="!mb-0">
          <Space>
            <Button type="primary" loading={loading} onClick={onSubmit}>开始研究</Button>
            <Button onClick={onReset}>重置</Button>
          </Space>
        </Form.Item>
      </Form>
    </Card>
  )
}
