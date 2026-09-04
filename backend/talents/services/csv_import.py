"""CSV import, the fallback for when Plaid cannot help.

Every aggregator eventually breaks, loses an institution, or refuses to link, and
Plaid's history stops at 730 days. A statement download always works, so this path
is kept alive rather than treated as a legacy escape hatch.

Layouts are auto-detected from the header, so files can simply be dropped in without
being told which bank they came from. The profiles are ported from
notion-finance-sync, where they were worked out against real exports.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, Transaction
from .categorizer import categorize, is_transfer_category, normalize_merchant
from .sync import _hash_key

# Same window the Notion import uses, to absorb posting-date drift between sources.
DUPLICATE_WINDOW_DAYS = 4


@dataclass
class CsvProfile:
    name: str
    detect: list[str]
    date_col: str
    desc_col: str
    account: str | None = None
    amount_col: str | None = None
    debit_col: str | None = None
    credit_col: str | None = None
    date_formats: list[str] = field(default_factory=list)
    # Chase exports purchases as negative; Citi and Capital One split them into
    # separate debit and credit columns, where a debit is a positive number.
    purchases_are_negative: bool = False


PROFILES = [
    CsvProfile(
        name="chase_credit",
        detect=["transaction date", "post date", "description", "amount"],
        date_col="Transaction Date", desc_col="Description", amount_col="Amount",
        purchases_are_negative=True, date_formats=["%m/%d/%Y"],
    ),
    CsvProfile(
        name="chase_checking",
        detect=["details", "posting date", "description", "amount"],
        account="Chase Checking Account",
        date_col="Posting Date", desc_col="Description", amount_col="Amount",
        purchases_are_negative=True, date_formats=["%m/%d/%Y"],
    ),
    CsvProfile(
        name="capital_one",
        detect=["transaction date", "posted date", "description", "debit", "credit"],
        account="Venture X",
        date_col="Transaction Date", desc_col="Description",
        debit_col="Debit", credit_col="Credit", date_formats=["%Y-%m-%d"],
    ),
    CsvProfile(
        name="costco_ytd",
        detect=["status", "date", "description", "debit", "credit", "member name"],
        account="Costco Anywhere Visa® Card by Citi",
        date_col="Date", desc_col="Description",
        debit_col="Debit", credit_col="Credit", date_formats=["%m/%d/%Y", "%b %d, %Y"],
    ),
    CsvProfile(
        name="citi",
        detect=["date", "description", "debit", "credit"],
        account="Costco Anywhere Visa® Card by Citi",
        date_col="Date", desc_col="Description",
        debit_col="Debit", credit_col="Credit", date_formats=["%b %d, %Y", "%m/%d/%Y"],
    ),
    CsvProfile(
        name="bilt",
        detect=["date", "description", "amount", "check #", "status"],
        account="Bilt Card",
        date_col="DATE", desc_col="DESCRIPTION", amount_col="AMOUNT",
        purchases_are_negative=True, date_formats=["%m/%d/%Y"],
    ),
]


def _rows_and_header(text: str) -> tuple[list[str], list[dict]]:
    """Read a statement, skipping any preamble above the real header.

    Citi puts two lines above its header, so the first line is not always the one
    that names the columns.
    """
    lines = text.splitlines()
    for start in range(min(6, len(lines))):
        candidate = "\n".join(lines[start:])
        reader = csv.DictReader(io.StringIO(candidate))
        if reader.fieldnames and len(reader.fieldnames) >= 3:
            header = [(f or "").strip().lower() for f in reader.fieldnames]
            return header, list(reader)
    return [], []


def pick_profile(header: list[str]) -> CsvProfile | None:
    """Most specific match wins, so Citi's year-to-date export beats the generic one."""
    best, best_score = None, 0
    for profile in PROFILES:
        if all(col in header for col in profile.detect):
            if len(profile.detect) > best_score:
                best, best_score = profile, len(profile.detect)
    return best


def _parse_date(value: str, formats: list[str]) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in formats + ["%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%m/%d/%y"]:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _money(value: str | None) -> float:
    if not value:
        return 0.0
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    if not cleaned:
        return 0.0
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _get(row: dict, column: str) -> str:
    """Case-insensitive lookup, since header casing differs between banks."""
    if column in row:
        return row[column] or ""
    wanted = column.strip().lower()
    for key, value in row.items():
        if (key or "").strip().lower() == wanted:
            return value or ""
    return ""


def import_csv(
    db: Session, text: str, account_name: str | None = None, dry_run: bool = False
) -> dict:
    header, rows = _rows_and_header(text)
    if not rows:
        return {"error": "Could not read any rows from this file"}

    profile = pick_profile(header)
    if profile is None:
        return {"error": f"Unrecognized layout. Columns found: {', '.join(header)}"}

    target = account_name or profile.account
    if not target:
        return {"error": f"{profile.name} files do not say which account they belong to; "
                         f"choose one before importing"}

    account = db.scalar(
        select(Account).where((Account.display_name == target) | (Account.name == target))
    )
    if account is None:
        return {"error": f"No account named {target!r}"}

    existing: dict[float, list[date]] = {}
    for txn in db.scalars(
        select(Transaction).where(Transaction.account_id == account.id)
    ).all():
        existing.setdefault(round(abs(txn.amount), 2), []).append(txn.date)

    added = skipped = unparsed = 0
    for row in rows:
        when = _parse_date(_get(row, profile.date_col), profile.date_formats)
        description = _get(row, profile.desc_col).strip()
        if when is None or not description:
            unparsed += 1
            continue

        if profile.amount_col:
            amount = _money(_get(row, profile.amount_col))
            if not profile.purchases_are_negative:
                amount = -amount
        else:
            debit = _money(_get(row, profile.debit_col or ""))
            credit = _money(_get(row, profile.credit_col or ""))
            amount = -abs(debit) if debit else abs(credit)
        if amount == 0:
            unparsed += 1
            continue

        if any(
            abs((other - when).days) <= DUPLICATE_WINDOW_DAYS
            for other in existing.get(round(abs(amount), 2), [])
        ):
            skipped += 1
            continue

        if not dry_run:
            db.add(
                Transaction(
                    account_id=account.id,
                    date=when,
                    effective_month=when.strftime("%Y-%m"),
                    amount=amount,
                    merchant_name=description,
                    raw_description=description,
                    merchant_key=normalize_merchant(description),
                    category_id=(cat_id := categorize(db, description, None, is_inflow=amount > 0)),
                    is_transfer=is_transfer_category(db, cat_id),
                    hash_key=_hash_key(account.id, when, amount, description),
                    source="csv",
                )
            )
        existing.setdefault(round(abs(amount), 2), []).append(when)
        added += 1

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return {
        "profile": profile.name,
        "account": target,
        "added": added,
        "skipped_duplicates": skipped,
        "unparsed": unparsed,
        "dry_run": dry_run,
    }
