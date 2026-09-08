import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Form, Input } from 'antd'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { asPlatformError } from '@/api/client'
import { sessionApi } from '@/api/platform'
import BrandMark from '@/components/BrandMark'

export default function LoginPage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const session = useQuery({ queryKey: ['session'], queryFn: sessionApi.me, retry: false })
  const login = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      sessionApi.login(email, password),
    onSuccess: (data) => {
      queryClient.setQueryData(['session'], data)
      const next = (location.state as { from?: string } | null)?.from || '/'
      navigate(next, { replace: true })
    },
    onError: (error) => {
      const platformError = asPlatformError(error)
      message.error(platformError.message)
    }
  })

  // 平台服务不可用时全局警告提示
  useEffect(() => {
    if (session.error && asPlatformError(session.error).status !== 401) {
      message.warning('暂时无法连接平台服务，请确认 API 服务已启动')
    }
  }, [session.error, message])

  if (session.data) return <Navigate to="/" replace />

  return (
    <main className="login-page">
      <section className="login-story">
        <BrandMark large />
        <span className="aik-eyebrow">AI KNOWLEDGE CORE</span>
        <h1>让每一个 AI 项目，都拥有可验证的专属知识。</h1>
        <p>统一管理知识、检索、回答策略、运行错误与接入密钥。</p>
        <div className="login-principles">
          <span>知识优先</span>
          <span>模型可控兜底</span>
          <span>结果全程可追溯</span>
        </div>
      </section>
      <section className="login-panel">
        <div className="login-card">
          <span className="aik-eyebrow">管理控制台</span>
          <h2>欢迎回来</h2>
          <p>使用部署环境中配置的管理员账号登录。</p>
          <Form layout="vertical" onFinish={login.mutate} requiredMark={false}>
            <Form.Item label="管理员邮箱" name="email" rules={[{ required: true }, { type: 'email' }]}>
              <Input autoFocus autoComplete="username" placeholder="admin@example.local" />
            </Form.Item>
            <Form.Item label="密码" name="password" rules={[{ required: true }]}>
              <Input.Password autoComplete="current-password" placeholder="输入管理员密码" />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={login.isPending} block>
              进入平台
            </Button>
          </Form>
        </div>
      </section>
    </main>
  )
}
