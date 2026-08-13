import { createBrowserRouter, Navigate } from 'react-router-dom'
import ConsoleShell from '@/layouts/ConsoleShell'
import ApplicationShell from '@/layouts/ApplicationShell'
import RouteErrorPage from '@/pages/RouteErrorPage'

export const router = createBrowserRouter(
  [
    {
      path: '/login',
      lazy: async () => ({ Component: (await import('@/pages/LoginPage')).default })
    },
    {
      path: '/',
      element: <ConsoleShell />,
      errorElement: <RouteErrorPage />,
      children: [
        {
          index: true,
          lazy: async () => ({ Component: (await import('@/pages/DashboardPage')).default })
        },
        {
          path: 'applications',
          lazy: async () => ({ Component: (await import('@/pages/ApplicationsPage')).default })
        },
        {
          path: 'applications/:applicationId/:environmentId',
          element: <ApplicationShell />,
          children: [
            { index: true, element: <Navigate to="overview" replace /> },
            {
              path: 'overview',
              lazy: async () => ({ Component: (await import('@/pages/ApplicationOverviewPage')).default })
            },
            {
              path: 'knowledge',
              lazy: async () => ({ Component: (await import('@/pages/KnowledgePage')).default })
            },
            {
              path: 'intelligence',
              lazy: async () => ({ Component: (await import('@/pages/IntelligencePage')).default })
            },
            {
              path: 'automation',
              lazy: async () => ({ Component: (await import('@/pages/AutomationPage')).default })
            },
            {
              path: 'developer',
              lazy: async () => ({ Component: (await import('@/pages/DeveloperPage')).default })
            },
            {
              path: 'operations',
              lazy: async () => ({ Component: (await import('@/pages/OperationsPage')).default })
            },
            {
              path: 'settings',
              lazy: async () => ({ Component: (await import('@/pages/SettingsPage')).default })
            }
          ]
        }
      ]
    }
  ],
  { future: { v7_relativeSplatPath: true } }
)
