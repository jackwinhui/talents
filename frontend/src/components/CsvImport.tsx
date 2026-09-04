import { useRef, useState } from 'react'
import type { AccountRow } from '../api'
import { api } from '../api'
import { Card } from './ui'

/** Statement import: the fallback for anything Plaid cannot reach. */
export function CsvImport({
  accounts, onImported,
}: { accounts: AccountRow[]; onImported: () => void }) {
  const [account, setAccount] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const run = async (file: File) => {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      // Always preview first. An import that silently doubled a year of history
      // would be tedious to unpick, so nothing is written until it is confirmed.
      const preview = await api.importCsv(file, account, true)
      if (preview.added === 0) {
        setResult(
          `Nothing new in this file — ${preview.skipped_duplicates} transactions are already here.`,
        )
        return
      }
      const ok = window.confirm(
        `${file.name}\n\nDetected: ${preview.profile} → ${preview.account}\n` +
          `${preview.added} new, ${preview.skipped_duplicates} already imported` +
          `${preview.unparsed ? `, ${preview.unparsed} unreadable` : ''}\n\nImport them?`,
      )
      if (!ok) return
      const done = await api.importCsv(file, account, false)
      setResult(
        `Imported ${done.added} transactions into ${done.account} ` +
          `(${done.skipped_duplicates} already present).`,
      )
      onImported()
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e))
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  return (
    <Card title="Import a statement">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          const file = e.dataTransfer.files?.[0]
          if (file) run(file)
        }}
        onClick={() => input.current?.click()}
        className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl
                    border-2 border-dashed px-4 py-8 text-center transition ${
                      dragging
                        ? 'border-brand-500 bg-brand-50/60'
                        : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50/60'
                    }`}
      >
        <p className="text-sm font-medium text-gray-700">
          {busy ? 'Reading…' : 'Drop a CSV here, or click to choose'}
        </p>
        <p className="text-xs text-gray-500">
          Chase, Capital One, Citi/Costco and Bilt exports are recognized automatically
        </p>
        <input
          ref={input}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) run(file)
          }}
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label className="text-xs text-gray-500">Account</label>
        <select
          value={account}
          onChange={(e) => setAccount(e.target.value)}
          className="rounded-lg border border-gray-200 px-2 py-1.5 text-sm outline-none
                     focus:border-brand-500 focus:ring-2 focus:ring-brand-500/15"
        >
          <option value="">Detect from the file</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.name}>{a.name}</option>
          ))}
        </select>
        <span className="text-xs text-gray-400">
          Chase card exports do not name the card, so pick one for those
        </span>
      </div>

      {result && (
        <p className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800">{result}</p>
      )}
      {error && (
        <p className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</p>
      )}
    </Card>
  )
}
