import { Card, Empty, Spin, Tag } from 'antd'
import type { KnowledgeBase } from '@/api/knowledge-bases'

interface KnowledgeBaseMiniListProps {
  /** 知识库列表 */
  knowledgeBases: KnowledgeBase[] | undefined
  /** 加载中状态 */
  loading?: boolean
}

/**
 * 知识库迷你列表
 * - 展示知识库（name、documentCount、status）
 */
export default function KnowledgeBaseMiniList({ knowledgeBases, loading }: KnowledgeBaseMiniListProps) {
  return (
    <Card title="知识库" className="!rounded-lg !border-gray-200 !shadow-sm">
      <Spin spinning={loading}>
        {(!knowledgeBases || knowledgeBases.length === 0) ? (
          <Empty description="暂无知识库" />
        ) : (
          <ul className="divide-y divide-gray-100">
            {knowledgeBases.map((kb) => (
              <li
                key={kb.id}
                className="flex items-center justify-between py-3 px-2"
              >
                <div className="flex flex-col leading-tight">
                  <span className="text-sm font-medium text-gray-800">{kb.name}</span>
                  <span className="text-xs text-gray-400">code: {kb.code}</span>
                </div>
                <div className="flex items-center gap-3">
                  {/* 文档数：接口未返回时显示"—" */}
                  <span className="text-xs text-gray-500">
                    文档数：{kb.documentCount ?? '—'}
                  </span>
                  <Tag color={kb.status === 'active' ? 'green' : 'red'}>
                    {kb.status === 'active' ? '启用' : '停用'}
                  </Tag>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Spin>
    </Card>
  )
}
