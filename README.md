# Talents

A local-first personal finance app: budgets, recurring bills, net worth, investments,
spending dashboards, and savings advice.

> *"Well done, good and faithful servant! You have been faithful with a few things;
> I will put you in charge of many things. Enter into the joy of your master!"*
> — Matthew 25:21

Named for the parable in Matthew 25:14–30. A *talent* was a unit of money, and the
parable is about faithfully investing what you've been entrusted with rather than
burying it in the ground.

## Why it exists

Replaces a Notion-based tracker that couldn't do net worth, investments, recurring
bills, or savings advice — and was tedious to use.

## Privacy model

Everything runs on your Mac.

> **Before running this anywhere but localhost, read this.** There is **no
> authentication** on any endpoint — anyone who can reach the port can read every
> transaction, balance and account. That is a deliberate trade for a single-user
> loopback app, and it is why the server binds to `127.0.0.1`. Do not put it behind
> a public port, and do not host it for other people.
>
> **macOS only.** Token encryption uses the macOS Keychain via the `security`
> command, so linking a bank will fail on Linux or Windows until `crypto.py` grows
> another keystore backend.
>
> **Plaid credentials are yours alone.** They live in `.env`, which is git-ignored
> and has never been committed. Anyone who clones this repo must supply their own
> `PLAID_CLIENT_ID`/`PLAID_SECRET`, and their bank links consume their own Trial
> Items, not yours.

- The server binds to `127.0.0.1`. For phone access use **Tailscale**, never a public port.
- Plaid access tokens are encrypted with a **Fernet key stored in the macOS Keychain** —
  not in `.env`, not in the database. A leaked repo or stolen `talents.db` alone does not
  expose bank tokens.
- `.env`, `*.db`, and `statements/` are git-ignored.
- The LLM categorizer is **opt-in** and off by default; when enabled it only ever sees an
  unknown merchant string.

## Install

**Download `Talents.dmg`, open it, drag Talents to Applications.**

macOS will refuse the first launch — *"Talents is damaged and can't be opened."* It is
not damaged. The app is signed ad-hoc rather than with a $99 Apple Developer
certificate, so Gatekeeper quarantines it. Clear the flag once:

```bash
xattr -dr com.apple.quarantine /Applications/Talents.app
```

Then double-click. Talents opens in its own window — it is an ordinary Mac app with
a Dock icon, not a browser tab. Closing the window quits it.

Under the hood it runs a small local server on `127.0.0.1:8787` and draws the UI in
a native WKWebView, the same engine Safari uses. There is no bundled browser and no
second rendering engine. If port 8787 is busy it quietly picks another, and a second
launch shows the window that is already open rather than starting a rival server.

### First run

1. **Open Talents.** It starts on an empty dashboard — nothing is connected yet.
2. **Go to Accounts.** Because no credentials are stored, it shows a short setup
   form instead of the usual connect buttons.
