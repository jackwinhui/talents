import { useEffect, useState } from 'react'
import type { CategoryRow2, TxnDetail } from '../api'
import { api, money } from '../api'

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4 py-2 text-sm">
      <dt className="w-28 shrink-0 text-gray-500">{label}</dt>
      <dd className="min-w-0 flex-1 text-gray-800">{children}</dd>
    </div>
  )
}

export function TransactionDrawer({
  id, categories, onClose, onSaved,
}: {
  id: number | null
  categories: CategoryRow2[]
  onClose: () => void
  onSaved: () => void
}) {
  const [txn, setTxn] = useState<TxnDetail | null>(null)
  const [categoryId, setCategoryId] = useState<number | null>(null)
  const [notes, setNotes] = useState('')
  const [isTransfer, setIsTransfer] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => {
    if (id == null) return
    setTxn(null)
    setMsg(null)
    api.transaction(id).then((t) => {
      setTxn(t)
      setCategoryId(t.category_id)
      setNotes(t.notes ?? '')
      setIsTransfer(t.is_transfer)
    })
  }, [id])

  // Escape should close, as with any modal surface.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (id == null) return null

  const dirty =
    txn != null &&
    (categoryId !== txn.category_id || notes !== (txn.notes ?? '') || isTransfer !== txn.is_transfer)

  const save = async () => {
    if (!txn) return
    setSaving(true)
    try {
      await api.updateTransaction(txn.id, {
        category_id: categoryId ?? undefined,
        notes,
        is_transfer: isTransfer,
      })
      onSaved()
      setMsg('Saved')
      setTimeout(onClose, 500)
    } catch (e) {
      setMsg(String(e))
    } finally {
      setSaving(false)
    }
  }

  const applyAll = async () => {
    if (!txn || categoryId == null) return
    setSaving(true)
    try {
      const r = await api.applyToSimilar(txn.id, categoryId)
      onSaved()
      setMsg(`Rule saved · ${r.updated} transaction${r.updated === 1 ? '' : 's'} updated`)
    } catch (e) {
      setMsg(String(e))
    } finally {
      setSaving(false)
    }
  }

  const expense = categories.filter((c) => c.kind === 'expense')
  const income = categories.filter((c) => c.kind === 'income')
  const other = categories.filter((c) => c.kind === 'transfer')

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/20 backdrop-blur-[1px]"
      />
      <aside className="relative flex h-full w-full max-w-md flex-col bg-white shadow-2xl">
        <header className="flex items-start gap-3 border-b border-gray-100 px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold text-gray-900">
              {txn?.merchant || txn?.description || 'Transaction'}
            </h2>
            {txn && (
              <p
                className={`tnum mt-0.5 text-xl font-semibold ${
                  txn.amount < 0 ? 'text-gray-900' : 'text-emerald-600'
                }`}
              >
                {money(txn.amount, true)}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            ✕
          </button>
        </header>

        {!txn ? (
          <div className="flex-1 p-5">
            <div className="h-40 animate-pulse rounded-xl bg-gray-100" />
          </div>
        ) : (
          <>
            <div className="flex-1 overflow-y-auto px-5 py-3">
              <dl className="divide-y divide-gray-50">
                <Row label="Date">{new Date(txn.date).toLocaleDateString()}</Row>
                <Row label="Account">{txn.account ?? '—'}</Row>
                <Row label="Source">
                  {txn.source}
                  {txn.pending && <span className="ml-2 text-amber-600">pending</span>}
                </Row>
                {txn.description && txn.description !== txn.merchant && (
                  <Row label="Raw text">
                    <span className="break-words text-xs text-gray-500">{txn.description}</span>
                  </Row>
                )}
              </dl>

              <div className="mt-4 space-y-4">
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-gray-600">
                    Category
                    {txn.is_manual_override && (
                      <span className="ml-2 font-normal text-brand-600">set by you</span>
                    )}
                  </label>
                  <select
                    value={categoryId ?? ''}
                    onChange={(e) => setCategoryId(Number(e.target.value))}
                    className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm
                               outline-none focus:border-brand-500 focus:ring-2
                               focus:ring-brand-500/15"
                  >
                    <optgroup label="Expense">
                      {expense.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </optgroup>
                    <optgroup label="Income">
                      {income.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </optgroup>
                    {other.length > 0 && (
                      <optgroup label="Other">
                        {other.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                      </optgroup>
                    )}
                  </select>
                  {txn.similar_count > 0 && (
                    <button
                      onClick={applyAll}
                      disabled={saving || categoryId == null}
                      className="mt-2 w-full rounded-lg border border-gray-200 px-3 py-2 text-xs
                                 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                    >
                      Apply to all {txn.similar_count + 1} transactions from this merchant
                    </button>
                  )}
                </div>

                <label className="flex items-start gap-2.5">
                  <input
                    type="checkbox"
                    checked={isTransfer}
                    onChange={(e) => setIsTransfer(e.target.checked)}
                    className="mt-0.5 size-4 rounded border-gray-300 text-brand-600
                               focus:ring-brand-500/30"
                  />
                  <span className="text-sm text-gray-700">
                    Money moved between my own accounts
                    <span className="block text-xs text-gray-500">
                      Excluded from spending, so a card payment is not counted twice.
                    </span>
                  </span>
                </label>

                <div>
                  <label className="mb-1.5 block text-xs font-medium text-gray-600">Notes</label>
                  <textarea
                    rows={3}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder="Optional"
                    className="w-full resize-none rounded-lg border border-gray-200 px-3 py-2
                               text-sm outline-none placeholder:text-gray-400
                               focus:border-brand-500 focus:ring-2 focus:ring-brand-500/15"
                  />
                </div>
              </div>
            </div>

            <footer className="flex items-center gap-3 border-t border-gray-100 px-5 py-3">
              {msg && <span className="flex-1 truncate text-xs text-gray-500">{msg}</span>}
              <button
                onClick={onClose}
                className="ml-auto rounded-lg border border-gray-200 px-3 py-1.5 text-sm
                           font-medium text-gray-700 hover:bg-gray-50"
              >
                Close
              </button>
              <button
                onClick={save}
                disabled={!dirty || saving}
                className="rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-medium text-white
                           hover:bg-brand-700 disabled:opacity-40"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </footer>
          </>
        )}
      </aside>
    </div>
  )
}
