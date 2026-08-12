import { Button, Result } from 'antd'
import { isRouteErrorResponse, useRouteError } from 'react-router-dom'

export default function RouteErrorPage() {
  const error = useRouteError()
  const description = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : '路由加载失败'
  return (
    <Result
      status="error"
      title="页面无法打开"
      subTitle={description}
      extra={<Button onClick={() => window.location.assign('/')}>返回工作台</Button>}
    />
  )
}

