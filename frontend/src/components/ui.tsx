import type { ReactNode } from 'react'
import { money } from '../api'

export function Card({
  title, sub, action, children, className = '',
}: {
  title?: string
  sub?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={`rounded-2xl bg-white ring-1 ring-black/5 shadow-sm shadow-black/[0.03] ${className}`}
    >
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 px-5 pt-4 pb-2">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-gray-700">{title}</h2>}
            {sub && <p className="mt-0.5 text-xs text-gray-500">{sub}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="px-5 pb-5 pt-1">{children}</div>
    </section>
  )
}

export function Stat({
  label, value, sub, tone = 'default',
}: {
  label: string
  value: number | null
  sub?: ReactNode
  tone?: 'default' | 'good' | 'bad' | 'brand'
}) {
  const tones: Record<string, string> = {
    default: 'text-gray-900',
    good: 'text-emerald-600',
    bad: 'text-rose-600',
    brand: 'text-brand-600',
  }
  return (
    <div className="rounded-2xl bg-white px-5 py-4 ring-1 ring-black/5 shadow-sm shadow-black/[0.03]">
      <div className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`tnum mt-1 text-2xl font-semibold ${tones[tone]}`}>{money(value)}</div>
      {sub && <div className="mt-1 text-xs text-gray-500">{sub}</div>}
    </div>
  )
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-12 text-center">
      <p className="text-sm font-medium text-gray-700">{title}</p>
      {hint && <p className="max-w-sm text-xs text-gray-500">{hint}</p>}
    </div>
  )
}

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded-lg bg-gray-200/70 ${className}`} />
}

export function Pill({ children, color }: { children: ReactNode; color?: string | null }) {
  if (!color) {
    return (
      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600">
        {children}
      </span>
    )
  }
  // Tint from the category color rather than a fixed palette, so the pill, the
  // donut slice and the legend all agree.
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{ backgroundColor: `${color}1f`, color }}
    >
      <span className="size-1.5 rounded-full" style={{ backgroundColor: color }} />
      {children}
    </span>
  )
}