3. **Get free Plaid keys.** The form links to
   [dashboard.plaid.com](https://dashboard.plaid.com/signup). Sign up, then copy the
   **production** client ID and secret from *Developers → Keys*.
4. **Paste them in and press Save.** They are checked against Plaid before being
   kept, so a typo is caught there and then rather than later when you try to
   connect a bank. Nothing is written to disk unless they work.
5. **Connect a bank.** The Accounts page now offers *Add bank or card* and
   *Add brokerage*, which open Plaid's own login window.
6. **Press Sync.** Your history is pulled in — up to two years where the bank
   offers it.

No terminal, no editing dotfiles, no restart: the credentials take effect
immediately. They are written to `.env` beside your database, readable only by you.

Anything Plaid cannot reach — a closed card, history older than its two-year
window, an institution that will not link — goes in through **statement import** on
the same page.

### Where your data lives

```
~/Library/Application Support/Talents/
  talents.db            your accounts, transactions, budgets, debts
  .env                  Plaid credentials
  personal_rules.json   optional, see "Categorisation" below
  talents.log           what the app did on startup
```

Deliberately outside the bundle: replacing `Talents.app` on an update would
otherwise destroy your history. Back up that folder and you have backed up
everything. Bank tokens are **not** in the database — they are encrypted with a key
in your Keychain.

Coming from a source checkout? `./scripts/migrate_to_app.sh` copies your existing
database and credentials across. Note that it *copies* — afterwards the app and the
checkout have separate data and will drift apart. Use one or the other.

## Building the app yourself

```bash
./scripts/build_app.sh        # -> dist/Talents.app and dist/Talents.dmg
```

Builds the UI, generates the icon, bundles with PyInstaller and wraps the result in
a disk image. Takes a couple of minutes. Requires the dev setup below.

## Setup (development)

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

cp .env.example .env       # add PLAID_CLIENT_ID and PLAID_SECRET
```

Optionally, for merchants only you would recognise — your church, a landlord you
pay by name — copy `personal_rules.example.json` to `personal_rules.json`. It is
git-ignored, and a rule there overrides a shipped one for the same merchant.

In a source checkout that file lives in the repo; in the packaged app it lives in
`~/Library/Application Support/Talents/`.

Build the UI, then run:

```bash
cd frontend && npm install && npm run build     # outputs into backend/talents/static
cd ../backend && ../.venv/bin/python -m uvicorn talents.main:app --host 127.0.0.1 --port 8787
```
Then open <http://127.0.0.1:8787>.

To run it the way the packaged app does — native window, no browser — use the
launcher instead:

```bash
cd backend && ../.venv/bin/python -m talents
```

For UI work, `npm run dev` in `frontend/` proxies `/api` to the backend with hot reload.

## What it does

- **Dashboard** — net worth, income vs. spending by month, category breakdown,
  savings advice, upcoming bills.
- **Transactions** — searchable, filterable, paginated.
- **Bills** — recurring series detected from history, plus anything expected but
  not yet paid.
- **Budgets** — per-category monthly limits against live actuals.
- **Accounts** — connect institutions, extend history, and import statements.

Statement import is the fallback for anything Plaid cannot reach: a discontinued card,
history older than its 730-day window, or an institution that will not link. Layouts
for Chase, Capital One, Citi/Costco and Bilt are detected from the file header, and
every import is previewed before anything is written.

## Nightly sync

```bash
sed -i '' "s|__REPO_PATH__|$(pwd)|g" scripts/com.talents.sync.plist
cp scripts/com.talents.sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.talents.sync.plist
```

Runs at 07:15 daily and skips quietly if the server is not up. The sync goes through
the API rather than a separate process, because the running server is what holds the
Keychain-derived encryption key. Logs append to `sync.log`.

## Backups and tests

```bash
./scripts/backup.sh                                   # verified copy, keeps 14
cd backend && ../.venv/bin/python -m pytest tests/ -q  # 83 tests
```

The nightly job backs up before syncing, so a bad sync is always recoverable. Backups
use `sqlite3 .backup` rather than `cp`, which is safe while the server holds the file
open, and each one is read back and row-counted before older copies are pruned.

The tests cover the logic that has actually gone wrong against real data: rent being
classified as a transfer, payroll landing in an expense category, an outlier hiding a
recurring bill, ended subscriptions reported as unpaid, and net worth including a
retired card.

## Phone access

The server binds to `127.0.0.1`. To reach it from a phone, install Tailscale on both
devices and set `HOST=0.0.0.0` in `.env`; Tailscale keeps it on the private network
rather than the public internet.

## Plaid notes

Uses the **Trial plan**: free, real production data, 10 Items lifetime.

- An *Item* is one bank login — Chase checking + both Chase cards is **one** Item.
- ⚠️ A slot is consumed on a successful token exchange, and **`/item/remove` does NOT
  free it**. Never delete and re-create connections casually.
- ⚠️ **Do not upgrade off Trial.** On Pay-as-you-go, Fidelity requires a manual request
  with no published SLA; Growth takes ~8 weeks. Upgrading is one-way.
- Link uses the desktop-browser **popup OAuth flow**, which requires no redirect URI —
  which is exactly why this app needs no public HTTPS endpoint.

Planned connections (4 of 10): Chase, Capital One, Citi, Fidelity.

## Conventions

- `amount` is **signed**: negative = money out, positive = money in.
- `effective_month` attributes a transaction to a period other than its payment date,
  so a late payment lands in the month it was owed.
- `is_transfer` is only set for movement between **two accounts you own**. An external
  Zelle (e.g. rent) is a real expense and is never excluded from spending.
- `holdings.cost_basis` may be null (Plaid does not guarantee it for Fidelity); manual
  entry fills the gap. Net worth and allocation work without it.
