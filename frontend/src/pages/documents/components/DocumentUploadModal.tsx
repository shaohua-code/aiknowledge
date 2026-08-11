import { useEffect, useState } from 'react'
import { Modal, Tabs, Form, Input, Upload, Button, Select, message } from 'antd'
import type { UploadFile } from 'antd/es/upload/interface'
import type { CreateDocumentPayload, UploadDocumentFilePayload } from '@/api/documents'

const { TextArea } = Input

interface DocumentUploadModalProps {
  /** 弹窗是否可见 */
  open: boolean
  /** 目标知识库编码 */
  knowledgeBaseCode: string
  /** 取消回调 */
  onCancel: () => void
  /** 文件上传提交回调 */
  onUploadFile: (payload: UploadDocumentFilePayload) => Promise<void>
  /** 文本/URL 创建提交回调 */
  onCreateDocument: (payload: CreateDocumentPayload) => Promise<void>
}

// 上传方式 Tab
type UploadTab = 'file' | 'text' | 'url'

// 文件大小上限：20MB
const MAX_FILE_SIZE = 20 * 1024 * 1024
// 允许的文件扩展名
const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.txt', '.md']

/**
 * 文档导入弹窗
 * - Tab 切换：文件上传 / 文本输入 / URL 输入
 * - 文件：Upload + title + tags
 * - 文本：title + content + tags
 * - URL：title + url + tags
 */
