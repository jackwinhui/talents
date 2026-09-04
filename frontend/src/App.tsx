import { useCallback, useEffect, useState } from 'react'
import type { AccountRow, Insight, RecurringRow, Summary } from './api'
import { api } from './api'
import { Accounts } from './pages/Accounts'
import { Benefits } from './pages/Benefits'
import { Bills } from './pages/Bills'
import { Budgets } from './pages/Budgets'
import { Debts } from './pages/Debts'
import { Dashboard } from './pages/Dashboard'
import { Investments } from './pages/Investments'
import { Transactions } from './pages/Transactions'

type Tab =
  | 'dashboard' | 'transactions' | 'bills' | 'budgets'
  | 'debts' | 'investments' | 'benefits' | 'accounts'

const TABS: [Tab, string][] = [
  ['dashboard', 'Dashboard'],
  ['transactions', 'Transactions'],
  ['bills', 'Bills'],
  ['budgets', 'Budgets'],
  ['debts', 'Debts'],
  ['investments', 'Investments'],
  ['benefits', 'Benefits'],
  ['accounts', 'Accounts'],
]

export default function App() {
  const [tab, setTab] = useState<Tab>('dashboard')
  const [accounts, setAccounts] = useState<AccountRow[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [insights, setInsights] = useState<Insight[]>([])
  const [bills, setBills] = useState<RecurringRow[]>([])
  const [period, setPeriod] = useState('current')
  const [syncing, setSyncing] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const load = useCallback(async () => {
    const [a, s, i, r] = await Promise.all([
      api.accounts(), api.summary(period), api.insights(), api.recurring(),
    ])
    setAccounts(a)
    setSummary(s)
    setInsights(i)
    setBills(r.upcoming)
  }, [period])

  useEffect(() => {
    load().catch((e) => setNote(String(e)))
  }, [load])

  const sync = async () => {
    setSyncing(true)
    setNote(null)
    try {
      await api.sync()
      await api.detectRecurring()
      await load()
      setNote('Synced')
      setTimeout(() => setNote(null), 2500)
    } catch (e) {
      setNote(String(e))
    } finally {
      setSyncing(false)
    }
  }

  const lastSynced = accounts.find((a) => a.last_synced_at)?.last_synced_at

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-10 border-b border-black/5 bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-6 py-3">
          <img src="/assets/icon-180.png" alt="" className="size-9 rounded-full" />
          <div className="mr-auto">
            <h1 className="text-[15px] font-semibold leading-tight text-gray-900">Talents</h1>
            <p className="text-[11px] leading-tight text-gray-500">
              Faithful with a few things · Matthew 25:21
            </p>
          </div>

          <nav className="hidden gap-1 rounded-xl bg-gray-100 p-1 lg:flex">
            {TABS.map(([id, label]) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                  tab === id
                    ? 'bg-white text-gray-900 shadow-sm'
                    : 'text-gray-600 hover:text-gray-900'
                }`}
              >
                {label}
              </button>
            ))}
          </nav>

          {note && <span className="hidden text-xs text-gray-500 sm:block">{note}</span>}

          <button
            onClick={sync}
            disabled={syncing}
            className="rounded-lg bg-brand-600 px-3.5 py-1.5 text-sm font-medium text-white
                       shadow-sm transition hover:bg-brand-700 disabled:opacity-60"
          >
            {syncing ? 'Syncing…' : 'Sync'}
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-6">
        <nav className="mb-4 flex gap-1 overflow-x-auto rounded-xl bg-gray-100 p-1 lg:hidden">
          {TABS.map(([id, label]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`flex-1 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium ${
                tab === id ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600'
              }`}
            >
              {label}
            </button>
          ))}
        </nav>

        {tab === 'dashboard' && (
          <Dashboard
            summary={summary} accounts={accounts}
            insights={insights} bills={bills}
            period={period} onPeriodChange={setPeriod}
            onConnect={() => setTab('accounts')}
          />
        )}
        {tab === 'transactions' && <Transactions onChanged={load} />}
        {tab === 'bills' && <Bills />}
        {tab === 'budgets' && <Budgets />}
        {tab === 'debts' && <Debts />}
        {tab === 'investments' && <Investments />}
        {tab === 'benefits' && <Benefits />}
        {tab === 'accounts' && <Accounts />}

        <p className="mt-8 text-center text-[11px] text-gray-400">
          Runs entirely on your Mac
          {lastSynced ? ` · last synced ${new Date(lastSynced).toLocaleString()}` : ''}
        </p>
      </main>
    </div>
  )
}
