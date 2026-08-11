import { Card, Statistic } from 'antd'
import type { ReactNode } from 'react'

interface StatCardProps {
  /** 卡片标题 */
  title: string
  /** 统计数值（为 null/undefined 时显示"—"占位） */
  value: number | null | undefined
  /** 数值前缀图标占位 */
  prefix?: ReactNode
  /** 数值后缀（如"个"、"次"） */
  suffix?: string
  /** 数值颜色（用于异常等强调） */
  valueStyle?: React.CSSProperties
  /** 加载中状态 */
  loading?: boolean
}

/**
 * 全局概览统计卡片
 * - 用于展示项目总数、活跃项目数、今日总调用、异常数
 * - 数值缺失时显示"—"占位，避免页面空白
 */
export default function StatCard({
  title,
  value,
  prefix,
  suffix,
  valueStyle,
  loading
}: StatCardProps) {
  return (
    <Card
      className="!rounded-2xl !border-slate-200/80 !bg-white/90 !shadow-[0_12px_32px_rgba(23,32,51,0.06)]"
      bodyStyle={{ padding: 22 }}
    >
      <Statistic
        title={title}
        // 数值缺失时显示占位"—"，避免页面因接口失败而空白
        value={value === null || value === undefined ? '—' : value}
        prefix={prefix}
        suffix={suffix}
        valueStyle={valueStyle}
        loading={loading}
      />
    </Card>
  )
}
