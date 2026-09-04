import { useCallback, useEffect, useState } from 'react'
import type { BenefitCard, BenefitRow } from '../api'
import { api, money } from '../api'
import { Card, Empty, Pill, Skeleton, Stat } from '../components/ui'

const CADENCES: [number, string][] = [
  [1, 'Monthly'],
  [3, 'Quarterly'],
  [6, 'Twice a year'],
  [12, 'Yearly'],
  [48, 'Every 4 years'],
]

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function urgency(days: number) {
  if (days <= 14) return 'text-rose-600'
  if (days <= 60) return 'text-amber-600'
  return 'text-gray-500'
}

export function Benefits() {
  const [cards, setCards] = useState<BenefitCard[]>([])
  const [left, setLeft] = useState(0)
  const [claimed, setClaimed] = useState(0)
  const [expiring, setExpiring] = useState<BenefitRow[]>([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState<number | null>(null)
  const [draft, setDraft] = useState({ name: '', value: '', period_months: 12, start_month: 1 })

  const load = useCallback(
    () =>
      api.cardBenefits().then((d) => {
        setCards(d.cards)
        setLeft(d.value_left)
        setClaimed(d.value_claimed)
        setExpiring(d.expiring)
        setLoading(false)
      }),
    [],
  )
  useEffect(() => {
    load()
  }, [load])

  const toggle = async (b: BenefitRow) => {
    await api.claimBenefit(b.id, !b.claimed)
    await load()
  }

  const remove = async (b: BenefitRow) => {
    await api.deleteBenefit(b.id)
    await load()
  }

  const submit = async (accountId: number) => {
    if (!draft.name.trim()) return
    await api.createBenefit({
      account_id: accountId,
      name: draft.name.trim(),
      value: draft.value ? Number(draft.value) : null,
      period_months: draft.period_months,
      start_month: draft.start_month,
    })
    setDraft({ name: '', value: '', period_months: 12, start_month: 1 })
    setAdding(null)
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

  if (!cards.length) {
    return (
      <Card title="Card benefits">
        <Empty
          title="No cards to track yet"
          hint="Connect a credit card on the Accounts tab and its perks will appear here."
        />
      </Card>
    )
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Still available" value={left} tone="brand" sub="in the cycles open now" />
        <Stat label="Used" value={claimed} tone="good" sub="ticked off this cycle" />
        <Stat
          label="Expiring within 60 days"
          value={expiring.reduce((n, b) => n + (b.value ?? 0), 0)}
          tone={expiring.length ? 'bad' : 'default'}
          sub={expiring.length ? `${expiring.length} to use` : 'nothing running out'}
        />
      </div>

      {expiring.length > 0 && (
        <div className="rounded-2xl bg-amber-50 px-5 py-4 ring-1 ring-amber-200/70">
          <p className="text-sm font-semibold text-amber-900">Running out soon</p>
          <ul className="mt-2 space-y-1">
            {expiring.map((b) => (
              <li key={b.id} className="text-xs text-amber-800">
                <span className="font-medium">{b.name}</span>
                {b.value ? ` · ${money(b.value)}` : ''} ·{' '}
                {b.days_left <= 0 ? 'expires today' : `${b.days_left} days left`}
              </li>
            ))}
          </ul>
        </div>
      )}

      {cards.map((card) => (
        <Card
          key={card.account_id}
          title={card.mask ? `${card.card} ····${card.mask}` : card.card}
          action={
            card.value_left > 0 ? (
              <span className="tnum text-xs text-gray-500">{money(card.value_left)} unused</span>
            ) : (
              <span className="text-xs text-emerald-600">all used</span>
            )
          }
        >
          <ul className="divide-y divide-gray-50">
            {card.benefits.map((b) => (
              <li key={b.id} className="group flex items-start gap-3 py-3">
                <input
                  type="checkbox"
                  checked={b.claimed}
                  onChange={() => toggle(b)}
                  className="mt-0.5 size-4 shrink-0 cursor-pointer rounded border-gray-300
                             text-brand-600 focus:ring-brand-500"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`text-sm font-medium ${
                        b.claimed ? 'text-gray-400 line-through' : 'text-gray-900'
                      }`}
                    >
                      {b.name}
                    </span>
                    {b.value != null && (
                      <span className="tnum text-sm font-semibold text-gray-700">
                        {money(b.value)}
                      </span>
                    )}
                    <Pill>{b.cadence}</Pill>
                  </div>
                  {b.detail && <p className="mt-0.5 text-xs text-gray-500">{b.detail}</p>}
                  <p className="mt-0.5 text-xs">
                    <span className="text-gray-400">{b.period_label}</span>
                    {!b.claimed && (
                      <span className={`ml-2 ${urgency(b.days_left)}`}>
                        {b.days_left <= 0 ? 'expired' : `${b.days_left} days left`}
                      </span>
                    )}
                    {b.claimed && b.claimed_on && (
                      <span className="ml-2 text-emerald-600">
                        used {new Date(b.claimed_on).toLocaleDateString()}
                      </span>
                    )}
                  </p>
                </div>
                <button
                  onClick={() => remove(b)}
                  title="Remove this benefit"
                  className="rounded-lg px-2 py-1 text-xs font-medium text-gray-400 opacity-0
                             transition hover:bg-gray-100 hover:text-rose-600
                             focus:opacity-100 group-hover:opacity-100"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>

          {adding === card.account_id ? (
            <div className="mt-3 space-y-2 rounded-xl bg-gray-50 p-3">
              <div className="flex flex-wrap gap-2">
                <input
                  autoFocus
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  placeholder="Benefit"
                  className="min-w-40 flex-1 rounded-lg border border-gray-200 px-3 py-1.5 text-sm"
                />
                <input
                  value={draft.value}
                  onChange={(e) => setDraft({ ...draft, value: e.target.value })}
                  placeholder="Value"
                  inputMode="decimal"
                  className="w-24 rounded-lg border border-gray-200 px-3 py-1.5 text-sm"
                />
                <select
                  value={draft.period_months}
                  onChange={(e) => setDraft({ ...draft, period_months: Number(e.target.value) })}
                  className="rounded-lg border border-gray-200 px-2 py-1.5 text-sm"
                >
                  {CADENCES.map(([v, label]) => (
                    <option key={v} value={v}>{label}</option>
                  ))}
                </select>
                {draft.period_months >= 12 && (
                  <select
                    value={draft.start_month}
                    onChange={(e) => setDraft({ ...draft, start_month: Number(e.target.value) })}
                    title="Month the cycle starts"
                    className="rounded-lg border border-gray-200 px-2 py-1.5 text-sm"
                  >
                    {MONTHS.map((m, i) => (
                      <option key={m} value={i + 1}>Resets {m}</option>
                    ))}
                  </select>
                )}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => submit(card.account_id)}
                  className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white
                             hover:bg-brand-700"
                >
                  Add
                </button>
                <button
                  onClick={() => setAdding(null)}
                  className="rounded-lg px-3 py-1.5 text-sm font-medium text-gray-500 hover:bg-gray-100"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setAdding(card.account_id)}
              className="mt-3 rounded-lg px-2 py-1 text-xs font-medium text-gray-500
                         hover:bg-gray-100 hover:text-gray-900"
            >
              + Add a benefit
            </button>
          )}
        </Card>
      ))}

      <p className="text-xs leading-relaxed text-gray-500">
        Starting list only — card terms change and depend on when the account was opened. Check
        each against your card's benefits page and edit anything that does not match.
      </p>
    </div>
  )
}
