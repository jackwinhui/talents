"""Import the Notion expense and income history.

Plaid cannot supply this. Its transaction window reaches back 730 days at most, and
the Bilt card was discontinued in February 2026 so no aggregator can ever return it.
The Notion tracker is the only record of either.

Where the two overlap, Plaid wins: it is the bank's own record, and it has already
proven more complete than the manual CSV imports that fed Notion. Notion rows are
therefore skipped when a Plaid transaction already covers the same spend, matched on
account, amount and a few days either side to absorb posting-date drift.

Idempotent: rows are keyed on the Notion page id, so re-running updates rather than
duplicates.

Run:  .venv/bin/python scripts/import_notion.py [--dry-run]
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from talents.db import SessionLocal, init_db  # noqa: E402
from talents.models import Account, Category, Institution, Transaction  # noqa: E402
from talents.services.categorizer import normalize_merchant  # noqa: E402
from talents.services.sync import _hash_key  # noqa: E402

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Database ids, not data-source ids: the 2022-06-28 API queries databases.
EXPENSES_DB = "2dc9fcb8-27a1-81f7-bd17-c12fb7d62fd0"
INCOME_DB = "2dc9fcb8-27a1-810e-9be3-cc8c52b7b28a"

# Notion "Account Charged" -> the account name used in this app.
ACCOUNT_MAP = {
    "Chase Checking Account": "Chase Checking Account",
    "Chase Freedom Unlimited": "Chase Freedom Unlimited",
    "Chase Preferred": "Chase Preferred",
    "Capital One Venture X": "Venture X",
    "Costco Citi Card": "Costco Anywhere Visa® Card by Citi",
    "Bilt Card": "Bilt Card",
}

# Notion income tags -> our income categories.
INCOME_CATEGORY_MAP = {
    "Salary": "Salary",
    "Rent": "Rent",
    "Freelance": "Freelance",
    "Dividends": "Dividends",
    "Interest": "Interest",
    "Other": "Other Income",
}

DUPLICATE_WINDOW_DAYS = 4


def load_token() -> str:
    for line in (Path.home() / "Github/notion-finance-sync/.env").read_text().splitlines():
        if line.startswith("NOTION_TOKEN="):
            token = line.split("=", 1)[1].strip()
            if token:
                return token
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        sys.exit("No Notion token found. Set NOTION_TOKEN or add it to notion-finance-sync/.env")
    return token


def fetch_all(client: httpx.Client, database_id: str, label: str = "") -> list[dict]:
    """Page through a Notion database.

    Notion occasionally stalls on a page, and a single slow response would otherwise
    lose the whole run, so each page is retried before giving up.
    """
    rows, cursor = [], None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor

        payload = None
        for attempt in range(4):
            try:
                resp = client.post(f"{NOTION_API}/databases/{database_id}/query", json=body)
                resp.raise_for_status()
                payload = resp.json()
                break
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                if attempt == 3:
                    raise
                wait = 2 ** attempt
                print(f"  {label} page retry {attempt + 1} after {type(exc).__name__}; "
                      f"waiting {wait}s")
                time.sleep(wait)

        assert payload is not None
        rows.extend(payload["results"])
        print(f"  {label} fetched {len(rows)}", end="\r", flush=True)
        if not payload.get("has_more"):
            print()
            return rows
        cursor = payload["next_cursor"]


def plain(prop: dict | None) -> str | None:
    if not prop:
        return None
    kind = prop.get("type")
    if kind == "title":
        return "".join(t["plain_text"] for t in prop["title"]) or None
    if kind == "rich_text":
        return "".join(t["plain_text"] for t in prop["rich_text"]) or None
    if kind == "number":
        return prop["number"]
    if kind == "select":
        return (prop["select"] or {}).get("name")
    if kind == "date":
        return (prop["date"] or {}).get("start")
    return None


def get_or_create_account(db, name: str) -> Account:
    acct = db.scalar(
        select(Account).where((Account.display_name == name) | (Account.name == name))
    )
    if acct:
        return acct
    # Bilt is the only account with no live connection: the card was retired, so it
    # exists for history alone and must stay out of current balances and net worth.
    inst = db.scalar(select(Institution).where(Institution.name == "Historical"))
    if inst is None:
        inst = Institution(name="Historical", provider="notion", status="inactive")
        db.add(inst)
        db.flush()
    acct = Account(
        institution_id=inst.id, name=name, display_name=name,
        type="credit", is_asset=False, is_active=False,
    )
    db.add(acct)
    db.flush()
    return acct


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    token = load_token()
    init_db()

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    with httpx.Client(headers=headers, timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        expenses = fetch_all(client, EXPENSES_DB, "expenses")
        income = fetch_all(client, INCOME_DB, "income")
    print(f"fetched {len(expenses)} expenses, {len(income)} income rows from Notion")

    created = updated = skipped_dupe = skipped_bad = 0

    with SessionLocal() as db:
        categories = {c.name: c for c in db.scalars(select(Category)).all()}
        accounts: dict[str, Account] = {}

        # Index existing Plaid rows so overlapping history is not imported twice.
        existing: dict[tuple[int, float], list[date]] = {}
        for txn in db.scalars(select(Transaction).where(Transaction.source == "plaid")).all():
            existing.setdefault((txn.account_id or 0, round(abs(txn.amount), 2)), []).append(txn.date)

        def is_duplicate(account_id: int, amount: float, when: date) -> bool:
            for other in existing.get((account_id, round(abs(amount), 2)), []):
                if abs((other - when).days) <= DUPLICATE_WINDOW_DAYS:
                    return True
            return False

        def ingest(pages: list[dict], *, kind: str) -> None:
            nonlocal created, updated, skipped_dupe, skipped_bad
            for page in pages:
                props = page["properties"]
                raw_date = plain(props.get("Date"))
                amount = plain(props.get("Amount"))
                source_name = plain(props.get("Source")) or ""
                if not raw_date or amount in (None, 0):
                    skipped_bad += 1
                    continue
                when = datetime.fromisoformat(raw_date[:10]).date()

                if kind == "expense":
                    notion_account = plain(props.get("Account Charged")) or "Chase Checking Account"
                    account_name = ACCOUNT_MAP.get(notion_account, notion_account)
                    tag = plain(props.get("Tags")) or "Other"
                    category = categories.get(tag)
                    signed = -abs(float(amount))
                else:
                    account_name = "Chase Checking Account"
                    tag = plain(props.get("Tags")) or "Other"
                    category = categories.get(INCOME_CATEGORY_MAP.get(tag, "Other Income"))
                    signed = abs(float(amount))

                if account_name not in accounts:
                    accounts[account_name] = get_or_create_account(db, account_name)
                account = accounts[account_name]

                row = db.scalar(
                    select(Transaction).where(Transaction.notion_page_id == page["id"])
                )
                if row is None:
                    if is_duplicate(account.id, signed, when):
                        skipped_dupe += 1
                        continue
                    row = Transaction(notion_page_id=page["id"], source="notion")
                    db.add(row)
                    created += 1
                else:
                    updated += 1

                row.account_id = account.id
                row.date = when
                row.effective_month = when.strftime("%Y-%m")
                row.amount = signed
                row.merchant_name = source_name
                row.raw_description = source_name
                row.merchant_key = normalize_merchant(source_name)
                row.category_id = category.id if category else None
                # The Notion tags were curated by hand over two years, so they are
                # treated as authoritative and protected from the rule engine.
                row.is_manual_override = True
                row.is_transfer = tag == "Transfers"
                row.hash_key = _hash_key(account.id, when, signed, source_name)

        ingest(expenses, kind="expense")
        ingest(income, kind="income")

        if dry_run:
            db.rollback()
            print("dry run — nothing written")
        else:
            db.commit()

    print(
        f"created {created}, updated {updated}, "
        f"skipped {skipped_dupe} already covered by Plaid, {skipped_bad} incomplete"
    )


if __name__ == "__main__":
    main()
