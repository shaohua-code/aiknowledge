import { useEffect } from 'react'
import { Modal, Form, Input, Select } from 'antd'
import type {
  CreateCrawlSourcePayload,
  CrawlSourceType,
  CrawlImportPolicy,
  CrawlSource
} from '@/api/crawl-sources'
import type { KnowledgeBase } from '@/api/knowledge-bases'

interface CrawlSourceCreateModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 编辑模式下的初始数据 */
  initial?: CrawlSource | null
  /** 可选的目标知识库列表 */
  knowledgeBases?: KnowledgeBase[]
  /** 取消回调 */
  onCancel: () => void
  /** 提交回调（创建/更新统一入口） */
  onSubmit: (payload: CreateCrawlSourcePayload) => Promise<void>
}

// 采集源类型选项
const TYPE_OPTIONS: { label: string; value: CrawlSourceType }[] = [
  { label: '网页', value: 'WEB' },
  { label: '站点地图', value: 'SITEMAP' },
  { label: 'RSS', value: 'RSS' },
  { label: 'API', value: 'API' }
]

// 导入策略选项
const IMPORT_POLICY_OPTIONS: { label: string; value: CrawlImportPolicy }[] = [
  { label: '自动入库', value: 'AUTO' },
  { label: '人工审核', value: 'REVIEW' },
  { label: '草稿暂存', value: 'DRAFT' }
]

/**
 * 创建/编辑采集源弹窗
 * - 字段：code、name、type、startUrls、allowedDomains、extractRules(JSON)、importPolicy、limits(JSON)、destinationKnowledgeBaseId
 * - startUrls/allowedDomains 以换行分隔的多行文本输入，提交前转为数组
 */
export default function CrawlSourceCreateModal({
  open,
  initial,
  knowledgeBases = [],
  onCancel,
  onSubmit
}: CrawlSourceCreateModalProps) {
  const [form] = Form.useForm<CreateCrawlSourcePayload & { startUrlsText: string; allowedDomainsText: string }>()
  const isEdit = !!initial

  // 弹窗打开时回填表单
  useEffect(() => {
    if (open) {
      if (initial) {
        // 编辑模式：数组字段转成多行文本回填
        form.setFieldsValue({
          code: initial.code,
          name: initial.name,
          type: initial.type,
          startUrlsText: (initial.startUrls || []).join('\n'),
          allowedDomainsText: (initial.allowedDomains || []).join('\n'),
          extractRules: initial.extractRules,
          importPolicy: initial.importPolicy,
          limits: initial.limits,
          destinationKnowledgeBaseId: initial.destinationKnowledgeBaseId
        })
      } else {
        // 新建模式：重置并填默认值
        form.resetFields()
        form.setFieldsValue({ type: 'WEB', importPolicy: 'REVIEW' })
      }
    }
  }, [open, initial, form])

  async function handleOk() {
    const raw = await form.validateFields()
    // 多行文本拆分为数组，过滤空行与首尾空白
    const startUrls = (raw.startUrlsText || '')
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
    const allowedDomains = (raw.allowedDomainsText || '')
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
    const payload: CreateCrawlSourcePayload = {
      code: raw.code,
      name: raw.name,
      type: raw.type,
      startUrls,
      allowedDomains,
      extractRules: raw.extractRules,
      importPolicy: raw.importPolicy,
      limits: raw.limits,
      destinationKnowledgeBaseId: raw.destinationKnowledgeBaseId
    }
    await onSubmit(payload)
    form.resetFields()
  }

  return (
    <Modal
      title={isEdit ? '编辑采集源' : '创建采集源'}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      okText={isEdit ? '保存' : '创建'}
      cancelText="取消"
      destroyOnClose
      width={620}
    >
      <Form form={form} layout="vertical" className="mt-2">
        <div className="flex gap-4">
          <Form.Item
            name="code"
            label="采集源编码"
            rules={[
              { required: true, message: '请输入采集源编码' },
              { pattern: /^[a-z0-9-]+$/, message: '仅支持小写字母、数字、短横线' }
            ]}
            className="flex-1"
          >
            <Input placeholder="如 fund-news" disabled={isEdit} />
          </Form.Item>
          <Form.Item
            name="name"
            label="采集源名称"
            rules={[{ required: true, message: '请输入采集源名称' }]}
            className="flex-1"
          >
            <Input placeholder="如 基金资讯采集" />
          </Form.Item>
        </div>
        <div className="flex gap-4">
          <Form.Item
            name="type"
            label="采集类型"
            rules={[{ required: true, message: '请选择采集类型' }]}
            className="flex-1"
          >
            <Select options={TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item
            name="importPolicy"
            label="导入策略"
            rules={[{ required: true, message: '请选择导入策略' }]}
            className="flex-1"
          >
            <Select options={IMPORT_POLICY_OPTIONS} />
          </Form.Item>
        </div>
        <Form.Item
          name="startUrlsText"
          label="起始 URL"
          rules={[{ required: true, message: '请输入至少一个起始 URL' }]}
          extra="每行一个 URL"
        >
          <Input.TextArea placeholder={'https://example.com/page1\nhttps://example.com/page2'} rows={3} />
        </Form.Item>
        <Form.Item
          name="allowedDomainsText"
          label="允许抓取域名"
          rules={[{ required: true, message: '请输入至少一个允许的域名' }]}
          extra="每行一个域名，如 example.com"
        >
          <Input.TextArea placeholder={'example.com\nsub.example.com'} rows={2} />
        </Form.Item>
        <Form.Item
          name="extractRules"
          label="抽取规则 (JSON)"
          extra='可选，如 {"title":"h1","content":"article"}'
        >
          <Input.TextArea placeholder='{"title":"h1","content":"article"}' rows={3} />
        </Form.Item>
        <Form.Item
          name="limits"
          label="限制项 (JSON)"
          extra='可选，如 {"maxDepth":2,"maxPages":100}'
        >
          <Input.TextArea placeholder='{"maxDepth":2,"maxPages":100}' rows={2} />
        </Form.Item>
        <Form.Item name="destinationKnowledgeBaseId" label="目标知识库">
          <Select
            placeholder="可选，选择入库目标知识库"
            allowClear
            options={knowledgeBases.map((kb) => ({ label: `${kb.name} (${kb.code})`, value: kb.id }))}
            optionFilterProp="label"
            showSearch
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}
