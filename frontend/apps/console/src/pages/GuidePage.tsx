import { useMemo, useState } from 'react'
import { Button, Tabs, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { PageHeader } from '@aik/ui'
import LineIcon from '@/components/LineIcon'

const { Paragraph, Text } = Typography

// 指南步骤数据：覆盖新手从启动到调用 API 的完整流程
interface GuideStep {
  key: string
  title: string
  summary: string
  details: Array<{
    label: string
    content: React.ReactNode
  }>
}

// 快速跳转锚点
const QUICK_LINKS = [
  { href: '#start', label: '快速启动', tone: 'violet' },
  { href: '#concepts', label: '核心概念', tone: 'blue' },
  { href: '#workflow', label: '使用流程', tone: 'green' },
  { href: '#faq', label: '常见问题', tone: 'amber' }
] as const

// 启动命令片段
const START_COMMANDS = [
  {
    label: '1. 进入项目目录',
    code: 'cd /d D:\\project\\aiknowledge'
  },
  {
    label: '2. 复制配置文件',
    code: 'copy .env.example .env'
  },
  {
    label: '3. 启动后端服务（数据库 + Redis + API + Worker）',
    code: 'docker compose up -d --build postgres redis api worker'
  },
  {
    label: '4. 启动前端控制台',
    code: 'docker compose up -d --build console'
  },
  {
    label: '5. 查看服务状态',
    code: 'docker compose ps'
  }
]

// 核心概念清单
const CORE_CONCEPTS = [
  {
    name: '应用空间 Application',
    icon: 'apps' as const,
    tone: 'violet' as const,
    description: '一个独立 AI 项目的最高隔离边界，例如 AI 简历、AI 基金。',
    example: 'ai-resume、ai-fund、ai-ecommerce'
  },
  {
    name: '环境 Environment',
    icon: 'layers' as const,
    tone: 'blue' as const,
    description: '每个应用自动拥有开发、测试、生产三个环境，互不干扰。',
    example: 'development、testing、production'
  },
  {
    name: '知识集合 Knowledge Collection',
    icon: 'database' as const,
    tone: 'green' as const,
    description: '同一用途的知识容器，例如简历规则、岗位能力、面试知识。',
    example: 'AI 简历：简历规范、岗位模型、公司知识'
  },
  {
    name: '回答策略 Answer Profile',
    icon: 'sparkle' as const,
    tone: 'amber' as const,
    description: '决定 AI 如何回答：使用哪些知识、是否联网、是否允许模型兜底。',
    example: 'resume_job_match、fund_risk_analysis'
  },
  {
    name: 'API Key',
    icon: 'shield' as const,
    tone: 'violet' as const,
    description: '业务后端调用平台接口的凭证，按应用+环境签发，明文只显示一次。',
    example: 'aik_live_xxxxxxxx（创建后立即复制保存）'
  },
  {
    name: '运行轨迹 Trace',
    icon: 'pulse' as const,
    tone: 'blue' as const,
    description: '每次请求的完整链路：检索、证据、模型、错误、Token、反馈。',
    example: '通过 requestId 在「运行与质量」页面查询'
  }
]

// 五步上手流程
const WORKFLOW_STEPS: GuideStep[] = [
  {
    key: 'step-1',
    title: '第一步 · 启动项目',
    summary: '使用 Docker 一键启动完整平台，第一次需要 4 步。',
    details: [
      {
        label: '前置准备',
        content: (
          <ul className="guide-list">
            <li>已安装 <Text code>Docker Desktop</Text> 并启动</li>
            <li>项目位于 <Text code>D:\project\aiknowledge</Text></li>
            <li>打开 CMD（命令提示符）</li>
          </ul>
        )
      },
      {
        label: '按顺序执行',
        content: (
          <div className="guide-code-blocks">
            {START_COMMANDS.map((item) => (
              <div className="guide-code-item" key={item.label}>
                <span className="guide-code-label">{item.label}</span>
                <pre className="guide-code-text">{item.code}</pre>
              </div>
            ))}
          </div>
        )
      },
      {
        label: '修改 .env 配置',
        content: (
          <div className="guide-env-hint">
            <p>使用记事本打开 <Text code>.env</Text>，确保以下 4 项配置正确：</p>
            <pre className="guide-code-text">{`POSTGRES_PASSWORD=123456
DATABASE_URL=postgresql+asyncpg://aiknowledge:123456@postgres:5432/aiknowledge_v2
ADMIN_PASSWORD=123456
SESSION_SECRET=12345678901234567890123456789012`}</pre>
            <p className="guide-warn">
              <LineIcon name="alert" size={14} /> 注意：<Text code>POSTGRES_PASSWORD</Text> 必须和 <Text code>DATABASE_URL</Text> 中的密码完全一致，否则后端无法连接数据库。
            </p>
          </div>
        )
      },
      {
        label: '验证启动成功',
        content: (
          <ul className="guide-list">
            <li>访问 <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer">http://localhost:8000/docs</a> 看到 API 文档</li>
            <li>访问 <a href="http://localhost:5173" target="_blank" rel="noreferrer">http://localhost:5173</a> 看到控制台登录页</li>
            <li>登录账号：<Text code>admin@example.local</Text> / 你设置的 <Text code>ADMIN_PASSWORD</Text></li>
          </ul>
        )
      }
    ]
  },
  {
    key: 'step-2',
    title: '第二步 · 创建应用空间',
    summary: '一个应用 = 一条独立知识边界。先创建应用，再导入知识。',
    details: [
      {
        label: '操作路径',
        content: (
          <div className="guide-path">
            <span className="guide-path-chip">控制台首页</span>
            <LineIcon name="arrow" size={14} />
            <span className="guide-path-chip">AI 应用</span>
            <LineIcon name="arrow" size={14} />
            <span className="guide-path-chip primary">创建 AI 应用</span>
          </div>
        )
      },
      {
        label: '填写表单',
        content: (
          <ul className="guide-list">
            <li><strong>应用名称</strong>：例如「AI 简历」</li>
            <li><strong>应用编码</strong>：小写字母+数字+连字符，例如 <Text code>ai-resume</Text>，创建后不可修改</li>
            <li><strong>应用类型</strong>：AI 简历 / AI 基金 / 通用知识 / 自定义</li>
            <li><strong>说明</strong>：可选，描述这个应用负责什么</li>
          </ul>
        )
      },
      {
        label: '自动生成',
        content: (
          <div className="guide-callout">
            <LineIcon name="sparkle" size={16} />
            <span>创建完成后，系统会自动生成 <strong>开发 / 测试 / 生产</strong> 三个隔离环境，无需手动配置。</span>
          </div>
        )
      }
    ]
  },
  {
    key: 'step-3',
    title: '第三步 · 导入知识',
    summary: '进入应用 → 知识中心 → 创建知识集合 → 添加文档。',
    details: [
      {
        label: '进入知识中心',
        content: (
          <div className="guide-path">
            <span className="guide-path-chip">应用列表</span>
            <LineIcon name="arrow" size={14} />
            <span className="guide-path-chip">进入应用</span>
            <LineIcon name="arrow" size={14} />
            <span className="guide-path-chip primary">知识中心</span>
          </div>
        )
      },
      {
        label: '创建知识集合',
        content: (
          <ul className="guide-list">
            <li>知识集合是同一类知识的容器，例如「简历写作规范」「岗位能力模型」</li>
            <li>不同集合可以独立管理权限和更新策略</li>
          </ul>
        )
      },
      {
        label: '添加文档（4 种数据源）',
        content: (
          <div className="guide-source-grid">
            <div className="guide-source-card">
              <strong>文件上传</strong>
              <p>直接上传 PDF、Word、Markdown 等文件</p>
            </div>
            <div className="guide-source-card">
              <strong>手动文本</strong>
              <p>粘贴或输入文本内容</p>
            </div>
            <div className="guide-source-card">
              <strong>网页地址</strong>
              <p>抓取公开网页内容，支持 HTML/Markdown</p>
            </div>
            <div className="guide-source-card">
              <strong>API 数据</strong>
              <p>支持 JSON/NDJSON、RSS/Atom、CSV/TSV</p>
            </div>
          </div>
        )
      },
      {
        label: '入库流程（自动）',
        content: (
          <div className="guide-pipeline">
            <span>接收校验</span>
            <i />
            <span>解析</span>
            <i />
            <span>切片</span>
            <i />
            <span>向量化</span>
            <i />
            <span>索引</span>
            <i />
            <span>原子发布</span>
          </div>
        )
      },
      {
        label: '查看进度',
        content: (
          <div className="guide-callout">
            <LineIcon name="pulse" size={16} />
            <span>进入 <Link to="/applications">应用概览</Link> 或「数据自动化」页面查看入库进度。新版本发布成功前，旧版本仍可检索，不会中断业务。</span>
          </div>
        )
      }
    ]
  },
  {
    key: 'step-4',
    title: '第四步 · 配置 AI 能力',
    summary: '配置检索策略和回答策略，并在 Playground 测试效果。',
    details: [
      {
        label: '进入 AI 能力',
        content: (
          <div className="guide-path">
            <span className="guide-path-chip">应用</span>
            <LineIcon name="arrow" size={14} />
            <span className="guide-path-chip primary">AI 能力</span>
          </div>
        )
      },
      {
        label: '检索策略 Retrieval Profile',
        content: (
          <ul className="guide-list">
            <li>选择可使用的知识集合</li>
            <li>调整全文与向量检索权重、Top K、分数阈值</li>
            <li>支持元数据过滤和文档去重</li>
          </ul>
        )
      },
      {
        label: '回答策略 Answer Profile',
        content: (
          <ul className="guide-list">
            <li>绑定检索策略和对话模型</li>
            <li>设置知识不足时是否允许模型兜底</li>
            <li>是否允许联网补充、最小证据要求、超时降级规则</li>
          </ul>
        )
      },
      {
        label: '平台级模型配置',
        content: (
          <div className="guide-callout">
            <LineIcon name="shield" size={16} />
            <span>进入「设置 → 模型与联网能力」配置对话模型、Embedding 模型、Serper 联网密钥。密钥只写入不回显，未配置时使用 <Text code>.env</Text> 默认值。</span>
          </div>
        )
      }
    ]
  },
  {
    key: 'step-5',
    title: '第五步 · 接入业务调用',
    summary: '创建 API Key，业务后端通过 Runtime API 调用回答接口。',
    details: [
      {
        label: '创建 API Key',
        content: (
          <div className="guide-path">
            <span className="guide-path-chip">应用</span>
            <LineIcon name="arrow" size={14} />
            <span className="guide-path-chip primary">开发者接入</span>
            <LineIcon name="arrow" size={14} />
            <span className="guide-path-chip">创建密钥</span>
          </div>
        )
      },
      {
        label: '权限范围 Scope',
        content: (
          <ul className="guide-list">
            <li><Text code>knowledge:read</Text> — 检索知识</li>
            <li><Text code>answer:run</Text> — 运行回答</li>
            <li><Text code>feedback:write</Text> — 提交反馈</li>
          </ul>
        )
      },
      {
        label: '重要提醒',
        content: (
          <div className="guide-callout danger">
            <LineIcon name="alert" size={16} />
            <span>API Key 明文 <strong>只在创建时显示一次</strong>，关闭弹窗后无法再查看。请立即复制保存到业务后端的环境变量中。</span>
          </div>
        )
      },
      {
        label: '调用示例',
        content: (
          <pre className="guide-code-text">{`curl -X POST http://localhost:8000/runtime/v1/answer \\
  -H "Authorization: Bearer <YOUR_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "profile": "resume_job_match",
    "query": "如何优化项目经历？",
    "inputs": {}
  }'`}</pre>
        )
      },
      {
        label: '业务项目接入步骤',
        content: (
          <ol className="guide-ordered">
            <li>在业务后端保存 API Key 到环境变量</li>
            <li>调用 <Text code>/runtime/v1/answer</Text> 获取 AI 回答</li>
            <li>使用返回的 <Text code>requestId</Text> 在「运行与质量」追踪问题</li>
            <li>按需调用 <Text code>/runtime/v1/feedback</Text> 提交用户反馈</li>
          </ol>
        )
      }
    ]
  }
]

// 常见问题
const FAQ_ITEMS = [
  {
    q: '启动后访问 5173 端口打不开？',
    a: '先执行 docker compose ps 确认 console 容器状态是 running。如果只开发前端，可在 frontend 目录执行 pnpm install && pnpm dev 启动独立开发服务。'
  },
  {
    q: '后端 API 文档（/docs）打不开？',
    a: '检查 api 容器是否 healthy。最常见原因是 .env 中 POSTGRES_PASSWORD 和 DATABASE_URL 里的密码不一致，修改后执行 docker compose up -d --build --force-recreate。'
  },
  {
    q: '登录提示平台服务连接失败？',
    a: '后端未就绪。查看日志：docker compose logs --tail=100 api，常见原因是数据库未启动或密码不匹配。'
  },
  {
    q: '上传文档后为什么搜索不到？',
    a: '文档入库是异步流程（抓取→解析→切片→向量化→索引→发布）。在「数据自动化」页面查看任务状态，新版本发布成功后才能被检索。'
  },
  {
    q: 'API Key 找不到了怎么办？',
    a: 'API Key 明文只显示一次，无法找回。请在「开发者接入」页面创建新的 API Key，并更新业务后端的配置。旧 Key 可以吊销。'
  },
  {
    q: '知识回答不准怎么办？',
    a: '1) 确认知识集合状态是 active；2) 在「AI 能力」调整检索权重和 Top K；3) 在 Playground 测试不同回答策略；4) 查看「运行与质量」中的证据和模式。'
  },
  {
    q: '如何停止项目？',
    a: '执行 docker compose down 即可停止所有服务，数据库数据会保留。切勿执行 docker compose down -v，会删除所有数据。'
  },
  {
    q: '前端如何独立开发？',
    a: 'cd frontend && pnpm install && pnpm dev。开发服务会自动将 API 请求代理到 localhost:8000，需要后端已启动。'
  }
]

export default function GuidePage() {
  const [activeStep, setActiveStep] = useState('step-1')

  // 平滑滚动到锚点
  const scrollToSection = (href: string) => {
    const element = document.querySelector(href)
    if (element) element.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // 渲染单个步骤内容
  const renderStep = (step: GuideStep) => (
    <div className="guide-step-content">
      <p className="guide-step-summary">{step.summary}</p>
      {step.details.map((detail) => (
        <div className="guide-detail-block" key={detail.label}>
          <h3 className="guide-detail-label">{detail.label}</h3>
          <div className="guide-detail-content">{detail.content}</div>
        </div>
      ))}
    </div>
  )

  // 渲染概念卡片
  const conceptCards = useMemo(
    () =>
      CORE_CONCEPTS.map((concept) => (
        <article className={`guide-concept-card tone-${concept.tone}`} key={concept.name}>
          <span className="guide-concept-icon">
            <LineIcon name={concept.icon} size={20} />
          </span>
          <strong>{concept.name}</strong>
          <p>{concept.description}</p>
          <small>{concept.example}</small>
        </article>
      )),
    []
  )

  return (
    <div className="page-container guide-page">
      {/* 页面头部 */}
      <PageHeader
        eyebrow="新手操作指南"
        title="从零开始使用 AI 知识能力底座"
        description="面向第一次接触本项目的开发者，覆盖启动、配置、知识导入、AI 调用和故障排查的完整流程。"
        actions={
          <Link to="/">
            <Button>
              返回工作台 <LineIcon name="arrow" size={15} />
            </Button>
          </Link>
        }
      />

      {/* Hero 引导区 */}
      <section className="guide-hero">
        <div className="guide-hero-copy">
          <span className="guide-hero-kicker">
            <i /> GETTING STARTED
          </span>
          <h2>5 步让 AI 应用拥有可验证的专属知识</h2>
          <p>本平台是面向 AI 简历、AI 基金等业务的统一知识与回答底座。业务系统只需调用一个接口，即可获得知识优先、模型兜底、证据可追溯的 AI 能力。</p>
          <div className="guide-hero-stats">
            <span><strong>5</strong> 个上手步骤</span>
            <span><strong>6</strong> 个核心概念</span>
            <span><strong>4</strong> 种数据源</span>
            <span><strong>3</strong> 个隔离环境</span>
          </div>
        </div>
        <div className="guide-hero-visual" aria-hidden="true">
          <div className="guide-orbit-ring" />
          <div className="guide-orbit-core">
            <LineIcon name="layers" size={28} />
            <strong>AIK</strong>
          </div>
          <div className="guide-orbit-node node-1"><i />知识</div>
          <div className="guide-orbit-node node-2"><i />检索</div>
          <div className="guide-orbit-node node-3"><i />回答</div>
        </div>
      </section>

      {/* 快速导航 */}
      <section className="guide-quick-nav" id="quick-nav">
        {QUICK_LINKS.map((link) => (
          <button
            type="button"
            key={link.href}
            className={`guide-quick-link tone-${link.tone}`}
            onClick={() => scrollToSection(link.href)}
          >
            {link.label}
          </button>
        ))}
      </section>

      {/* 核心概念 */}
      <section className="guide-section" id="concepts">
        <div className="guide-section-heading">
          <div>
            <span className="section-kicker">CORE CONCEPTS</span>
            <h2>先理解这 6 个概念</h2>
          </div>
          <span className="guide-section-hint">理解后再操作，少走弯路</span>
        </div>
        <div className="guide-concept-grid">{conceptCards}</div>
      </section>

      {/* 启动与使用流程 */}
      <section className="guide-section" id="start">
        <div className="guide-section-heading">
          <div>
            <span className="section-kicker">STEP BY STEP</span>
            <h2>5 步完整上手流程</h2>
          </div>
        </div>
        <div className="guide-workflow" id="workflow">
          <Tabs
            activeKey={activeStep}
            onChange={setActiveStep}
            tabPosition="left"
            items={WORKFLOW_STEPS.map((step, index) => ({
              key: step.key,
              label: (
                <span className="guide-tab-label">
                  <span className="guide-tab-index">{index + 1}</span>
                  <span className="guide-tab-title">{step.title.split(' · ')[1]}</span>
                </span>
              ),
              children: renderStep(step)
            }))}
          />
        </div>
      </section>

      {/* 架构总览 */}
      <section className="guide-section">
        <div className="guide-section-heading">
          <div>
            <span className="section-kicker">ARCHITECTURE</span>
            <h2>平台如何工作</h2>
          </div>
        </div>
        <div className="guide-arch-grid">
          <article className="guide-arch-card">
            <span className="guide-arch-icon violet"><LineIcon name="layers" size={20} /></span>
            <h3>控制面 Control API</h3>
            <p>管理员通过 HttpOnly Session 操作，前缀 <Text code>/control/v1</Text>。管理知识、策略、API Key 和运行轨迹。</p>
          </article>
          <article className="guide-arch-card">
            <span className="guide-arch-icon blue"><LineIcon name="pulse" size={20} /></span>
            <h3>运行面 Runtime API</h3>
            <p>业务后端通过 Bearer API Key 调用，前缀 <Text code>/runtime/v1</Text>。提供 <Text code>/retrieve</Text>、<Text code>/answer</Text>、<Text code>/feedback</Text> 三个核心接口。</p>
          </article>
          <article className="guide-arch-card">
            <span className="guide-arch-icon green"><LineIcon name="database" size={20} /></span>
            <h3>数据隔离</h3>
            <p>应用 <Text code>application_id</Text> + 环境 <Text code>environment_id</Text> 双重隔离，所有数据表都携带这两个字段，杜绝跨项目数据泄漏。</p>
          </article>
          <article className="guide-arch-card">
            <span className="guide-arch-icon amber"><LineIcon name="sparkle" size={20} /></span>
            <h3>异步入库</h3>
            <p>Celery Worker 异步执行抓取、解析、切片、向量化。新版本发布前旧版本仍可检索，不会中断业务。</p>
          </article>
        </div>
      </section>

      {/* 常见问题 */}
      <section className="guide-section" id="faq">
        <div className="guide-section-heading">
          <div>
            <span className="section-kicker">FAQ</span>
            <h2>常见问题与故障排查</h2>
          </div>
        </div>
        <div className="guide-faq-list">
          {FAQ_ITEMS.map((item, index) => (
            <details className="guide-faq-item" key={index}>
              <summary>
                <span className="guide-faq-q">Q{index + 1}</span>
                <span>{item.q}</span>
                <i className="guide-faq-arrow" />
              </summary>
              <div className="guide-faq-a">
                <Paragraph>{item.a}</Paragraph>
              </div>
            </details>
          ))}
        </div>
      </section>

      {/* 常用命令速查 */}
      <section className="guide-section">
        <div className="guide-section-heading">
          <div>
            <span className="section-kicker">CHEATSHEET</span>
            <h2>常用命令速查</h2>
          </div>
        </div>
        <div className="guide-cheatsheet">
          <div className="guide-cheat-item">
            <span className="guide-cheat-label">查看服务状态</span>
            <pre className="guide-code-text">docker compose ps</pre>
          </div>
          <div className="guide-cheat-item">
            <span className="guide-cheat-label">查看后端日志</span>
            <pre className="guide-code-text">docker compose logs -f api</pre>
          </div>
          <div className="guide-cheat-item">
            <span className="guide-cheat-label">查看 Worker 日志</span>
            <pre className="guide-code-text">docker compose logs -f worker</pre>
          </div>
          <div className="guide-cheat-item">
            <span className="guide-cheat-label">重启整个项目</span>
            <pre className="guide-code-text">docker compose restart</pre>
          </div>
          <div className="guide-cheat-item">
            <span className="guide-cheat-label">停止项目（保留数据）</span>
            <pre className="guide-code-text">docker compose down</pre>
          </div>
          <div className="guide-cheat-item">
            <span className="guide-cheat-label">修改配置后重启</span>
            <pre className="guide-code-text">docker compose up -d --build --force-recreate</pre>
          </div>
        </div>
        <div className="guide-callout danger">
          <LineIcon name="alert" size={16} />
          <span>切勿执行 <Text code>docker compose down -v</Text>，它会删除数据库所有数据。</span>
        </div>
      </section>

      {/* 下一步 */}
      <section className="guide-next">
        <div className="guide-next-card">
          <span className="section-kicker">READY TO GO</span>
          <h2>开始创建第一个 AI 应用</h2>
          <p>概念已理解，流程已掌握，现在就可以动手实践。</p>
          <div className="guide-next-actions">
            <Link to="/applications?create=true">
              <Button type="primary" size="large">
                创建 AI 应用 <LineIcon name="arrow" size={16} />
              </Button>
            </Link>
            <Link to="/">
              <Button size="large">返回工作台</Button>
            </Link>
          </div>
        </div>
      </section>
    </div>
  )
}
