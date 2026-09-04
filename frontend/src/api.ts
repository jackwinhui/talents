export interface AccountRow {
  id: number
  name: string
  institution: string | null
  mask: string | null
  type: string
  subtype: string | null
  balance: number | null
  available: number | null
  limit: number | null
  is_asset: boolean
  last_synced_at: string | null
  error: string | null
}

export interface MonthRow { month: string; spent: number; income: number; net: number }
export interface CategoryRow { category: string; amount: number; color?: string | null }

export interface Summary {
  months: MonthRow[]
  days: { date: string; spent: number; income: number; net: number }[]
  current_month: MonthRow
  categories: CategoryRow[]
  period: string
  period_totals: { spent: number; income: number; net: number; transfers: number }
  years: string[]
}

export interface TxnRow {
  id: number
  date: string
  merchant: string | null
  description: string | null
  amount: number
  category: string | null
  category_color?: string | null
  account: string | null
  pending: boolean
}

export interface RecurringRow {
  id: number
  name: string
  cadence: string
  amount: number
  next_due: string | null
  last_seen: string | null
  confidence: number
}

export interface Outstanding {
  id: number
  series_id: number | null
  name: string
  period: string
  amount: number
  expected_date: string | null
}

export interface BudgetRow {
  id: number
  category: string
  category_id: number
  amount: number
  spent: number
  month: string | null
}

export interface Insight {
  type: string
  severity: 'high' | 'medium' | 'low'
  title: string
  body: string
  estimated_monthly_savings: number
}

export interface CategoryRow2 { id: number; name: string; kind: string; color?: string | null }

export interface TxnDetail extends TxnRow {
  effective_month: string | null
  merchant_key: string | null
  category_id: number | null
  is_transfer: boolean
  is_manual_override: boolean
  notes: string | null
  source: string
  similar_count: number
}

export interface InstitutionRow {
  id: number
  name: string
  status: string
  last_synced_at: string | null
  last_error: string | null
  history_days: number | null
  history_from: string | null
  linked: boolean
  accounts: { id: number; name: string; mask: string | null; type: string; balance: number | null }[]
}

export interface SetupStatus {
  configured: boolean
  plaid_env: string
  env_path: string
  client_id_tail: string
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(await errorDetail(res, path))
  return res.json() as Promise<T>
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    ...(body === undefined
      ? {}
      : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  })
  if (!res.ok) throw new Error(await errorDetail(res, path))
  return res.json() as Promise<T>
}

/** The server explains its refusals in `detail`; without this the UI shows only a
 *  status code, which tells the person nothing about what to do next. */
async function errorDetail(res: Response, path: string): Promise<string> {
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') return body.detail
  } catch {
    // Not JSON — fall through to the status line.
  }
  return `${path} -> ${res.status}`
}

export interface Investments {
  positions: {
    id: number
    account: string
    account_id: number
    account_subtype: string | null
    ticker: string | null
    name: string | null
    type: string | null
    quantity: number
    price: number | null
    value: number | null
    cost_basis: number | null
    cost_basis_is_manual: boolean
    gain: number | null
    gain_pct: number | null
  }[]
  accounts: {
    id: number
    name: string
    subtype: string | null
    balance: number | null
    held: number
    uninvested: number
  }[]
  by_ticker: {
    ticker: string
    name: string | null
    type: string | null
    value: number
    share: number
    accounts: string[]
  }[]
  employer_exposure: { employer: string; value: number; share: number; holdings: string[] } | null
  portfolio_value: number
  holdings_value: number
  cost_basis_known: number
  value_with_known_basis: number
  unrealized_gain: number | null
  unrealized_gain_pct: number | null
  unpriced_value: number
  by_type: { type: string; value: number }[]
}

export interface BenefitRow {
  id: number
  name: string
  detail: string | null
  value: number | null
  period: string
  period_label: string
  cadence: string
  period_months: number
  start_month: number
  ends_on: string
  days_left: number
  claimed: boolean
  claimed_on: string | null
  note: string | null
}

export interface BenefitCard {
  account_id: number
  card: string
  mask: string | null
  benefits: BenefitRow[]
  value_left: number
}

export interface DebtRow {
  id: number
  name: string
  payee: string | null
  detail: string | null
  total: number
  monthly: number
  rate: number | null
  paid: number
  interest_paid: number
  principal_paid: number
  remaining: number
  interest_left: number
  scenarios: {
    extra: number
    monthly: number
    months: number
    payoff: string
    interest: number
    saved: number
    months_earlier: number
  }[]
  percent: number
  payments_made: number
  entries: number
  payments_left: number | null
  last_paid_on: string | null
  months_since_last: number | null
  paying: boolean
  projected_payoff: string | null
  payments: { id: number; paid_on: string; amount: number; note: string | null; linked: boolean }[]
}

export interface DebtsData {
  debts: DebtRow[]
  total_remaining: number
  total_paid: number
  total_interest: number
  monthly_committed: number
  unallocated: { id: number; date: string; amount: number; merchant: string | null }[]
}

