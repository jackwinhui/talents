import { useEffect, useMemo, useState } from 'react'
import {
  Cell, Pie, PieChart, ResponsiveContainer, Tooltip,
} from 'recharts'
import type { Investments as InvestmentsData } from '../api'
import { api, money } from '../api'
import { Card, Empty, Skeleton, Stat } from '../components/ui'

const PALETTE = ['#2f6fb0', '#1e9e6a', '#c8912f', '#8a5cc4', '#c4472c', '#3f9ca8', '#b06090', '#6b7f95']

const pct = (v: number | null) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`)

// A blended fund reported as a single ticker is not really one company, so the
// concentration warning would be nonsense for it.
const isFund = (ticker: string | null, type: string | null) =>
  type === 'etf' || type === 'mutual fund' || (ticker ?? '').includes('.')

export function Investments() {
  const [data, setData] = useState<InvestmentsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [accountId, setAccountId] = useState<number | null>(null)

  useEffect(() => {
    api.investments().then((d) => {
      setData(d)
      setLoading(false)
    })
  }, [])

  const positions = useMemo(
    () => (data?.positions ?? []).filter((p) => accountId == null || p.account_id === accountId),
    [data, accountId],
  )

  // Recomputed for the selection rather than reusing the portfolio figures, so
  // the headline numbers always describe the rows shown underneath them.
  const view = useMemo(() => {
    const priced = positions.filter((p) => p.gain != null)
    const basis = priced.reduce((n, p) => n + (p.cost_basis ?? 0), 0)
    const gain = priced.reduce((n, p) => n + (p.gain ?? 0), 0)
    const unpriced = positions
      .filter((p) => p.gain == null)
      .reduce((n, p) => n + (p.value ?? 0), 0)
    const total =
      accountId == null
        ? (data?.portfolio_value ?? 0)
        : (data?.accounts.find((a) => a.id === accountId)?.balance ?? 0)

    const tickers = new Map<string, { ticker: string; name: string | null; type: string | null; value: number }>()
    for (const p of positions) {
      const key = p.ticker ?? p.name ?? 'unknown'
      const e = tickers.get(key) ?? { ticker: key, name: p.name, type: p.type, value: 0 }
      e.value += p.value ?? 0
      tickers.set(key, e)
    }
    const byTicker = [...tickers.values()]
      .sort((a, b) => b.value - a.value)
      .map((t) => ({ ...t, share: total ? (t.value / total) * 100 : 0 }))

    return {
      total,
      gain: basis ? gain : null,
      gainPct: basis ? (gain / basis) * 100 : null,
      basis,
      unpriced,
      byTicker,
    }
  }, [positions, accountId, data])

  const donut = useMemo(() => view.byTicker.filter((t) => t.value > 0).slice(0, 8), [view])

  // A single company being a fifth of the portfolio is worth saying out loud. A
  // fund being that large is just diversification working, so funds are excluded.
  const concentrated = useMemo(
    () => (data?.by_ticker ?? []).filter((t) => t.share >= 20 && !isFund(t.ticker, t.type)),
    [data],
  )

  if (loading) {
    return (
      <div className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
        </div>
        <Skeleton className="h-64" />
      </div>
    )
  }

  if (!data || !data.accounts.length) {
    return (
      <Card title="Investments">
        <Empty
          title="No investment accounts connected"
          hint="Connect a brokerage on the Accounts tab to see holdings here."
        />
      </Card>
    )
  }

  const emp = data.employer_exposure
  const selected = data.accounts.find((a) => a.id === accountId) ?? null

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-1 rounded-xl bg-gray-100 p-1">
        <button
          onClick={() => setAccountId(null)}
          className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
            accountId == null ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
          }`}
        >
          All accounts
        </button>
        {data.accounts.map((a) => (
          <button
            key={a.id}
            onClick={() => setAccountId(a.id)}
            title={a.name}
            className={`max-w-[16rem] truncate rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              accountId === a.id ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
            }`}
          >
            {a.name}
          </button>
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat
          label={selected ? 'Account value' : 'Portfolio'}
          value={view.total}
          tone="brand"
          sub={selected?.subtype ?? undefined}
        />
        <Stat
          label="Unrealized gain"
          value={view.gain}
          tone={view.gain == null ? 'default' : view.gain >= 0 ? 'good' : 'bad'}
          sub={
            view.gain == null
              ? 'no cost basis reported'
              : `${pct(view.gainPct)} on ${money(view.basis)} invested`
          }
        />
        <Stat
          label="Cost basis unknown"
          value={view.unpriced}
          sub={
            view.unpriced > 0
              ? 'gain cannot be worked out for this part'
              : 'every position has a basis'
          }
        />
      </div>

      {emp && emp.share >= 10 && accountId == null && (
        <div className="rounded-2xl bg-amber-50 px-5 py-4 ring-1 ring-amber-200/70">
          <p className="text-sm font-semibold text-amber-900">
            {emp.share.toFixed(1)}% of your portfolio is {emp.employer} stock
          </p>
          <p className="mt-1 text-xs leading-relaxed text-amber-800">
{money(emp.value)} in {emp.holdings.join(' and ')}, on top of a salary from the same
            company — a bad year there would hit your income and your savings at once.
          </p>
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Holdings" className="lg:col-span-2">
          {positions.length === 0 ? (
            <Empty
              title="No holdings reported for this account"
              hint={
                selected && selected.uninvested > 1
                  ? `The broker reports ${money(selected.uninvested)} here but no positions, which is normal for a stock plan between purchase dates.`
                  : undefined
              }
            />
          ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-left text-xs text-gray-500">
                  <th className="pb-2 font-medium">Holding</th>
                  <th className="pb-2 text-right font-medium">Units</th>
                  <th className="pb-2 text-right font-medium">Price</th>
                  <th className="pb-2 text-right font-medium">Value</th>
                  <th className="pb-2 text-right font-medium">Gain</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {positions.map((p) => (
                  <tr key={p.id}>
                    <td className="py-2.5 pr-3">
                      <p className="font-medium text-gray-900">{p.ticker ?? p.name}</p>
                      <p className="truncate text-xs text-gray-500" title={p.name ?? ''}>
                        {p.account}
                      </p>
                    </td>
                    <td className="tnum py-2.5 text-right text-gray-600">
                      {p.quantity.toLocaleString(undefined, { maximumFractionDigits: 3 })}
                    </td>
                    <td className="tnum py-2.5 text-right text-gray-600">
                      {p.price == null ? '—' : money(p.price, true)}
                    </td>
                    <td className="tnum py-2.5 text-right font-medium text-gray-900">
                      {money(p.value)}
                    </td>
                    <td
                      className={`tnum py-2.5 text-right font-medium ${
                        p.gain == null ? 'text-gray-400' : p.gain >= 0 ? 'text-emerald-600' : 'text-rose-600'
                      }`}
                    >
                      {p.gain == null ? '—' : (
                        <>
                          {money(p.gain, true)}
                          <span className="ml-1 text-xs font-normal">{pct(p.gain_pct)}</span>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          )}
          {view.unpriced > 0 && (
            <p className="mt-3 border-t border-gray-100 pt-3 text-xs leading-relaxed text-gray-500">
A dash means the broker reported no cost basis, so gain cannot be
              calculated for that position.
            </p>
          )}
        </Card>

        <div className="space-y-5">
          <Card title="Allocation">
            {donut.length ? (
              <>
                <ResponsiveContainer width="100%" height={180}>
                  <PieChart>
                    <Pie
                      data={donut} dataKey="value" nameKey="ticker"
                      innerRadius={48} outerRadius={78} paddingAngle={2} stroke="none"
                    >
                      {donut.map((_, i) => (
                        <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(v) => money(Number(v), true)} />
                  </PieChart>
                </ResponsiveContainer>
                <ul className="mt-2 space-y-1.5">
                  {donut.map((t, i) => (
                    <li key={t.ticker} className="flex items-center gap-2 text-xs">
                      <span
                        className="size-2.5 shrink-0 rounded-full"
                        style={{ background: PALETTE[i % PALETTE.length] }}
                      />
                      <span className="flex-1 truncate text-gray-600" title={t.name ?? ''}>
                        {t.ticker}
                      </span>
                      <span className="tnum text-gray-500">{t.share.toFixed(1)}%</span>
                      <span className="tnum w-20 text-right font-medium text-gray-900">
                        {money(t.value)}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <Empty title="No holdings reported" />
            )}
          </Card>

          <Card title="Accounts">
            <ul className="divide-y divide-gray-50">
              {data.accounts.map((a) => (
                <li key={a.id}>
                  <button
                    onClick={() => setAccountId(accountId === a.id ? null : a.id)}
                    className={`-mx-2 w-[calc(100%+1rem)] rounded-lg px-2 py-2.5 text-left transition
                                hover:bg-gray-50 ${accountId === a.id ? 'bg-brand-50/70' : ''}`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-sm text-gray-700" title={a.name}>
                        {a.name}
                      </span>
                      <span className="tnum text-sm font-semibold text-gray-900">
                        {money(a.balance)}
                      </span>
                    </div>
                    <p className="text-xs text-gray-500">
                      {a.subtype}
                      {a.uninvested > 1 && ` · ${money(a.uninvested)} not in holdings`}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>

      {concentrated.length > 0 && accountId == null && (
        <Card title="Concentration">
          <ul className="space-y-2">
            {concentrated.map((t) => (
              <li key={t.ticker} className="text-xs text-gray-600">
                <span className="font-medium text-gray-900">{t.ticker}</span> is{' '}
                {t.share.toFixed(1)}% of the portfolio, {money(t.value)} riding on one company.
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
