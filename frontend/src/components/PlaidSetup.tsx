import { useState } from 'react'
import { api } from '../api'
import { Card } from './ui'

/** First-run setup for Plaid credentials.
 *
 * Without this the first thing a new person does is press "Connect a bank" and
 * get an error, then be told to hand-write a dotfile in a directory they have to
 * be told about. The credentials are saved by the server into the same `.env` it
 * already reads, so nothing about how the app is configured actually changes —
 * this only removes the need to do it in a terminal.
 */
export function PlaidSetup({ onDone }: { onDone: () => void }) {
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await api.savePlaidCredentials(clientId.trim(), secret.trim())
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const ready = clientId.trim() !== '' && secret.trim() !== '' && !saving

  return (
    <Card title="Connect Talents to your banks">
      <p className="text-sm leading-relaxed text-gray-600">
        Talents reads your accounts through{' '}
        <a
          href="https://dashboard.plaid.com/signup"
          target="_blank"
          rel="noreferrer"
          className="font-medium text-brand-600 hover:underline"
        >
          Plaid
        </a>
        . Create a free account, then copy the <strong>production</strong> client ID and
        secret from <span className="whitespace-nowrap">Developers → Keys</span>.
      </p>

      <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-sm leading-relaxed text-gray-600">
        <li>
          When Plaid asks what you are signing up for, choose{' '}
          <strong>Personal use</strong> — not <em>App user</em>, which is for people
          linking a bank to somebody else's app and will not get you any keys.
        </li>
        <li>
          Accept the free <strong>Trial plan</strong>. It is auto-approved, uses real
          bank data, and covers ten connected institutions.
        </li>
        <li>
          Open <span className="whitespace-nowrap">Developers → Keys</span> and copy the
          client ID and the <strong>production</strong> secret — not the sandbox one.
        </li>
      </ol>

      <div className="mt-4 space-y-3">
        <label className="block">
          <span className="text-xs font-medium text-gray-500">Client ID</span>
          <input
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            placeholder="5f9a2c1e8b7d4a0012c3e4f5"
            className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm
                       focus:border-brand-500 focus:outline-none"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-gray-500">Secret</span>
          <input
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="••••••••••••••••••••••••"
            className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm
                       focus:border-brand-500 focus:outline-none"
          />
        </label>
      </div>

      {error && (
        <p className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs leading-relaxed text-rose-800">
          {error}
        </p>
      )}

      <button
        onClick={save}
        disabled={!ready}
        className="mt-4 rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white
                   hover:bg-brand-600 disabled:cursor-not-allowed disabled:bg-gray-200
                   disabled:text-gray-400"
      >
        {saving ? 'Checking with Plaid…' : 'Save and continue'}
      </button>

      <p className="mt-3 text-xs leading-relaxed text-gray-500">
        Checked against Plaid before being kept, so a typo is caught here rather than
        when you try to connect a bank. Stored on this Mac only, in a file readable
        just by you.
      </p>
    </Card>
  )
}
