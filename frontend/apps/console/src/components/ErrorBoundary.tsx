import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button, Result } from 'antd'

interface State {
  error?: Error
}

export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = {}

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('UI_FATAL_ERROR', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="aik-fatal-page">
        <Result
          status="500"
          title="控制台遇到未处理错误"
          subTitle="这不是空数据。请刷新页面；若问题持续，请复制浏览器控制台信息进行诊断。"
          extra={
            <Button type="primary" onClick={() => window.location.reload()}>
              刷新控制台
            </Button>
          }
        />
      </div>
    )
  }
}

