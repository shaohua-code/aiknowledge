import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import { RouterProvider } from 'react-router-dom'
import { router } from '@/router'

// dayjs 中文语言包
dayjs.locale('zh-cn')

// react-query 客户端，统一缓存/重试策略
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30 * 1000
    }
  }
})

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: '#4f46e5',
            colorInfo: '#4f46e5',
            colorText: '#172033',
            colorTextSecondary: '#667085',
            colorBorderSecondary: '#e7eaf2',
            borderRadius: 10,
            fontSize: 14
          }
        }}
      >
        <RouterProvider router={router} />
      </ConfigProvider>
    </QueryClientProvider>
  )
}

export default App
