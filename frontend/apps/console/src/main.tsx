import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { RouterProvider } from 'react-router-dom'
import ErrorBoundary from '@/components/ErrorBoundary'
import { router } from '@/router'
import '@/styles.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 20_000,
      retry: (count, error: any) => error?.status !== 401 && count < 1,
      refetchOnWindowFocus: false
    },
    mutations: { retry: false }
  }
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: '#6558f5',
            colorInfo: '#6558f5',
            colorSuccess: '#17a673',
            colorWarning: '#f59e0b',
            colorError: '#ef5466',
            colorText: '#151b2b',
            colorTextSecondary: '#697386',
            colorBgLayout: '#f6f7fb',
            colorBorderSecondary: '#e8eaf1',
            borderRadius: 12,
            fontSize: 14
          },
          components: {
            Button: { controlHeight: 40, fontWeight: 650, primaryShadow: '0 8px 18px rgba(101, 88, 245, 0.2)' },
            Input: { controlHeight: 42, activeShadow: '0 0 0 3px rgba(101, 88, 245, 0.12)' },
            Select: { controlHeight: 42 },
            Modal: { borderRadiusLG: 18 },
            Table: { headerBg: '#f8f9fc', headerColor: '#4c566a' }
          }
        }}
      >
        <AntApp>
          <QueryClientProvider client={queryClient}>
            <RouterProvider router={router} future={{ v7_startTransition: true }} />
          </QueryClientProvider>
        </AntApp>
      </ConfigProvider>
    </ErrorBoundary>
  </React.StrictMode>
)
