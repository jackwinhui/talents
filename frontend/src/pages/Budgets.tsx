import { useEffect, useState } from 'react'
import type { BudgetRow, CategoryRow2 } from '../api'
import { api, money } from '../api'
import { Card, Empty, Skeleton } from '../components/ui'

export function Budgets() {
  const [rows, setRows] = useState<BudgetRow[]>([])
  const [cats, setCats] = useState<CategoryRow2[]>([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState({ category_id: 0, amount: '' })

  const load = () =>
    Promise.all([api.budgets(), api.categories()]).then(([b, c]) => {
      setRows(b)
      setCats(c.filter((x) => x.kind === 'expense'))
      setLoading(false)
    })

  useEffect(() => {
    load()
  }, [])

  const save = async (category_id: number, amount: number) => {
    await api.saveBudget(category_id, amount)
    await load()
  }

  const totalBudget = rows.reduce((s, r) => s + r.amount, 0)
  const totalSpent = rows.reduce((s, r) => s + r.spent, 0)
  const unbudgeted = cats.filter((c) => !rows.some((r) => r.category_id === c.id))

  if (loading) {
    return (
      <Card title="Budgets">
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12" />)}
        </div>
      </Card>
    )
  }

  return (
    <div className="space-y-5">
      <Card
        title="Monthly budgets"
        action={
          totalBudget > 0 ? (
            <span className="tnum text-xs text-gray-500">
              {money(totalSpent)} of {money(totalBudget)}
            </span>
          ) : undefined
        }
      >
        {rows.length ? (
          <ul className="space-y-4">
            {rows
              .slice()
              .sort((a, b) => b.spent / (b.amount || 1) - a.spent / (a.amount || 1))
              .map((r) => {
                const pct = r.amount ? Math.min(200, (r.spent / r.amount) * 100) : 0
                const over = r.spent > r.amount
                return (
                  <li key={r.id}>
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-sm font-medium text-gray-800">{r.category}</span>
                      <span className="tnum text-xs text-gray-500">
                        <span className={over ? 'font-semibold text-rose-600' : 'text-gray-700'}>
                          {money(r.spent)}
                        </span>{' '}
                        / {money(r.amount)}
                      </span>
                    </div>
                    <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-gray-100">
                      <div
                        className={`h-full rounded-full transition-all ${
                          over ? 'bg-rose-500' : pct > 80 ? 'bg-amber-500' : 'bg-brand-500'
                        }`}
                        style={{ width: `${Math.min(100, pct)}%` }}
                      />
                    </div>
                    <div className="mt-1 flex items-center justify-between">
                      <span className="text-[11px] text-gray-400">
                        {over
                          ? `${money(r.spent - r.amount)} over`
                          : `${money(r.amount - r.spent)} left`}
                      </span>
                      <button
                        onClick={() => save(r.category_id, 0)}
                        className="text-[11px] text-gray-400 hover:text-rose-600"
                      >
                        Remove
                      </button>
                    </div>
                  </li>
                )
              })}
          </ul>
        ) : (
          <Empty
            title="No budgets yet"
            hint="Add one below to start tracking a category against a monthly limit."
          />
        )}
      </Card>

      <Card title="Add a budget">
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={adding.category_id}
            onChange={(e) => setAdding({ ...adding, category_id: Number(e.target.value) })}
            className="rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none
                       focus:border-brand-500 focus:ring-2 focus:ring-brand-500/15"
          >
            <option value={0}>Choose a category…</option>
            {unbudgeted.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <input
            type="number"
            min="0"
            placeholder="Monthly limit"
            value={adding.amount}
            onChange={(e) => setAdding({ ...adding, amount: e.target.value })}
            className="w-40 rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none
                       focus:border-brand-500 focus:ring-2 focus:ring-brand-500/15"
          />
          <button
            disabled={!adding.category_id || !Number(adding.amount)}
            onClick={async () => {
              await save(adding.category_id, Number(adding.amount))
              setAdding({ category_id: 0, amount: '' })
            }}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white
                       hover:bg-brand-700 disabled:opacity-40"
          >
            Add
          </button>
        </div>
      </Card>
    </div>
  )
}
