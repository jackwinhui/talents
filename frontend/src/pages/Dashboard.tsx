import { useMemo, useState } from 'react'
import {
  Area, AreaChart, Bar, BarChart, Cell, Pie, PieChart, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import type { AccountRow, Insight, RecurringRow, Summary } from '../api'
import { money, monthLabel } from '../api'
import { Card, Empty, Stat } from '../components/ui'

const PALETTE = [
  '#1e5f63', '#e3b341', '#c4795a', '#2e7268', '#c6942b', '#7fb069',
  '#8b5e3c', '#5f9eaa', '#c4472c', '#9cc17f', '#d4633a', '#a9764c',
]
const GREEN = '#1e9e6a'
const RED = '#c4472c'

type Grain = 'month' | 'quarter' | 'year'

const GRAINS: [Grain, string][] = [
  ['month', 'Month'],
  ['quarter', 'Quarter'],
  ['year', 'Year'],
]

/** Month "2026-07" collapses into "2026-Q3" or "2026" depending on the grain. */
function bucketOf(month: string, grain: Grain): { key: string; label: string } {
  const [year, mm] = month.split('-')
  if (grain === 'year') return { key: year, label: year }
  if (grain === 'quarter') {
    const q = Math.ceil(Number(mm) / 3)
    return { key: `${year}-Q${q}`, label: `Q${q} ${year.slice(2)}` }
  }
  return { key: month, label: monthLabel(month) }
}

const axis = { stroke: '#9aa1a9', fontSize: 11, tickLine: false, axisLine: false }
const tooltipStyle = {
  contentStyle: {
    borderRadius: 12,
    border: '1px solid rgba(0,0,0,0.06)',
    boxShadow: '0 8px 24px rgba(0,0,0,0.08)',
    fontSize: 12,
  },
}

export function Dashboard({
  summary, accounts, insights, bills, period, onPeriodChange, onConnect,
}: {
  summary: Summary | null
  accounts: AccountRow[]
  insights: Insight[]
  bills: RecurringRow[]
  period: string
  onPeriodChange: (p: string) => void
  onConnect: () => void
}) {
  const cats = summary?.categories ?? []
  const months = summary?.months ?? []
  const totals = summary?.period_totals
  const topCats = cats.slice(0, 9)

  const [grain, setGrain] = useState<Grain>('month')

  const periods = ['current', ...(summary?.years ?? []), 'all']
  const periodLabel = (p: string) =>
    p === 'current' ? 'This month' : p === 'all' ? 'All time' : p

  // Running total of income minus spending. Notion charted this as "Cumulative Net
  // Spending"; it answers whether you are ahead over time, which a per-month bar
  // cannot show.
  const isThisMonth = period === 'current'
  const cumulative = useMemo(() => {
    let running = 0
    return months.map((m) => {
      running += m.net
      return { key: m.month, cumulative: Math.round(running * 100) / 100, net: m.net }
    })
  }, [months])

  // A single month is one bar, so viewing "This month" plots the running total
  // day by day within the month instead of income and spending side by side.
  const daily = useMemo(() => {
    let running = 0
    return (summary?.days ?? []).map((d) => {
      running += d.net
      return { key: d.date, cumulative: Math.round(running * 100) / 100, net: d.net }
    })
  }, [summary?.days])

  const cumLabel = monthLabel
  const dayLabel = (v: string) =>
    new Date(v + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

  // Ending below zero means the period spent more than it earned, so neither chart
  // should stay green just because it is a savings chart.
  const cumColor = (cumulative.at(-1)?.cumulative ?? 0) < 0 ? RED : GREEN
  const dayColor = (daily.at(-1)?.cumulative ?? 0) < 0 ? RED : GREEN

  const savingsRate =
    totals && totals.income > 0 ? Math.round((totals.net / totals.income) * 100) : null

  const shownMonths = useMemo(() => {
    if (period === 'all' || period === 'current') return months
    return months.filter((m) => m.month.startsWith(period))
  }, [months, period])

  const bars = useMemo(() => {
    const buckets = new Map<string, { label: string; income: number; spent: number; net: number }>()
    for (const m of shownMonths) {
      const { key, label } = bucketOf(m.month, grain)
      const bucket = buckets.get(key) ?? { label, income: 0, spent: 0, net: 0 }
      bucket.income += m.income
      bucket.spent += m.spent
      bucket.net += m.net
      buckets.set(key, bucket)
    }
    return [...buckets.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([, v]) => ({
        label: v.label,
        income: Math.round(v.income * 100) / 100,
        spent: Math.round(v.spent * 100) / 100,
        net: Math.round(v.net * 100) / 100,
      }))
  }, [shownMonths, grain])

  // A brand-new install opens here, on a dashboard of zeros. Without a way
  // forward the obvious reading is that the app is broken rather than empty,
  // and the one thing that needs doing lives on another tab.
  if (!accounts.length) {
    return (
      <Card title="Welcome to Talents">
        <p className="text-sm leading-relaxed text-gray-600">
          Nothing is connected yet, so there is nothing to show. Link a bank and
          Talents will pull in your history — up to two years where the bank offers
          it — then work out your spending, bills and budgets from it.
        </p>
        <button
          onClick={onConnect}
          className="mt-4 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white
                     hover:bg-brand-700"
        >
          Connect your first account
        </button>
        <p className="mt-3 text-xs leading-relaxed text-gray-500">
          Everything stays on this Mac. Nothing is uploaded anywhere.
        </p>
      </Card>
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-1 rounded-xl bg-gray-100 p-1">
        {periods.map((p) => (
          <button
            key={p}
            onClick={() => onPeriodChange(p)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
              period === p ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            {periodLabel(p)}
          </button>
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label={`Spent · ${periodLabel(period)}`} value={totals?.spent ?? null} tone="bad" />
        <Stat label={`Income · ${periodLabel(period)}`} value={totals?.income ?? null} tone="good" />
        <Stat
          label="Net"
          value={totals?.net ?? null}
          tone={(totals?.net ?? 0) >= 0 ? 'good' : 'bad'}
          sub={savingsRate != null ? `${savingsRate}% of income kept` : undefined}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card
          title={isThisMonth ? 'Cumulative savings · this month' : 'Income vs. spending'}
          className="lg:col-span-2"
          action={
            isThisMonth ? undefined : (
              <div className="flex gap-1 rounded-lg bg-gray-100 p-0.5">
                {GRAINS.map(([id, label]) => (
                  <button
                    key={id}
                    onClick={() => setGrain(id)}
                    className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
                      grain === id ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )
          }
        >
          {isThisMonth ? (
            daily.length ? (
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={daily} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                  <defs>
                    <linearGradient id="day" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={dayColor} stopOpacity={0.3} />
                      <stop offset="100%" stopColor={dayColor} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="key" tickFormatter={dayLabel} {...axis} />
                  <YAxis tickFormatter={(v) => money(Number(v))} width={64} {...axis} />
                  <Tooltip
                    {...tooltipStyle}
                    formatter={(v) => money(Number(v), true)}
                    labelFormatter={(l) => dayLabel(String(l))}
                  />
                  <ReferenceLine y={0} stroke="rgba(0,0,0,0.18)" strokeDasharray="3 3" />
                  <Area
                    type="monotone" dataKey="cumulative" name="Cumulative"
                    stroke={dayColor} strokeWidth={2} fill="url(#day)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <Empty title="Nothing recorded this month yet" />
            )
          ) : bars.length ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={bars} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                <XAxis dataKey="label" {...axis} />
                <YAxis tickFormatter={(v) => money(Number(v))} width={64} {...axis} />
                <Tooltip
                  {...tooltipStyle}
                  formatter={(v, n) => [money(Number(v), true), String(n)]}
                />
                <Bar dataKey="income" name="Income" fill={GREEN} radius={[5, 5, 0, 0]} />
                <Bar dataKey="spent" name="Spent" fill={RED} radius={[5, 5, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <Empty title="No transactions yet" hint="Run a sync to pull them from your banks." />
          )}
        </Card>

        <Card title={`Categories · ${periodLabel(period)}`}>
          {topCats.length ? (
            <>
              <ResponsiveContainer width="100%" height={190}>
                <PieChart>
                  <Pie
                    data={topCats} dataKey="amount" nameKey="category"
                    innerRadius={52} outerRadius={82} paddingAngle={2} stroke="none"
                  >
                    {topCats.map((c, i) => (
                      <Cell key={i} fill={c.color || PALETTE[i % PALETTE.length]} />
                    ))}
                  </Pie>
                  <Tooltip {...tooltipStyle} formatter={(v) => money(Number(v), true)} />
                </PieChart>
              </ResponsiveContainer>
              <ul className="mt-2 space-y-1.5">
                {topCats.map((c, i) => (
                  <li key={c.category} className="flex items-center gap-2 text-xs">
                    <span
                      className="size-2.5 shrink-0 rounded-full"
                      style={{ background: c.color || PALETTE[i % PALETTE.length] }}
                    />
                    <span className="flex-1 truncate text-gray-600">{c.category}</span>
                    <span className="tnum font-medium text-gray-900">{money(c.amount)}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <Empty title="Nothing spent in this period" />
          )}
        </Card>
      </div>

      <div className="grid gap-5">
        <Card title="Cumulative savings">
          {cumulative.length > 1 ? (
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={cumulative} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="cum" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={cumColor} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={cumColor} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="key" tickFormatter={cumLabel} {...axis} />
                <YAxis tickFormatter={(v) => money(Number(v))} width={64} {...axis} />
                <Tooltip
                  {...tooltipStyle}
                  formatter={(v) => money(Number(v), true)}
                  labelFormatter={(l) => cumLabel(String(l))}
                />
                <ReferenceLine y={0} stroke="rgba(0,0,0,0.18)" strokeDasharray="3 3" />
                <Area
                  type="monotone" dataKey="cumulative" name="Cumulative"
                  stroke={cumColor} strokeWidth={2} fill="url(#cum)"
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <Empty title="Not enough history yet" />
          )}
        </Card>

      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Month by month" className="lg:col-span-2">
          <div className="max-h-80 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-white">
                <tr className="border-b border-gray-100 text-left text-xs uppercase tracking-wide text-gray-500">
                  <th className="py-2 pr-3 font-medium">Month</th>
                  <th className="py-2 pr-3 text-right font-medium">Income</th>
                  <th className="py-2 pr-3 text-right font-medium">Spent</th>
                  <th className="py-2 text-right font-medium">Net</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {[...shownMonths].reverse().map((m) => (
                  <tr key={m.month} className="hover:bg-gray-50/70">
                    <td className="py-2 pr-3 font-medium text-gray-800">{monthLabel(m.month)}</td>
                    <td className="tnum py-2 pr-3 text-right text-emerald-600">
                      {money(m.income)}
                    </td>
                    <td className="tnum py-2 pr-3 text-right text-gray-700">{money(m.spent)}</td>
                    <td
                      className={`tnum py-2 text-right font-semibold ${
                        m.net >= 0 ? 'text-emerald-600' : 'text-rose-600'
                      }`}
                    >
                      {money(m.net)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title="Accounts">
          <ul className="divide-y divide-gray-100">
            {accounts.map((a) => (
              <li key={a.id} className="flex items-center gap-3 py-2.5">
                <span
                  className={`size-2 shrink-0 rounded-full ${
                    a.is_asset ? 'bg-emerald-500' : 'bg-amber-500'
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-gray-800">{a.name}</p>
                  <p className="truncate text-xs text-gray-500">
                    {a.institution}
                    {a.mask ? ` ····${a.mask}` : ''}
                  </p>
                </div>
                <span
                  className={`tnum text-sm font-semibold ${
                    a.is_asset ? 'text-gray-900' : 'text-rose-600'
                  }`}
                >
                  {money(a.balance, true)}
                </span>
              </li>
            ))}
            {!accounts.length && <Empty title="No accounts connected" />}
          </ul>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Where you could save" className="lg:col-span-2">
          {insights.length ? (
            <ul className="space-y-3">
              {insights.slice(0, 5).map((i, idx) => (
                <li key={idx} className="flex gap-3 rounded-xl bg-gray-50/70 p-3.5">
                  <span
                    className={`mt-1 size-2 shrink-0 rounded-full ${
                      i.severity === 'high'
                        ? 'bg-rose-500'
                        : i.severity === 'medium'
                          ? 'bg-amber-500'
                          : 'bg-gray-400'
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-gray-900">{i.title}</p>
                    <p className="mt-0.5 text-xs leading-relaxed text-gray-600">{i.body}</p>
                  </div>
                  {i.estimated_monthly_savings > 0 && (
                    <span className="tnum shrink-0 self-start rounded-lg bg-emerald-50 px-2 py-1
                                     text-xs font-semibold text-emerald-700">
                      {money(i.estimated_monthly_savings)}/mo
                    </span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="Nothing to flag right now" />
          )}
        </Card>

        <Card title="Coming up">
          {bills.length ? (
            <ul className="divide-y divide-gray-100">
              {bills.slice(0, 7).map((b) => {
                const d = b.next_due
                  ? Math.round((new Date(b.next_due).getTime() - Date.now()) / 86_400_000)
                  : null
                return (
                  <li key={b.id} className="flex items-center gap-2 py-2.5">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-gray-800">{b.name}</p>
                      {d != null && (
                        <p className="text-xs text-gray-500">
                          {d <= 0 ? 'due now' : d === 1 ? 'tomorrow' : `in ${d} days`}
                        </p>
                      )}
                    </div>
                    <span className="tnum text-sm font-semibold text-gray-900">
                      {money(b.amount)}
                    </span>
                  </li>
                )
              })}
            </ul>
          ) : (
            <Empty title="No upcoming bills detected" />
          )}
        </Card>
      </div>
    </div>
  )
}