export default function DocumentUploadModal({
  open,
  knowledgeBaseCode,
  onCancel,
  onUploadFile,
  onCreateDocument
}: DocumentUploadModalProps) {
  const [activeTab, setActiveTab] = useState<UploadTab>('file')

  // 三套表单（各自独立，互不污染）
  const [fileForm] = Form.useForm<{ title?: string; tags?: string[] }>()
  const [textForm] = Form.useForm<{ title: string; content: string; tags?: string[] }>()
  const [urlForm] = Form.useForm<{ title: string; url: string; tags?: string[] }>()

  // 文件列表（受控）
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [submitting, setSubmitting] = useState(false)

  // 打开弹窗时重置
  useEffect(() => {
    if (open) {
      setActiveTab('file')
      fileForm.resetFields()
      textForm.resetFields()
      urlForm.resetFields()
      setFileList([])
    }
  }, [open, fileForm, textForm, urlForm])

  /** 文件上传前校验：大小、扩展名 */
  function beforeUpload(file: File): boolean {
    const ext = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] || ''
    if (!ACCEPTED_EXTENSIONS.includes(ext)) {
      message.error(`仅支持 ${ACCEPTED_EXTENSIONS.join(' / ')} 文件`)
      return false
    }
    if (file.size > MAX_FILE_SIZE) {
      message.error('文件大小不可超过 20MB')
      return false
    }
    return true
  }

  /** 提交文件上传 */
  async function handleFileSubmit() {
    if (!knowledgeBaseCode) {
      message.warning('请先选择知识库')
      return
    }
    if (fileList.length === 0) {
      message.warning('请选择要上传的文件')
      return
    }
    const file = fileList[0].originFileObj as File
    if (!file) {
      message.warning('文件无效')
      return
    }
    let values: { title?: string; tags?: string[] }
    try {
      values = await fileForm.validateFields()
    } catch {
      // 表单校验失败时由 Form 展示字段错误，不能继续提交空载荷。
      return
    }
    setSubmitting(true)
    try {
      await onUploadFile({
        file,
        title: values.title,
        tags: values.tags
      })
      onCancel()
    } finally {
      setSubmitting(false)
    }
  }

  /** 提交文本创建 */
  async function handleTextSubmit() {
    if (!knowledgeBaseCode) {
      message.warning('请先选择知识库')
      return
    }
    const values = await textForm.validateFields()
    setSubmitting(true)
    try {
      await onCreateDocument({
        type: 'TEXT',
        title: values.title,
        content: values.content,
        tags: values.tags
      })
      onCancel()
    } finally {
      setSubmitting(false)
    }
  }

  /** 提交 URL 创建 */
  async function handleUrlSubmit() {
    if (!knowledgeBaseCode) {
      message.warning('请先选择知识库')
      return
    }
    const values = await urlForm.validateFields()
    setSubmitting(true)
    try {
      await onCreateDocument({
        type: 'URL',
        title: values.title,
        url: values.url,
        tags: values.tags
      })
      onCancel()
    } finally {
      setSubmitting(false)
    }
  }

  // 公共 tags 选择项（建议输入）
  const tagsSelect = (
    <Form.Item name="tags" label="标签">
      <Select mode="tags" placeholder="输入后回车添加标签" tokenSeparators={[',']} />
    </Form.Item>
  )

  return (
    <Modal
      title="导入文档"
      open={open}
      onCancel={onCancel}
      footer={null}
      width={640}
      destroyOnClose
    >
      <Tabs
        activeKey={activeTab}
        onChange={(k) => setActiveTab(k as UploadTab)}
        items={[
          {
            key: 'file',
            label: '文件上传',
            children: (
              <Form form={fileForm} layout="vertical" className="mt-2">
                <Form.Item label="文件" required>
                  <Upload
                    fileList={fileList}
                    beforeUpload={beforeUpload}
                    maxCount={1}
                    onRemove={() => {
                      setFileList([])
                      return true
                    }}
                    onChange={({ fileList: fl }) => setFileList(fl.slice(-1))}
                  >
                    <Button>选择文件</Button>
                    <span className="ml-2 text-xs text-gray-400">
                      支持 PDF/DOCX/TXT/MD，≤20MB
                    </span>
                  </Upload>
                </Form.Item>
                <Form.Item name="title" label="标题">
                  <Input placeholder="选填，默认使用文件名" />
                </Form.Item>
                {tagsSelect}
                <div className="flex justify-end gap-2">
                  <Button onClick={onCancel}>取消</Button>
                  <Button type="primary" loading={submitting} onClick={handleFileSubmit}>
                    上传
                  </Button>
                </div>
              </Form>
            )
          },
          {
            key: 'text',
            label: '文本输入',
            children: (
              <Form form={textForm} layout="vertical" className="mt-2">
                <Form.Item
                  name="title"
                  label="标题"
                  rules={[{ required: true, message: '请输入标题' }]}
                >
                  <Input placeholder="请输入文档标题" />
                </Form.Item>
                <Form.Item
                  name="content"
                  label="内容"
                  rules={[{ required: true, message: '请输入内容' }]}
                >
                  <TextArea placeholder="粘贴或输入文本内容" rows={6} />
                </Form.Item>
                {tagsSelect}
                <div className="flex justify-end gap-2">
                  <Button onClick={onCancel}>取消</Button>
                  <Button type="primary" loading={submitting} onClick={handleTextSubmit}>
                    创建
                  </Button>
                </div>
              </Form>
            )
          },
          {
            key: 'url',
            label: 'URL 输入',
            children: (
              <Form form={urlForm} layout="vertical" className="mt-2">
                <Form.Item
                  name="title"
                  label="标题"
                  rules={[{ required: true, message: '请输入标题' }]}
                >
                  <Input placeholder="请输入文档标题" />
                </Form.Item>
                <Form.Item
                  name="url"
                  label="URL"
                  rules={[
                    { required: true, message: '请输入 URL' },
                    { type: 'url', message: 'URL 格式不正确' }
                  ]}
                >
                  <Input placeholder="https://example.com/article" />
                </Form.Item>
                {tagsSelect}
                <div className="flex justify-end gap-2">
                  <Button onClick={onCancel}>取消</Button>
                  <Button type="primary" loading={submitting} onClick={handleUrlSubmit}>
                    创建
                  </Button>
                </div>
              </Form>
            )
          }
        ]}
      />
    </Modal>
  )
}
