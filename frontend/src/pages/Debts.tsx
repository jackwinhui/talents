import { useCallback, useEffect, useState } from 'react'
import type { DebtRow, DebtsData } from '../api'
import { api, money } from '../api'
import { Card, Empty, Skeleton, Stat } from '../components/ui'

const fmtDate = (iso: string) => new Date(iso + 'T00:00:00').toLocaleDateString()

const fmtMonth = (iso: string) =>
  new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'long', year: 'numeric' })

export function Debts() {
  const [data, setData] = useState<DebtsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<number | null>(null)

  const load = useCallback(
    () =>
      api.debts().then((d) => {
        setData(d)
        setLoading(false)
      }),
    [],
  )
  useEffect(() => {
    load()
  }, [load])

  const allocate = async (txnId: number, debtId: number) => {
    await api.allocateToDebt(txnId, debtId)
    await load()
  }

  const ignore = async (txnId: number) => {
    await api.ignoreForDebts(txnId)
    await load()
  }

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

  if (!data || !data.debts.length) {
    return (
      <Card title="Debts">
        <Empty title="Nothing being paid off" hint="Add a debt to track it here." />
      </Card>
    )
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Still owed" value={data.total_remaining} tone="bad" />
        <Stat
          label="Paid so far"
          value={data.total_paid}
          tone="good"
          sub={
            data.total_interest
              ? `${money(data.total_interest)} of it interest`
              : undefined
          }
        />
        <Stat
          label="Committed monthly"
          value={data.monthly_committed}
          sub="only what is currently being paid"
        />
      </div>

      {data.debts.map((d: DebtRow) => (
        <Card
          key={d.id}
          title={d.name}
          sub={d.payee ? `to ${d.payee}` : undefined}
          action={
            <span className="tnum text-xs text-gray-500">
              {money(d.monthly)}/mo{d.rate != null && ` · ${d.rate}%`}
            </span>
          }
        >
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <span className="tnum text-2xl font-semibold text-gray-900">
              {money(d.remaining)}
            </span>
            <span className="text-sm text-gray-500">
              left of {money(d.total)}
            </span>
          </div>

          <div className="mt-3 h-2 overflow-hidden rounded-full bg-gray-100">
            <div
              className="h-full rounded-full bg-brand-500 transition-all"
              style={{ width: `${Math.min(d.percent, 100)}%` }}
            />
          </div>
          <div className="mt-1.5 flex flex-wrap justify-between gap-x-4 text-xs text-gray-500">
            <span>
              {money(d.paid)} paid · {d.payments_made} payment{d.payments_made === 1 ? '' : 's'} ·{' '}
              {d.percent}%
            </span>
            <span>
              {d.payments_left == null
                ? `${money(d.monthly)} does not cover the interest`
                : `${d.payments_left} left at ${money(d.monthly)}`}
            </span>
          </div>

          {d.rate != null && (
            <p className="mt-3 rounded-lg bg-gray-50 px-3 py-2 text-xs leading-relaxed text-gray-600">
              At {d.rate}% interest, {money(d.paid)} paid has taken {money(d.principal_paid)} off
              the total — the other {money(d.interest_paid)} was interest. Carrying on as you are
              costs a further {money(d.interest_left)}.
            </p>
          )}

          {d.scenarios.length > 1 && (
            <div className="mt-3">
              <p className="text-xs font-medium text-gray-500">Paying more each month</p>
              <table className="mt-1.5 w-full text-xs">
                <thead>
                  <tr className="text-gray-400">
                    <th className="py-1 text-left font-medium">Monthly</th>
                    <th className="py-1 text-right font-medium">Paid off</th>
                    <th className="py-1 text-right font-medium">Sooner</th>
                    <th className="py-1 text-right font-medium">Interest saved</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {d.scenarios.map((s) => (
                    <tr key={s.extra} className={s.extra === 0 ? 'text-gray-500' : 'text-gray-800'}>
                      <td className="tnum py-1">
                        {money(s.monthly)}
                        {s.extra > 0 && (
                          <span className="text-gray-400"> (+{money(s.extra)})</span>
                        )}
                      </td>
                      <td className="tnum py-1 text-right">{fmtMonth(s.payoff)}</td>
                      <td className="tnum py-1 text-right">
                        {s.months_earlier ? `${(s.months_earlier / 12).toFixed(1)} yrs` : '—'}
                      </td>
                      <td className="tnum py-1 text-right font-medium">
                        {s.saved ? money(s.saved) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <dl className="mt-4 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            <div className="flex gap-2">
              <dt className="text-gray-500">Last paid</dt>
              <dd className="text-gray-800">
                {d.last_paid_on ? fmtDate(d.last_paid_on) : '—'}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-gray-500">Paid off</dt>
              <dd className="text-gray-800">
                {d.projected_payoff
                  ? fmtMonth(d.projected_payoff)
                  : d.payments_left == null
                    ? 'never at this rate'
                    : 'not while paused'}
              </dd>
            </div>
          </dl>

          {!d.paying && d.months_since_last != null && d.payments_left != null && (
            <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
              Nothing paid for {d.months_since_last} months. At {money(d.monthly)} a month the
              remaining {money(d.remaining)} would take {d.payments_left} more payments once
              they restart.
            </p>
          )}

          <button
            onClick={() => setOpen(open === d.id ? null : d.id)}
            className="mt-3 rounded-lg px-2 py-1 text-xs font-medium text-gray-500
                       hover:bg-gray-100 hover:text-gray-900"
          >
            {open === d.id ? 'Hide payments' : `Show ${d.entries} payments`}
          </button>

          {open === d.id && (
            <ul className="mt-2 max-h-72 divide-y divide-gray-50 overflow-y-auto">
              {d.payments.map((p) => (
                <li key={p.id} className="flex items-center gap-3 py-1.5 text-sm">
                  <span className="w-24 shrink-0 text-gray-600">{fmtDate(p.paid_on)}</span>
                  <span className="tnum w-24 text-right font-medium text-gray-900">
                    {money(p.amount)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs text-gray-400">
                    {p.note ?? (p.linked ? '' : 'entered by hand')}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ))}

      {data.unallocated.length > 0 && (
        <Card title="Not counted towards a debt">
          <ul className="divide-y divide-gray-50">
            {data.unallocated.map((u) => (
              <li key={u.id} className="flex flex-wrap items-center gap-3 py-2.5 text-sm">
                <span className="w-24 shrink-0 text-gray-600">{fmtDate(u.date)}</span>
                <span className="tnum w-24 text-right font-medium text-gray-900">
                  {money(u.amount)}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs text-gray-500">{u.merchant}</span>
                <span className="flex gap-1">
                  {data.debts.map((d) => (
                    <button
                      key={d.id}
                      onClick={() => allocate(u.id, d.id)}
                      className="rounded-lg border border-gray-200 px-2 py-1 text-xs
                                 font-medium text-gray-600 hover:bg-gray-50"
                    >
                      → {d.name.split(',')[0].split(' ').slice(0, 2).join(' ')}
                    </button>
                  ))}
                  <button
                    onClick={() => ignore(u.id)}
                    className="rounded-lg px-2 py-1 text-xs font-medium text-gray-400
                               hover:bg-gray-100 hover:text-gray-700"
                  >
                    Ignore
                  </button>
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-gray-500">
            Payments that do not divide into whole installments. They still count as spending.
          </p>
        </Card>
      )}
    </div>
  )
}
