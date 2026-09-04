import { useEffect, useMemo, useRef, useState } from 'react'
import type { AccountRow, CategoryRow2, TxnRow } from '../api'
import { api, money, monthLabel } from '../api'
import { TransactionDrawer } from '../components/TransactionDrawer'
import { Card, Empty, Pill, Skeleton } from '../components/ui'

const PAGE = 50

export function Transactions({ onChanged }: { onChanged?: () => void }) {
  const [rows, setRows] = useState<TxnRow[]>([])
  const [total, setTotal] = useState(0)
  const [sums, setSums] = useState({ out: 0, in: 0 })
  const [page, setPage] = useState(0)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [accountId, setAccountId] = useState('')
  const [accounts, setAccounts] = useState<AccountRow[]>([])
  const [month, setMonth] = useState('')
  const [months, setMonths] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<number | null>(null)
  const [cats, setCats] = useState<CategoryRow2[]>([])
  const [reloadKey, setReloadKey] = useState(0)

  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [bulkCat, setBulkCat] = useState('')
  const [busy, setBusy] = useState(false)
  // Anchor for shift-click range selection.
  const lastClicked = useRef<number | null>(null)

  useEffect(() => {
    api.categories().then(setCats)
    api.accounts().then(setAccounts)
    api.transactionMonths().then(setMonths)
  }, [])

  // Debounced so typing does not fire a request per keystroke.
  const [debounced, setDebounced] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 250)
    return () => clearTimeout(t)
  }, [search])

  useEffect(() => setPage(0), [debounced, category, accountId, month])

  useEffect(() => {
    let canceled = false
    setLoading(true)
    api
      .transactions({
        limit: PAGE, offset: page * PAGE, search: debounced, category,
        account_id: accountId, month,
      })
      .then((d) => {
        if (canceled) return
        setRows(d.items)
        setTotal(d.total)
        setSums({ out: d.sum_out, in: d.sum_in })
      })
      .finally(() => !canceled && setLoading(false))
    return () => {
      canceled = true
    }
  }, [page, debounced, category, accountId, month, reloadKey])

  // Changing the visible set makes any prior selection meaningless.
  useEffect(() => {
    setPicked(new Set())
    lastClicked.current = null
  }, [page, debounced, category, accountId, month])

  const categories = useMemo(
    () => [...new Set(rows.map((r) => r.category).filter(Boolean))].sort() as string[],
    [rows],
  )
  const pages = Math.max(1, Math.ceil(total / PAGE))
  const allPicked = rows.length > 0 && picked.size === rows.length

  const toggle = (id: number, index: number, shiftKey: boolean) => {
    setPicked((prev) => {
      const next = new Set(prev)
      if (shiftKey && lastClicked.current != null) {
        const from = rows.findIndex((r) => r.id === lastClicked.current)
        if (from !== -1) {
          const [a, b] = from < index ? [from, index] : [index, from]
          const turningOn = !prev.has(id)
          for (let i = a; i <= b; i++) {
            if (turningOn) next.add(rows[i].id)
            else next.delete(rows[i].id)
          }
          return next
        }
      }
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
    lastClicked.current = id
  }

  const applyBulk = async (body: { category_id?: number; is_transfer?: boolean }) => {
    setBusy(true)
    try {
      await api.bulkUpdate([...picked], body)
      setPicked(new Set())
      setBulkCat('')
      setReloadKey((k) => k + 1)
      onChanged?.()
    } finally {
      setBusy(false)
    }
  }

  // Only worth showing once something is narrowed down: the sum of every
  // transaction ever is not a number anyone needs.
  const isFiltered = Boolean(debounced || category || accountId || month)

  return (
    <Card
      title={`Transactions${total ? ` · ${total.toLocaleString()}` : ''}`}
      sub={
        isFiltered && total > 0 ? (
          <span className="tnum">
            {sums.out > 0 && <>{money(sums.out)} out</>}
            {sums.out > 0 && sums.in > 0 && ' · '}
            {sums.in > 0 && <>{money(sums.in)} in</>}
            {sums.out > 0 && sums.in > 0 && (
              <> · net {money(sums.in - sums.out, true)}</>
            )}
          </span>
        ) : undefined
      }
      action={
        <div className="flex items-center gap-2">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search merchant…"
            className="w-48 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm
                       outline-none placeholder:text-gray-400 focus:border-brand-500
                       focus:ring-2 focus:ring-brand-500/15"
          />
          <select
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm
                       outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/15"
          >
            <option value="">All months</option>
            {months.map((m) => (
              <option key={m} value={m}>{monthLabel(m)}</option>
            ))}
          </select>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm
                       outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/15"
          >
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <select
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm
                       outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/15"
          >
            <option value="">All accounts</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}{a.mask ? ` ····${a.mask}` : ''}
              </option>
            ))}
          </select>
        </div>
      }
    >
      {loading ? (
        <div className="space-y-2 py-2">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
        </div>
      ) : rows.length ? (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-left text-xs uppercase tracking-wide text-gray-500">
                  <th className="w-9 py-2">
                    <input
                      type="checkbox"
                      checked={allPicked}
                      onChange={() =>
                        setPicked(allPicked ? new Set() : new Set(rows.map((r) => r.id)))
                      }
                      className="size-4 rounded border-gray-300 text-brand-600
                                 focus:ring-brand-500/30"
                    />
                  </th>
                  <th className="py-2 pr-3 font-medium">Date</th>
                  <th className="py-2 pr-3 font-medium">Merchant</th>
                  <th className="py-2 pr-3 font-medium">Category</th>
                  <th className="py-2 pr-3 font-medium">Account</th>
                  <th className="py-2 pl-3 text-right font-medium">Amount</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {rows.map((t, i) => {
                  const isPicked = picked.has(t.id)
                  return (
                    <tr
                      key={t.id}
                      onClick={() => setSelected(t.id)}
                      className={`cursor-pointer ${isPicked ? 'bg-brand-50/60' : 'hover:bg-gray-50/70'}`}
                    >
                      <td className="py-2.5" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={isPicked}
                          onChange={() => undefined}
                          onClick={(e) => toggle(t.id, i, e.shiftKey)}
                          className="size-4 rounded border-gray-300 text-brand-600
                                     focus:ring-brand-500/30"
                        />
                      </td>
                      <td className="tnum whitespace-nowrap py-2.5 pr-3 text-gray-500">{t.date}</td>
                      <td className="max-w-[22rem] truncate py-2.5 pr-3 font-medium text-gray-800">
                        {t.merchant || t.description}
                        {t.pending && <span className="ml-2"><Pill>pending</Pill></span>}
                      </td>
                      <td className="py-2.5 pr-3"><Pill color={t.category_color}>{t.category ?? 'Uncategorized'}</Pill></td>
                      <td className="max-w-[12rem] truncate py-2.5 pr-3 text-gray-500">{t.account}</td>
                      <td
                        className={`tnum whitespace-nowrap py-2.5 pl-3 text-right font-semibold ${
                          t.amount < 0 ? 'text-gray-900' : 'text-emerald-600'
                        }`}
                      >
                        {money(t.amount, true)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex items-center justify-between text-xs text-gray-500">
            <span>
              Page {page + 1} of {pages}
              <span className="ml-2 text-gray-400">Tip: shift-click to select a range</span>
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="rounded-lg border border-gray-200 px-3 py-1.5 font-medium
                           enabled:hover:bg-gray-50 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
                disabled={page >= pages - 1}
                className="rounded-lg border border-gray-200 px-3 py-1.5 font-medium
                           enabled:hover:bg-gray-50 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        </>
      ) : (
        <Empty title="No transactions match" hint="Try clearing the search or category filter." />
      )}

      {picked.size > 0 && (
        <div className="fixed inset-x-0 bottom-6 z-40 flex justify-center px-6">
          <div className="flex flex-wrap items-center gap-3 rounded-2xl bg-gray-900 px-4 py-3
                          text-white shadow-xl">
            <span className="text-sm font-medium">
              {picked.size} selected
            </span>
            <select
              value={bulkCat}
              onChange={(e) => setBulkCat(e.target.value)}
              className="rounded-lg bg-white/10 px-2.5 py-1.5 text-sm outline-none
                         ring-1 ring-white/20 focus:ring-white/40"
            >
              <option value="" className="text-gray-900">Set category…</option>
              {cats.map((c) => (
                <option key={c.id} value={c.id} className="text-gray-900">{c.name}</option>
              ))}
            </select>
            <button
              disabled={!bulkCat || busy}
              onClick={() => applyBulk({ category_id: Number(bulkCat) })}
              className="rounded-lg bg-white px-3 py-1.5 text-sm font-medium text-gray-900
                         hover:bg-gray-100 disabled:opacity-40"
            >
              {busy ? 'Applying…' : 'Apply'}
            </button>
            <button
              disabled={busy}
              onClick={() => applyBulk({ is_transfer: true })}
              className="rounded-lg px-3 py-1.5 text-sm font-medium text-white/80
                         ring-1 ring-white/20 hover:bg-white/10 disabled:opacity-40"
            >
              Mark as transfer
            </button>
            <button
              onClick={() => setPicked(new Set())}
              className="text-sm text-white/60 hover:text-white"
            >
              Clear
            </button>
          </div>
        </div>
      )}

      <TransactionDrawer
        id={selected}
        categories={cats}
        onClose={() => setSelected(null)}
        onSaved={() => {
          setReloadKey((k) => k + 1)
          onChanged?.()
        }}
      />
    </Card>
  )
}
