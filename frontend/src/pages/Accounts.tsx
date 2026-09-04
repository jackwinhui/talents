import { useCallback, useEffect, useState } from 'react'
import type { AccountRow, InstitutionRow } from '../api'
import { api, money } from '../api'
import { CsvImport } from '../components/CsvImport'
import { PlaidSetup } from '../components/PlaidSetup'
import { Card, Empty, Pill, Skeleton } from '../components/ui'

declare global {
  interface Window { Plaid?: { create: (o: Record<string, unknown>) => { open: () => void } } }
}

const PLAID_JS = 'https://cdn.plaid.com/link/v2/stable/link-initialize.js'

function usePlaid() {
  const [ready, setReady] = useState(!!window.Plaid)
  useEffect(() => {
    if (window.Plaid) return setReady(true)
    const s = document.createElement('script')
    s.src = PLAID_JS
    s.onload = () => setReady(true)
    document.body.appendChild(s)
  }, [])
  return ready
}

export function Accounts() {
  const [rows, setRows] = useState<InstitutionRow[]>([])
  const [loading, setLoading] = useState(true)
  const [note, setNote] = useState<string | null>(null)
  const [plainAccounts, setPlainAccounts] = useState<AccountRow[]>([])
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [configured, setConfigured] = useState<boolean | null>(null)
  const plaidReady = usePlaid()

  const load = useCallback(
    () =>
      Promise.all([api.institutions(), api.accounts(), api.setupStatus()]).then(
        ([i, a, setup]) => {
          setRows(i)
          setPlainAccounts(a)
          setConfigured(setup.configured)
          setLoading(false)
        },
      ),
    [],
  )
  useEffect(() => { load() }, [load])

  const openLink = async (token: string, onDone: (t?: string) => Promise<void> | void) => {
    if (!window.Plaid) return setNote('Plaid Link is still loading — try again in a moment.')
    window.Plaid.create({
      token,
      onSuccess: async (public_token: string) => {
        setNote('Connecting…')
        await onDone(public_token)
        await load()
      },
      onExit: (err: unknown) => setNote(err ? 'Link closed before finishing.' : null),
    }).open()
  }

  const connect = async (kind: 'bank' | 'investments') => {
    setNote(null)
    try {
      const { link_token } = await api.linkToken(kind)
      await openLink(link_token, async (pt) => {
        if (!pt) return
        const r = await api.exchange(pt)
        setNote(`Connected ${r.institution} · ${r.accounts_added} accounts`)
      })
    } catch (e) { setNote(String(e)) }
  }

  // Update mode: re-authorizes the same Item, so it does not consume another Plaid
  // Trial slot. It cannot extend history — Plaid fixes that when the Item is made.
  const reconnect = async (inst: InstitutionRow) => {
    setNote(null)
    try {
      const { link_token } = await api.reconnect(inst.id)
      await openLink(link_token, async () => setNote(`${inst.name}: reconnected — run Sync.`))
    } catch (e) { setNote(String(e)) }
  }

  const saveName = async (id: number) => {
    await api.renameAccount(id, draft)
    setEditing(null)
    await load()
  }

  if (loading) {
    return (
      <Card title="Connected institutions">
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-20" />)}
        </div>
      </Card>
    )
  }

  // Nothing can be connected until Plaid knows who is asking, so the setup comes
  // first rather than sitting behind a button that would only fail.
  if (configured === false) {
    return (
      <div className="space-y-5">
        <PlaidSetup onDone={load} />
        {/* Statement import needs somewhere to import *into*, so it is only worth
            offering once at least one account exists. */}
        {plainAccounts.length > 0 && (
          <CsvImport accounts={plainAccounts} onImported={load} />
        )}
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <Card
        title="Connected institutions"
        action={
          <div className="flex gap-2">
            <button
              onClick={() => connect('bank')}
              disabled={!plaidReady}
              className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white
                         hover:bg-brand-700 disabled:opacity-50"
            >
              Add bank or card
            </button>
            <button
              onClick={() => connect('investments')}
              disabled={!plaidReady}
              className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm font-medium
                         text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Add brokerage
            </button>
          </div>
        }
      >
        {note && (
          <p className="mb-3 rounded-lg bg-brand-50 px-3 py-2 text-xs text-brand-700">{note}</p>
        )}

        {rows.length ? (
          <ul className="space-y-3">
            {rows.map((i) => (
              <li key={i.id} className="rounded-xl border border-gray-100 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-gray-900">{i.name}</span>
                  <Pill>{i.accounts.length} account{i.accounts.length === 1 ? '' : 's'}</Pill>
                  {i.last_error && (
                    <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700">
                      needs attention
                    </span>
                  )}
                  {i.linked ? (
                    <button
                      onClick={() => reconnect(i)}
                      title="Re-authorize this bank after a broken login or password change. Does not use a Plaid slot."
                      className="ml-auto rounded-lg border border-gray-200 px-2.5 py-1
                                 text-xs font-medium text-gray-600 hover:bg-gray-50"
                    >
                      Reconnect
                    </button>
                  ) : (
                    <Pill>imported</Pill>
                  )}
                </div>
                {i.history_days != null && (
                  <p className="mt-1.5 text-xs text-gray-500">
                    {i.history_days} days of history
                    {i.history_from && ` (from ${new Date(i.history_from).toLocaleDateString()})`}
                    <span className="ml-1">— fixed when the account was linked.</span>
                  </p>
                )}
                <ul className="mt-2 divide-y divide-gray-50">
                  {i.accounts.map((a) => (
                    <li key={a.id} className="flex items-center gap-2 py-1.5 text-sm">
                      {editing === a.id ? (
                        <>
                          <input
                            autoFocus
                            value={draft}
                            onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') saveName(a.id)
                              if (e.key === 'Escape') setEditing(null)
                            }}
                            className="flex-1 rounded-lg border border-gray-200 px-2 py-1 text-sm
                                       outline-none focus:border-brand-500 focus:ring-2
                                       focus:ring-brand-500/15"
                          />
                          <button
                            onClick={() => saveName(a.id)}
                            className="rounded-lg bg-brand-600 px-2.5 py-1 text-xs font-medium text-white"
                          >
                            Save
                          </button>
                          <button
                            onClick={() => setEditing(null)}
                            className="text-xs text-gray-400 hover:text-gray-700"
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <>
                          <span className="flex-1 truncate text-gray-700">
                            {a.name}
                            {a.mask && <span className="text-gray-400"> ····{a.mask}</span>}
                          </span>
                          <button
                            onClick={() => { setEditing(a.id); setDraft(a.name) }}
                            className="text-xs text-gray-400 hover:text-brand-600"
                          >
                            Rename
                          </button>
                          <span className="tnum w-24 text-right text-gray-900">
                            {money(a.balance, true)}
                          </span>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="Nothing connected yet" hint="Add a bank or card to get started." />
        )}
      </Card>

      <CsvImport accounts={plainAccounts} onImported={load} />

      <Card title="Before you connect more">
        <ul className="space-y-2 text-xs leading-relaxed text-gray-600">
          <li>
            <strong className="text-gray-800">10 connections, permanently.</strong> Removing one
            does not give the slot back. One login covers every account at that bank.
          </li>
          <li>
            <strong className="text-gray-800">History is fixed at link time.</strong> It cannot be
            extended later. Reconnect fixes a broken login but adds no history.
          </li>
          <li>
            <strong className="text-gray-800">Everything stays on this Mac.</strong> Access tokens
            are encrypted with a key held in the macOS Keychain.
          </li>
        </ul>
      </Card>
    </div>
  )
}
