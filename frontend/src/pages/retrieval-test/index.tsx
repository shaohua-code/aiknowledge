import { useState } from 'react'
import { Card, Form, Input, Select, InputNumber, Button, Space, message, Empty, Spin, Typography, Alert } from 'antd'
import { useCurrentProject } from '@/stores/project'
import { useKnowledgeBases } from '@/pages/knowledge-bases/hooks/useKnowledgeBases'
import { useRetrievalSearch } from './hooks/useRetrievalSearch'
import RetrievalHitCard from './components/RetrievalHitCard'

const { Title, Text } = Typography

// 检索表单数据结构
interface RetrievalFormValues {
  query: string
  knowledgeBaseIds: string[]
  topK: number
}

// 默认表单值
const DEFAULT_FORM: RetrievalFormValues = {
  query: '',
  knowledgeBaseIds: [],
  topK: 5
}

/**
 * 检索测试页
 * - 左侧表单：query、knowledgeBaseIds 多选、topK 数字输入
 * - 右侧结果：命中片段卡片列表、总命中数、耗时
 * - 查询条件集中在 useState 对象
 */
export default function RetrievalTestPage() {
  const currentProject = useCurrentProject()
  const { data: knowledgeBases = [] } = useKnowledgeBases({ status: 'active' })
  const searchMutation = useRetrievalSearch()

  // 查询条件集中管理
  const [formData, setFormData] = useState<RetrievalFormValues>(DEFAULT_FORM)

  /** 提交检索 */
  async function handleSearch() {
    if (!formData.query.trim()) {
      message.warning('请输入检索查询')
      return
    }
    if (formData.knowledgeBaseIds.length === 0) {
      message.warning('请至少选择一个知识库')
      return
    }
    try {
      await searchMutation.mutateAsync({
        query: formData.query.trim(),
        knowledgeBaseIds: formData.knowledgeBaseIds,
        topK: formData.topK
      })
    } catch {
      // 错误已由拦截器提示
    }
  }

  /** 重置 */
  function handleReset() {
    setFormData(DEFAULT_FORM)
  }

  const result = searchMutation.data
  const loading = searchMutation.isPending

  return (
    <div className="flex h-full w-full flex-col">
      {/* 顶部标题 */}
      <div className="mb-4 flex flex-col leading-tight">
        <span className="text-xl font-semibold text-gray-800">检索测试</span>
        {currentProject && (
          <span className="text-sm text-gray-400">
            当前项目：{currentProject.name}（{currentProject.code}）
          </span>
        )}
      </div>

      {/* 主体：左右两栏 */}
      <div className="grid flex-1 grid-cols-1 gap-4 overflow-hidden lg:grid-cols-12">
        {/* 左侧表单 */}
        <div className="lg:col-span-4 xl:col-span-3">
          <Card title="检索条件" size="small" className="!h-full">
            <Form layout="vertical">
              <Form.Item label="查询语句" required>
                <Input.TextArea
                  placeholder="输入检索查询，例如：新能源汽车出口政策"
                  value={formData.query}
                  onChange={(e) => setFormData((f) => ({ ...f, query: e.target.value }))}
                  rows={4}
                />
              </Form.Item>
              <Form.Item label="知识库（多选）" required>
                <Select
                  mode="multiple"
                  placeholder="选择知识库"
                  value={formData.knowledgeBaseIds}
                  onChange={(v) => setFormData((f) => ({ ...f, knowledgeBaseIds: v }))}
                  options={knowledgeBases.map((kb) => ({ label: kb.name, value: kb.id }))}
                  optionFilterProp="label"
                />
              </Form.Item>
              <Form.Item label="Top-K">
                <InputNumber
                  min={1}
                  max={50}
                  value={formData.topK}
                  onChange={(v) => setFormData((f) => ({ ...f, topK: v ?? 5 }))}
                  className="!w-full"
                />
              </Form.Item>
              <Form.Item className="!mb-0">
                <Space>
                  <Button type="primary" loading={loading} onClick={handleSearch}>
                    检索
                  </Button>
                  <Button onClick={handleReset}>重置</Button>
                </Space>
              </Form.Item>
            </Form>
          </Card>
        </div>

        {/* 右侧结果 */}
        <div className="rounded border border-gray-200 bg-white p-4 lg:col-span-8 xl:col-span-9">
          {/* 加载中 */}
          {loading ? (
            <div className="flex h-full items-center justify-center">
              <Spin tip="检索中..." size="large" />
            </div>
          ) : !result ? (
            <div className="flex h-full items-center justify-center">
              <Empty description="暂无检索结果，请在左侧填写查询条件后执行检索" />
            </div>
          ) : (
            <div className="flex h-full flex-col overflow-auto">
              {/* 概要信息 */}
              <div className="mb-3 flex items-center justify-between">
                <Title level={5} className="!mb-0">命中片段</Title>
                <Space>
                  <Text type="secondary">总命中数：{result.totalHits}</Text>
                  <Text type="secondary">耗时：{result.elapsedMs} ms</Text>
                </Space>
              </div>

              {/* 空结果提示 */}
              {result.hits.length === 0 ? (
                <Alert type="info" message="未命中任何片段，可尝试调整查询或扩大 topK" showIcon />
              ) : (
                <div className="grid grid-cols-1 gap-3">
                  {result.hits.map((h, i) => (
                    <RetrievalHitCard key={h.chunkId || i} hit={h} index={i} />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