export const api = {
  accounts: () => get<AccountRow[]>('/api/accounts'),
  summary: (period = 'current') => get<Summary>(`/api/summary?period=${period}`),
  transactions: (params: Record<string, string | number> = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null) as [string, string][],
    )
    return get<{ total: number; sum_out: number; sum_in: number; items: TxnRow[] }>(
      `/api/transactions?${q}`,
    )
  },
  sync: () => post<{ results: unknown[] }>('/api/sync'),
  investments: () => get<Investments>('/api/investments'),
  debts: () => get<DebtsData>('/api/debts'),
  allocateToDebt: (txnId: number, debtId: number) =>
    post<{ id: number }>(`/api/transactions/${txnId}/allocate`, { debt_id: debtId }),
  ignoreForDebts: (txnId: number) =>
    post<{ ignored: number }>(`/api/transactions/${txnId}/debt-ignore`),
  cardBenefits: () =>
    get<{ cards: BenefitCard[]; value_left: number; value_claimed: number; expiring: BenefitRow[] }>(
      '/api/card-benefits',
    ),
  claimBenefit: (id: number, claimed: boolean) =>
    post<{ claimed: boolean }>(`/api/card-benefits/${id}/claim`, { claimed }),
  createBenefit: (body: {
    account_id: number
    name: string
    value: number | null
    period_months: number
    start_month: number
  }) => post<{ id: number }>('/api/card-benefits', body),
  deleteBenefit: async (id: number) => {
    const res = await fetch(`/api/card-benefits/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  },
  recurring: () =>
    get<{
      upcoming: RecurringRow[]
      outstanding: Outstanding[]
      rejected: RecurringRow[]
      canceled: RecurringRow[]
      monthly_total: number
    }>('/api/recurring'),
  setRecurringStatus: async (id: number, status: 'active' | 'rejected' | 'canceled') => {
    const res = await fetch(`/api/recurring/${id}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  },
  transactionMonths: () => get<string[]>('/api/transaction-months'),
  importCsv: async (file: File, account: string, dryRun: boolean) => {
    const body = new FormData()
    body.append('file', file)
    if (account) body.append('account', account)
    body.append('dry_run', String(dryRun))
    const res = await fetch('/api/import-csv', { method: 'POST', body })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail ?? 'Import failed')
    return data as {
      profile: string; account: string; added: number
      skipped_duplicates: number; unparsed: number; dry_run: boolean
    }
  },
  detectRecurring: () => post<{ series: number }>('/api/recurring/detect'),
  budgets: () => get<BudgetRow[]>('/api/budgets'),
  saveBudget: async (category_id: number, amount: number) => {
    const res = await fetch('/api/budgets', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category_id, amount }),
    })
    if (!res.ok) throw new Error(`budget save -> ${res.status}`)
    return res.json()
  },
  insights: () => get<Insight[]>('/api/insights'),
  categories: () => get<CategoryRow2[]>('/api/categories'),
  transaction: (id: number) => get<TxnDetail>(`/api/transactions/${id}`),
  updateTransaction: async (
    id: number,
    body: { category_id?: number; notes?: string; is_transfer?: boolean },
  ) => {
    const res = await fetch(`/api/transactions/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  },
  applyToSimilar: async (id: number, category_id: number) => {
    const res = await fetch(`/api/transactions/${id}/apply-to-similar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category_id }),
    })
    if (!res.ok) throw new Error(await res.text())
    return res.json() as Promise<{ rule: string; updated: number }>
  },
  bulkUpdate: async (
    ids: number[],
    body: { category_id?: number; is_transfer?: boolean },
  ) => {
    const res = await fetch('/api/transactions/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, ...body }),
    })
    if (!res.ok) throw new Error(await res.text())
    return res.json() as Promise<{ updated: number }>
  },
  institutions: () => get<InstitutionRow[]>('/api/link/institutions'),
  renameAccount: async (id: number, display_name: string) => {
    const res = await fetch(`/api/accounts/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name }),
    })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  },
  linkToken: (kind: 'bank' | 'investments') =>
    get<{ link_token: string }>(`/api/link/token?kind=${kind}`),
  setupStatus: () =>
    get<SetupStatus>('/api/link/setup'),
  savePlaidCredentials: (client_id: string, secret: string, plaid_env = 'production') =>
    post<{ configured: boolean; plaid_env: string }>('/api/link/setup', {
      client_id, secret, plaid_env,
    }),
  reconnect: (id: number) =>
    get<{ link_token: string; institution: string }>(`/api/link/reconnect?institution_id=${id}`),
  exchange: async (public_token: string) => {
    const res = await fetch('/api/link/exchange', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ public_token }),
    })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  },
}

export const money = (n: number | null | undefined, cents = false) =>
  n == null
    ? '—'
    : n.toLocaleString('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: cents ? 2 : 0,
        maximumFractionDigits: cents ? 2 : 0,
      })

export const monthLabel = (ym: string) => {
  const [y, m] = ym.split('-').map(Number)
  return new Date(y, m - 1, 1).toLocaleDateString('en-US', { month: 'short', year: '2-digit' })
}
