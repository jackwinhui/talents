import { useEffect, useState } from 'react'
import type { Outstanding, RecurringRow } from '../api'
import { api, money } from '../api'
import { Card, Empty, Pill, Skeleton } from '../components/ui'

// Below this the pattern is more likely coincidence than a real bill, so those are
// shown separately rather than mixed into the confirmed list.
const CONFIDENT = 0.7

export function Bills() {
  const [upcoming, setUpcoming] = useState<RecurringRow[]>([])
  const [outstanding, setOutstanding] = useState<Outstanding[]>([])
  const [rejected, setRejected] = useState<RecurringRow[]>([])
  const [canceled, setCanceled] = useState<RecurringRow[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)

  const load = () =>
    api.recurring().then((d) => {
      setUpcoming(d.upcoming)
      setOutstanding(d.outstanding)
      setRejected(d.rejected)
      setCanceled(d.canceled)
      setTotal(d.monthly_total)
      setLoading(false)
    })

  useEffect(() => {
    load()
  }, [])

  if (loading) {
    return (
      <Card title="Recurring bills">
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-12" />)}
        </div>
      </Card>
    )
  }

  const confirmed = upcoming.filter((r) => r.confidence >= CONFIDENT)
  const possible = upcoming.filter((r) => r.confidence < CONFIDENT)
  const days = (iso: string | null) =>
    iso ? Math.round((new Date(iso).getTime() - Date.now()) / 86_400_000) : null

  const setStatus = async (id: number, status: 'active' | 'rejected' | 'canceled') => {
    await api.setRecurringStatus(id, status)
    await load()
  }

  const row = (r: RecurringRow) => {
    const d = days(r.next_due)
    return (
      <li key={r.id} className="group flex items-center gap-3 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-gray-800">{r.name}</p>
          <p className="text-xs text-gray-500">
            {r.cadence}
            {r.next_due && ` · due ${new Date(r.next_due).toLocaleDateString()}`}
          </p>
        </div>
        {d != null && (
          <Pill>{d <= 0 ? 'due now' : d === 1 ? 'tomorrow' : `in ${d} days`}</Pill>
        )}
        <span className="tnum w-24 text-right text-sm font-semibold text-gray-900">
          {money(r.amount, true)}
        </span>
        <button
          onClick={() => setStatus(r.id, 'canceled')}
          title="This was a real bill, but I no longer pay it"
          className="rounded-lg px-2 py-1 text-xs font-medium text-gray-400 opacity-0
                     transition hover:bg-gray-100 hover:text-gray-700
                     focus:opacity-100 group-hover:opacity-100"
        >
          Canceled
        </button>
        <button
          onClick={() => setStatus(r.id, 'rejected')}
          title="This is not a recurring bill"
          className="rounded-lg px-2 py-1 text-xs font-medium text-gray-400 opacity-0
                     transition hover:bg-gray-100 hover:text-rose-600
                     focus:opacity-100 group-hover:opacity-100"
        >
          Not a bill
        </button>
      </li>
    )
  }

  return (
    <div className="space-y-5">
      {outstanding.length > 0 && (
        <Card title="Expected but not yet paid">
          <ul className="divide-y divide-gray-100">
            {outstanding.map((o) => (
              <li key={o.id} className="flex items-center gap-3 py-3">
                <span className="size-2 shrink-0 rounded-full bg-amber-500" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-gray-800">{o.name}</p>
                  <p className="text-xs text-gray-500">
                    {o.period}
                    {o.expected_date && ` · expected ${new Date(o.expected_date).toLocaleDateString()}`}
                  </p>
                </div>
                <span className="tnum text-sm font-semibold text-amber-700">
                  {money(o.amount, true)}
                </span>
                {o.series_id != null && (
                  <button
                    onClick={() => setStatus(o.series_id!, 'canceled')}
                    title="I no longer pay this bill"
                    className="rounded-lg px-2 py-1 text-xs font-medium text-gray-400
                               transition hover:bg-gray-100 hover:text-gray-700"
                  >
                    No longer pay
                  </button>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-gray-500">
Owed but not yet paid, so not counted as spending.
          </p>
        </Card>
      )}

      <Card
        title="Recurring bills"
        action={
          <span className="tnum text-xs text-gray-500">{money(total)}/mo committed</span>
        }
      >
        {confirmed.length ? (
          <ul className="divide-y divide-gray-100">{confirmed.map(row)}</ul>
        ) : (
          <Empty
            title="No recurring bills detected yet"
            hint="These appear once a steady pattern of payments builds up."
          />
        )}
      </Card>

      {possible.length > 0 && (
        <Card title="Possibly recurring">
          <ul className="divide-y divide-gray-100">{possible.map(row)}</ul>
          <p className="mt-3 text-xs text-gray-500">
Detected from an irregular pattern, so not counted as bills.
          </p>
        </Card>
      )}

      {canceled.length > 0 && (
        <Card title="No longer paying">
          <ul className="divide-y divide-gray-100">
            {canceled.map((r) => (
              <li key={r.id} className="flex items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-gray-500">{r.name}</p>
                  {r.last_seen && (
                    <p className="text-xs text-gray-400">
                      last paid {new Date(r.last_seen).toLocaleDateString()}
                    </p>
                  )}
                </div>
                <span className="tnum text-sm text-gray-400">{money(r.amount, true)}</span>
                <button
                  onClick={() => setStatus(r.id, 'active')}
                  className="rounded-lg px-2 py-1 text-xs font-medium text-gray-400
                             hover:bg-gray-100 hover:text-brand-600"
                >
                  Restore
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-gray-500">
Past payments stay in your history. Restored automatically if you pay one again.
          </p>
        </Card>
      )}

      {rejected.length > 0 && (
        <Card title="Not bills">
          <ul className="divide-y divide-gray-100">
            {rejected.map((r) => (
              <li key={r.id} className="flex items-center gap-3 py-2.5">
                <span className="min-w-0 flex-1 truncate text-sm text-gray-500">{r.name}</span>
                <span className="tnum text-sm text-gray-400">{money(r.amount, true)}</span>
                <button
                  onClick={() => setStatus(r.id, 'active')}
                  className="rounded-lg px-2 py-1 text-xs font-medium text-gray-400
                             hover:bg-gray-100 hover:text-brand-600"
                >
                  Restore
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
