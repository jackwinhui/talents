"""Read APIs for the dashboard, plus the sync trigger."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Account, BenefitClaim, Budget, CardBenefit, Category, CategoryRule, Debt,
    DebtExclusion, DebtPayment, Holding, Institution, PendingObligation,
    RecurringSeries, Security,
    Transaction,
)
from ..services.categorizer import (
    TRANSFER_CATEGORY,
    categorize,
    is_peer_to_peer,
    is_transfer_category,
    seed_rules,
)
from ..services import benefits, csv_import, debts, insights, recurring
from ..services.sync import sync_all

router = APIRouter(prefix="/api", tags=["data"])


def _transfer_category_id(db: Session) -> int | None:
    return db.scalar(select(Category.id).where(Category.name == "Transfers"))


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


@router.post("/sync")
def run_sync(db: Session = Depends(get_db)) -> dict:
    seed_rules(db)
    results = sync_all(db)
    return {"results": results}


@router.post("/recategorize")
def recategorize(db: Session = Depends(get_db)) -> dict:
    """Re-apply categorization rules to every transaction, keeping manual overrides.

    Only the ported rules are rebuilt. Rules created by "apply to all like this" are
    the user's own work and are not in the source list, so clearing them wholesale
    would silently undo every categorization they had corrected by hand.
    """
    db.query(CategoryRule).filter(CategoryRule.is_user_defined.is_(False)).delete()
    db.commit()
    seed_rules(db)

    changed = 0
    # Which accounts are peer-to-peer, so an unrecognised credit there is read as
    # someone settling up rather than as income.
    p2p_accounts = {
        acct_id
        for (acct_id, inst_name) in db.execute(
            select(Account.id, Institution.name).join(
                Institution, Institution.id == Account.institution_id
            )
        ).all()
        if is_peer_to_peer(inst_name)
    }
    for txn in db.scalars(select(Transaction).where(Transaction.is_manual_override.is_(False))).all():
        text = f"{txn.merchant_name or ''} {txn.raw_description or ''}"
        # fallback=None so a transaction is only moved when something actually
        # matches. Without this, anything Plaid classified but no local rule covers
        # would be demoted to "Other" on every run.
        new_id = categorize(
            db, text, txn.plaid_category, fallback=None, is_inflow=txn.amount > 0,
            inflow_fallback=(
                TRANSFER_CATEGORY if txn.account_id in p2p_accounts else "Other Income"
            ),
        )
        if new_id is not None and new_id != txn.category_id:
            txn.category_id = new_id
            changed += 1
        if new_id is not None:
            txn.is_transfer = is_transfer_category(db, new_id)
    db.commit()
    return {"recategorized": changed}


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(Account, Institution)
        .join(Institution, Account.institution_id == Institution.id, isouter=True)
        .where(Account.is_active.is_(True))
    ).all()
    return [
        {
            "id": a.id,
            "name": a.display_name or a.name,
            "institution": i.name if i else None,
            "mask": a.mask,
            "type": a.type,
            "subtype": a.subtype,
            "balance": a.current_balance,
            "available": a.available_balance,
            "limit": a.credit_limit,
            "is_asset": a.is_asset,
            "last_synced_at": i.last_synced_at.isoformat() if i and i.last_synced_at else None,
            "error": i.last_error if i else None,
        }
        for a, i in rows
    ]


class AccountRename(BaseModel):
    display_name: str


@router.patch("/accounts/{account_id}")
def rename_account(
    account_id: int, payload: AccountRename, db: Session = Depends(get_db)
) -> dict:
    """Give an account a usable name.

    Plaid returns whatever the bank reports, and Chase reports both of its cards as
    "CREDIT CARD / Ultimate Rewards®" — identical apart from the mask — so the
    product name has to come from the user.
    """
    acct = db.get(Account, account_id)
    if acct is None:
        raise HTTPException(404, "Account not found")
    name = payload.display_name.strip()
    acct.display_name = name or None
    db.commit()
    return {"id": acct.id, "display_name": acct.display_name, "name": acct.name}


@router.get("/summary")
def summary(period: str = "current", db: Session = Depends(get_db)) -> dict:
    """Spending and income by month, plus a category breakdown for one period.

    `period` is "current", "all", or a year such as "2025". Notion offered a donut
    per year, which only becomes useful once there is more than one year of history.
    Months are always returned in full so the cumulative chart and the monthly table
    can cover everything.
    """
    transfers = db.scalar(select(Category.id).where(Category.name == "Transfers"))
    rows = db.execute(
        select(Transaction, Category)
        .join(Category, Transaction.category_id == Category.id, isouter=True)
        .where(Transaction.is_transfer.is_(False))
    ).all()

    # Money moved between accounts you own is not spending. It is reported
    # separately so the net figure can be checked rather than taken on trust:
    # counting card payments as expenses is what made the old Notion rollup show
    # a loss.
    transfer_rows = db.scalars(
        select(Transaction).where(Transaction.is_transfer.is_(True))
    ).all()

    this_month = _month_key(date.today())

    def in_period(month: str) -> bool:
        if period == "all":
            return True
        if period == "current":
            return month == this_month
        return month.startswith(period)

    by_month: dict[str, dict[str, float]] = defaultdict(lambda: {"spent": 0.0, "income": 0.0})
    by_day: dict[str, dict[str, float]] = defaultdict(lambda: {"spent": 0.0, "income": 0.0})
    by_category: dict[str, float] = defaultdict(float)
    period_totals = {"spent": 0.0, "income": 0.0}

    for txn, cat in rows:
        if cat and transfers and cat.id == transfers:
            continue
        key = txn.effective_month or _month_key(txn.date)
        name = cat.name if cat else "Other"
        # Money arriving against an expense category is a refund, so it reduces that
        # category rather than counting as income. A returned jacket is not earnings.
        is_refund = txn.amount > 0 and cat is not None and cat.kind == "expense"
        # A single month has too few points to plot as a running total by month, so
        # the current month is also bucketed by day.
        day = txn.date.isoformat() if key == this_month else None

        if txn.amount < 0 or is_refund:
            delta = -txn.amount if txn.amount < 0 else -txn.amount
            by_month[key]["spent"] += delta
            if day:
                by_day[day]["spent"] += delta
            if in_period(key):
                by_category[name] = by_category.get(name, 0.0) + delta
                period_totals["spent"] += delta
        else:
            by_month[key]["income"] += txn.amount
            if day:
                by_day[day]["income"] += txn.amount
            if in_period(key):
                period_totals["income"] += txn.amount

    months_out = [
        {"month": m, "spent": round(v["spent"], 2), "income": round(v["income"], 2),
         "net": round(v["income"] - v["spent"], 2)}
        for m, v in sorted(by_month.items())
    ]

    transfers_out = 0.0
    for txn in transfer_rows:
        key = txn.effective_month or _month_key(txn.date)
        if txn.amount < 0 and in_period(key):
            transfers_out += -txn.amount

    days_out = [
        {"date": d, "spent": round(v["spent"], 2), "income": round(v["income"], 2),
         "net": round(v["income"] - v["spent"], 2)}
        for d, v in sorted(by_day.items())
    ]

    colors = {c.name: c.color for c in db.scalars(select(Category)).all()}
    cats_out = [
        {"category": c, "amount": round(a, 2), "color": colors.get(c)}
        for c, a in sorted(by_category.items(), key=lambda kv: -kv[1])
        if round(a, 2) != 0
    ]
    current = next((m for m in months_out if m["month"] == this_month), None)

    years = sorted({m["month"][:4] for m in months_out}, reverse=True)
    return {
        "months": months_out,
        "days": days_out,
        "current_month": current or {"month": this_month, "spent": 0, "income": 0, "net": 0},
        "categories": cats_out,
        "period": period,
        "period_totals": {
            "spent": round(period_totals["spent"], 2),
            "income": round(period_totals["income"], 2),
            "net": round(period_totals["income"] - period_totals["spent"], 2),
            "transfers": round(transfers_out, 2),
        },
        "years": years,
    }


@router.get("/transactions")
def list_transactions(
    limit: int = Query(100, le=1000),
    offset: int = 0,
    search: str | None = None,
    category: str | None = None,
    account_id: int | None = None,
    month: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    stmt = (
        select(Transaction, Category, Account)
        .join(Category, Transaction.category_id == Category.id, isouter=True)
        .join(Account, Transaction.account_id == Account.id, isouter=True)
    )
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(func.lower(Transaction.merchant_name).like(like))
    if category:
        stmt = stmt.where(Category.name == category)
    if account_id:
        stmt = stmt.where(Transaction.account_id == account_id)
    if month:
        stmt = stmt.where(Transaction.effective_month == month)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    # Summed over everything the filter matches, not just the page on screen.
    # Answering "how much have I paid this person" otherwise means adding up rows
    # by hand across several pages.
    sub = stmt.subquery()
    money_out, money_in = db.execute(
        select(
            func.coalesce(func.sum(case((sub.c.amount < 0, -sub.c.amount), else_=0.0)), 0.0),
            func.coalesce(func.sum(case((sub.c.amount > 0, sub.c.amount), else_=0.0)), 0.0),
        )
    ).one()
    rows = db.execute(
        stmt.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(limit).offset(offset)
    ).all()
    return {
        "total": total,
        "sum_out": round(money_out or 0, 2),
        "sum_in": round(money_in or 0, 2),
        "items": [
            {
                "id": t.id,
                "date": t.date.isoformat(),
                "merchant": t.merchant_name,
                "description": t.raw_description,
                "amount": t.amount,
                "category": c.name if c else None,
                "category_color": c.color if c else None,
                "account": (a.display_name or a.name) if a else None,
                "pending": t.is_pending,
            }
            for t, c, a in rows
        ],
    }


@router.get("/recurring")
def recurring_bills(db: Session = Depends(get_db)) -> dict:
    if not db.scalar(select(func.count()).select_from(RecurringSeries)):
        recurring.detect(db)
    return {
        "upcoming": recurring.upcoming(db),
        "outstanding": recurring.outstanding(db),
        "rejected": recurring.rejected(db),
        "canceled": recurring.canceled(db),
        "monthly_total": recurring.monthly_total(db),
    }


class RecurringStatus(BaseModel):
    status: str


@router.post("/recurring/{series_id}/status")
def set_recurring_status(
    series_id: int, payload: RecurringStatus, db: Session = Depends(get_db)
) -> dict:
    """Accept, reject or cancel a detected series.

    "rejected" means it was never a bill; "canceled" means it was a real bill that
    is no longer paid, such as an insurer that has been switched. Both are
    remembered by merchant so re-running detection does not bring them back, but a
    canceled series revives itself if payments resume.
    """
    if payload.status not in ("active", "rejected", "canceled", "paused"):
        raise HTTPException(400, "Unknown status")
    series = db.get(RecurringSeries, series_id)
    if series is None:
        raise HTTPException(404, "Series not found")
    series.status = payload.status
    if payload.status in ("rejected", "canceled"):
        # The obligation was raised by a bill the user says they no longer owe.
        db.query(PendingObligation).filter(
            PendingObligation.recurring_series_id == series.id
        ).delete()
    db.commit()
    return {"id": series.id, "status": series.status}


@router.post("/recurring/detect")
def detect_recurring(db: Session = Depends(get_db)) -> dict:
    return {"series": recurring.detect(db)}


@router.get("/budgets")
def list_budgets(db: Session = Depends(get_db)) -> list[dict]:
    """Budgets with this month's actuals, so the UI needs a single request."""
    this_month = date.today().strftime("%Y-%m")
    spent: dict[int, float] = {}
    for txn in db.scalars(
        select(Transaction).where(
            Transaction.amount < 0, Transaction.is_transfer.is_(False)
        )
    ).all():
        if (txn.effective_month or "") == this_month and txn.category_id:
            spent[txn.category_id] = spent.get(txn.category_id, 0.0) + -txn.amount

    rows = db.execute(
        select(Budget, Category).join(Category, Budget.category_id == Category.id)
    ).all()
    return [
        {
            "id": b.id,
            "category": c.name,
            "category_id": c.id,
            "amount": b.amount,
            "spent": round(spent.get(c.id, 0.0), 2),
            "month": b.month,
        }
        for b, c in rows
        if b.month in (None, this_month)
    ]


class BudgetIn(BaseModel):
    category_id: int
    amount: float


@router.put("/budgets")
def upsert_budget(payload: BudgetIn, db: Session = Depends(get_db)) -> dict:
    """Budgets with month=NULL act as the recurring default for every month."""
    row = db.scalar(
        select(Budget).where(
            Budget.category_id == payload.category_id, Budget.month.is_(None)
        )
    )
    if payload.amount <= 0:
        if row:
            db.delete(row)
            db.commit()
        return {"deleted": True}
    if row is None:
        row = Budget(category_id=payload.category_id, amount=payload.amount)
        db.add(row)
    else:
        row.amount = payload.amount
    db.commit()
    return {"id": row.id, "amount": row.amount}


@router.get("/insights")
def list_insights(db: Session = Depends(get_db)) -> list[dict]:
    return insights.generate(db)


class TransactionUpdate(BaseModel):
    category_id: int | None = None
    notes: str | None = None
    is_transfer: bool | None = None


@router.get("/transactions/{txn_id}")
def get_transaction(txn_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.execute(
        select(Transaction, Category, Account)
        .join(Category, Transaction.category_id == Category.id, isouter=True)
        .join(Account, Transaction.account_id == Account.id, isouter=True)
        .where(Transaction.id == txn_id)
    ).first()
    if row is None:
        raise HTTPException(404, "Transaction not found")
    t, c, a = row

    similar = db.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.merchant_key == t.merchant_key, Transaction.id != t.id)
    ) or 0

    return {
        "id": t.id,
        "date": t.date.isoformat(),
        "effective_month": t.effective_month,
        "merchant": t.merchant_name,
        "description": t.raw_description,
        "merchant_key": t.merchant_key,
        "amount": t.amount,
        "category": c.name if c else None,
        "category_id": t.category_id,
        "account": (a.display_name or a.name) if a else None,
        "pending": t.is_pending,
        "is_transfer": t.is_transfer,
        "is_manual_override": t.is_manual_override,
        "notes": t.notes,
        "source": t.source,
        "similar_count": similar,
    }


@router.patch("/transactions/{txn_id}")
def update_transaction(
    txn_id: int, payload: TransactionUpdate, db: Session = Depends(get_db)
) -> dict:
    """Edit one transaction.

    Changing the category sets is_manual_override, so a later re-run of the rules
    will not quietly undo the correction.
    """
    txn = db.get(Transaction, txn_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found")

    if payload.category_id is not None and payload.category_id != txn.category_id:
        txn.category_id = payload.category_id
        txn.is_manual_override = True
        # Choosing Transfers in the UI has to set the flag too, otherwise recurring
        # detection and insights keep treating the row as ordinary spending.
        txn.is_transfer = is_transfer_category(db, payload.category_id)
    if payload.notes is not None:
        txn.notes = payload.notes
    if payload.is_transfer is not None:
        txn.is_transfer = payload.is_transfer
        if payload.is_transfer:
            # Flag and category must not disagree: the summary filters on one and
            # recurring detection on the other.
            txn.category_id = _transfer_category_id(db) or txn.category_id
            txn.is_manual_override = True
    db.commit()
    return {"id": txn.id, "category_id": txn.category_id, "is_transfer": txn.is_transfer}


class ApplyToSimilar(BaseModel):
    category_id: int


@router.post("/transactions/{txn_id}/apply-to-similar")
def apply_to_similar(
    txn_id: int, payload: ApplyToSimilar, db: Session = Depends(get_db)
) -> dict:
    """Turn a one-off correction into a durable rule for this merchant.

    The rule stores the normalized merchant key, so it keeps matching even though
    descriptions carry a different reference on every payment.
    """
    txn = db.get(Transaction, txn_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found")
    pattern = (txn.merchant_key or txn.merchant_name or "").strip().lower()
    if not pattern:
        raise HTTPException(400, "This transaction has no merchant to match on")

    rule = db.scalar(select(CategoryRule).where(CategoryRule.pattern == pattern))
    if rule:
        rule.category_id = payload.category_id
        rule.is_user_defined = True
    else:
        db.add(
            CategoryRule(
                pattern=pattern, category_id=payload.category_id, priority=len(pattern),
                is_user_defined=True,
            )
        )

    updated = 0
    is_xfer = is_transfer_category(db, payload.category_id)
    for other in db.scalars(
        select(Transaction).where(Transaction.merchant_key == txn.merchant_key)
    ).all():
        if other.category_id != payload.category_id:
            other.category_id = payload.category_id
            updated += 1
        other.is_manual_override = True
        other.is_transfer = is_xfer
    db.commit()
    return {"rule": pattern, "updated": updated}


class BulkUpdate(BaseModel):
    ids: list[int]
    category_id: int | None = None
    is_transfer: bool | None = None


@router.post("/transactions/bulk")
def bulk_update(payload: BulkUpdate, db: Session = Depends(get_db)) -> dict:
    """Apply a category or transfer flag to many transactions at once.

    As with a single edit, setting a category marks the rows as manually overridden
    so a later rules run cannot revert them.
    """
    if not payload.ids:
        raise HTTPException(400, "No transactions selected")
    rows = db.scalars(select(Transaction).where(Transaction.id.in_(payload.ids))).all()
    bulk_is_xfer = is_transfer_category(db, payload.category_id)
    for txn in rows:
        if payload.category_id is not None:
            txn.category_id = payload.category_id
            txn.is_manual_override = True
            txn.is_transfer = bulk_is_xfer
        if payload.is_transfer is not None:
            txn.is_transfer = payload.is_transfer
            if payload.is_transfer:
                txn.category_id = _transfer_category_id(db) or txn.category_id
                txn.is_manual_override = True
    db.commit()
    return {"updated": len(rows)}


@router.get("/transaction-months")
def transaction_months(db: Session = Depends(get_db)) -> list[str]:
    """Months that actually contain transactions, newest first."""
    rows = db.scalars(
        select(Transaction.effective_month)
        .where(Transaction.effective_month.is_not(None))
        .distinct()
        .order_by(Transaction.effective_month.desc())
    ).all()
    return [m for m in rows if m]


@router.post("/import-csv")
async def import_csv_file(
    file: UploadFile = File(...),
    account: str | None = Form(None),
    dry_run: bool = Form(False),
    db: Session = Depends(get_db),
) -> dict:
    """Import a bank statement. The layout is detected from its header."""
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    result = csv_import.import_csv(db, text, account_name=account, dry_run=dry_run)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)) -> list[dict]:
    return [
        {"id": c.id, "name": c.name, "kind": c.kind, "color": c.color}
        for c in db.scalars(select(Category).order_by(Category.kind, Category.name)).all()
    ]


@router.get("/investments")
def investments(db: Session = Depends(get_db)) -> dict:
    """Holdings, allocation and unrealized gain across every brokerage account.

    Value comes from the broker's own figure where it is given. Plaid returns NULL
    cost basis for some Fidelity positions - the 401(k) blended funds especially -
    so gain is only reported for the part of the portfolio where the basis is known,
    rather than quietly treating an unknown basis as zero and inventing a profit.
    """
    rows = db.execute(
        select(Holding, Security, Account)
        .join(Security, Holding.security_id == Security.id)
        .join(Account, Holding.account_id == Account.id)
    ).all()

    positions = []
    for h, sec, acct in rows:
        value = h.institution_value
        if value is None and sec.close_price is not None:
            value = h.quantity * sec.close_price
        gain = (value - h.cost_basis) if (value is not None and h.cost_basis) else None
        positions.append({
            "id": h.id,
            "account": acct.display_name or acct.name,
            "account_id": acct.id,
            "account_subtype": acct.subtype,
            "ticker": sec.ticker,
            "name": sec.name,
            "type": sec.type,
            "quantity": round(h.quantity, 4),
            "price": sec.close_price,
            "value": round(value, 2) if value is not None else None,
            "cost_basis": round(h.cost_basis, 2) if h.cost_basis else None,
            "cost_basis_is_manual": h.cost_basis_is_manual,
            "gain": round(gain, 2) if gain is not None else None,
            "gain_pct": round(gain / h.cost_basis * 100, 2) if gain is not None and h.cost_basis else None,
        })
    positions.sort(key=lambda p: -(p["value"] or 0))

    total = sum(p["value"] or 0 for p in positions)
    priced = [p for p in positions if p["gain"] is not None]
    known_basis = sum(p["cost_basis"] for p in priced)
    known_value = sum(p["value"] for p in priced)

    # Investment accounts with no holdings still hold money - an ESPP account
    # mid-purchase-period, for example - so the account balance is the truth for
    # the portfolio total, not the sum of positions.
    accounts = []
    for acct in db.scalars(
        select(Account).where(Account.type == "investment", Account.is_active.is_(True))
    ).all():
        held = sum(p["value"] or 0 for p in positions if p["account_id"] == acct.id)
        accounts.append({
            "id": acct.id,
            "name": acct.display_name or acct.name,
            "subtype": acct.subtype,
            "balance": acct.current_balance,
            "held": round(held, 2),
            "uninvested": round((acct.current_balance or 0) - held, 2),
        })
    portfolio = sum(a["balance"] or 0 for a in accounts)

    by_type: dict[str, float] = defaultdict(float)
    for p in positions:
        by_type[p["type"] or "unknown"] += p["value"] or 0

    # The same fund can be held in several accounts - SOXX sits in both the
    # brokerage and the HSA - so concentration has to be measured per security
    # rather than per row.
    by_ticker: dict[str, dict] = {}
    for p in positions:
        key = p["ticker"] or p["name"] or "unknown"
        entry = by_ticker.setdefault(
            key,
            {"ticker": key, "name": p["name"], "type": p["type"], "value": 0.0, "accounts": []},
        )
        entry["value"] += p["value"] or 0
        entry["accounts"].append(p["account"])
    holdings_by_ticker = sorted(by_ticker.values(), key=lambda e: -e["value"])
    for e in holdings_by_ticker:
        e["value"] = round(e["value"], 2)
        e["share"] = round(e["value"] / portfolio * 100, 2) if portfolio else 0

    return {
        "positions": positions,
        "accounts": accounts,
        "by_ticker": holdings_by_ticker,
        "employer_exposure": _employer_exposure(db, positions, accounts, portfolio),
        "portfolio_value": round(portfolio, 2),
        "holdings_value": round(total, 2),
        "cost_basis_known": round(known_basis, 2),
        "value_with_known_basis": round(known_value, 2),
        "unrealized_gain": round(known_value - known_basis, 2) if known_basis else None,
        "unrealized_gain_pct": (
            round((known_value - known_basis) / known_basis * 100, 2) if known_basis else None
        ),
        "unpriced_value": round(total - known_value, 2),
        "by_type": [
            {"type": k, "value": round(v, 2)}
            for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])
        ],
    }


def _employer_exposure(db: Session, positions: list[dict], accounts: list[dict], portfolio: float) -> dict | None:
    """How much of the portfolio rides on the same company that pays the salary.

    Holding your employer's stock doubles a bet you have already made: a bad year
    can cost the job and the savings together. The employer is read from the salary
    payments rather than configured, so this needs no setup and follows a job change.

    Only securities are matched by name. The 401(k) is administered by the employer
    but holds a target-date fund, so matching on the account name would wildly
    overstate the exposure. Stock-plan accounts are counted whole, since that is
    what they hold even when the broker reports no positions.
    """
    salary = db.scalar(
        select(Transaction.merchant_key)
        .join(Category, Transaction.category_id == Category.id)
        .where(Category.name == "Salary", Transaction.amount > 0)
        .group_by(Transaction.merchant_key)
        .order_by(func.count().desc())
        .limit(1)
    )
    if not salary or not portfolio:
        return None
    token = salary.split()[0].lower()
    if len(token) < 4:
        return None

    value = 0.0
    matched: list[str] = []
    for p in positions:
        if token in (p["name"] or "").lower() or token in (p["ticker"] or "").lower():
            value += p["value"] or 0
            matched.append(p["ticker"] or p["name"] or "?")
    for a in accounts:
        if a["subtype"] == "stock plan":
            value += a["balance"] or 0
            matched.append(a["name"])
    if value <= 0:
        return None
    return {
        "employer": token.title(),
        "value": round(value, 2),
        "share": round(value / portfolio * 100, 2),
        "holdings": matched,
    }


@router.get("/card-benefits")
def card_benefits(db: Session = Depends(get_db)) -> dict:
    """Perks per card, with whichever cycle is currently open."""
    if not db.scalar(select(func.count()).select_from(CardBenefit)):
        benefits.seed(db)
    return benefits.listing(db)


class BenefitClaimIn(BaseModel):
    claimed: bool
    note: str | None = None


@router.post("/card-benefits/{benefit_id}/claim")
def claim_benefit(
    benefit_id: int, payload: BenefitClaimIn, db: Session = Depends(get_db)
) -> dict:
    """Tick a benefit off for the cycle it is currently in.

    Claims are recorded against the period rather than the benefit, so ticking this
    year's travel credit leaves last year's record intact and next year starts
    unticked on its own.
    """
    benefit = db.get(CardBenefit, benefit_id)
    if benefit is None:
        raise HTTPException(404, "Benefit not found")
    period, _, _ = benefits.current_period(benefit)
    existing = db.scalar(
        select(BenefitClaim).where(
            BenefitClaim.benefit_id == benefit_id, BenefitClaim.period == period
        )
    )
    if payload.claimed and existing is None:
        db.add(
            BenefitClaim(
                benefit_id=benefit_id, period=period,
                claimed_on=date.today(), note=payload.note,
            )
        )
    elif payload.claimed and existing is not None and payload.note is not None:
        existing.note = payload.note
    elif not payload.claimed and existing is not None:
        db.delete(existing)
    db.commit()
    return {"id": benefit_id, "period": period, "claimed": payload.claimed}


class BenefitIn(BaseModel):
    account_id: int | None = None
    name: str | None = None
    detail: str | None = None
    value: float | None = None
    period_months: int | None = None
    start_month: int | None = None


@router.post("/card-benefits")
def create_benefit(payload: BenefitIn, db: Session = Depends(get_db)) -> dict:
    if not payload.account_id or not (payload.name or "").strip():
        raise HTTPException(400, "A card and a name are required")
    row = CardBenefit(
        account_id=payload.account_id,
        name=payload.name.strip(),
        detail=payload.detail,
        value=payload.value,
        period_months=payload.period_months or 12,
        start_month=payload.start_month or 1,
        anchor_year=date.today().year if (payload.period_months or 12) > 12 else None,
        sort_order=99,
    )
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.patch("/card-benefits/{benefit_id}")
def update_benefit(
    benefit_id: int, payload: BenefitIn, db: Session = Depends(get_db)
) -> dict:
    row = db.get(CardBenefit, benefit_id)
    if row is None:
        raise HTTPException(404, "Benefit not found")
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.detail is not None:
        row.detail = payload.detail
    if payload.value is not None:
        row.value = payload.value
    if payload.period_months is not None:
        row.period_months = payload.period_months
    if payload.start_month is not None:
        row.start_month = payload.start_month
    db.commit()
    return {"id": row.id}


@router.delete("/card-benefits/{benefit_id}")
def delete_benefit(benefit_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(CardBenefit, benefit_id)
    if row is None:
        raise HTTPException(404, "Benefit not found")
    # Claims are the user's own record of what they used, so they go with it.
    db.query(BenefitClaim).filter(BenefitClaim.benefit_id == benefit_id).delete()
    db.delete(row)
    db.commit()
    return {"deleted": benefit_id}


@router.get("/debts")
def list_debts(db: Session = Depends(get_db)) -> dict:
    """Payoff progress, plus payments to a tracked payee that are not yet allocated."""
    debts.link_new_payments(db)
    out = debts.listing(db)
    out["unallocated"] = debts.unallocated(db)
    return out


class DebtIn(BaseModel):
    name: str | None = None
    payee: str | None = None
    detail: str | None = None
    total_amount: float | None = None
    monthly_payment: float | None = None
    annual_rate: float | None = None
    match_merchant: str | None = None
    priority: int | None = None


@router.post("/debts")
def create_debt(payload: DebtIn, db: Session = Depends(get_db)) -> dict:
    if not (payload.name or "").strip() or not payload.total_amount:
        raise HTTPException(400, "A name and a total are required")
    row = Debt(
        name=payload.name.strip(),
        payee=payload.payee,
        detail=payload.detail,
        total_amount=payload.total_amount,
        monthly_payment=payload.monthly_payment or 0,
        annual_rate=payload.annual_rate,
        match_merchant=payload.match_merchant,
        priority=payload.priority or 0,
    )
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.patch("/debts/{debt_id}")
def update_debt(debt_id: int, payload: DebtIn, db: Session = Depends(get_db)) -> dict:
    row = db.get(Debt, debt_id)
    if row is None:
        raise HTTPException(404, "Debt not found")
    for field in ("name", "payee", "detail", "total_amount", "monthly_payment",
                  "annual_rate", "match_merchant", "priority"):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    db.commit()
    return {"id": row.id}


@router.delete("/debts/{debt_id}")
def delete_debt(debt_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(Debt, debt_id)
    if row is None:
        raise HTTPException(404, "Debt not found")
    db.query(DebtPayment).filter(DebtPayment.debt_id == debt_id).delete()
    db.delete(row)
    db.commit()
    return {"deleted": debt_id}


class DebtPaymentIn(BaseModel):
    paid_on: str
    amount: float
    note: str | None = None


@router.post("/debts/{debt_id}/payments")
def add_debt_payment(
    debt_id: int, payload: DebtPaymentIn, db: Session = Depends(get_db)
) -> dict:
    """Record an installment by hand, for payments made before any account was linked."""
    if db.get(Debt, debt_id) is None:
        raise HTTPException(404, "Debt not found")
    row = DebtPayment(
        debt_id=debt_id,
        paid_on=date.fromisoformat(payload.paid_on),
        amount=payload.amount,
        note=payload.note,
    )
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.delete("/debt-payments/{payment_id}")
def delete_debt_payment(payment_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(DebtPayment, payment_id)
    if row is None:
        raise HTTPException(404, "Payment not found")
    db.delete(row)
    db.commit()
    return {"deleted": payment_id}


class AllocateIn(BaseModel):
    debt_id: int
    note: str | None = None


@router.post("/transactions/{txn_id}/allocate")
def allocate_to_debt(
    txn_id: int, payload: AllocateIn, db: Session = Depends(get_db)
) -> dict:
    """Put an odd amount towards a debt.

    The matcher only claims payments that divide into whole installments, so a $500
    top-up on a month whose rent was already paid is left for the user to place.
    """
    txn = db.get(Transaction, txn_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found")
    if db.get(Debt, payload.debt_id) is None:
        raise HTTPException(404, "Debt not found")
    if db.scalar(select(DebtPayment).where(DebtPayment.transaction_id == txn_id)):
        raise HTTPException(400, "Already allocated")
    row = DebtPayment(
        debt_id=payload.debt_id,
        transaction_id=txn_id,
        paid_on=txn.date,
        amount=abs(txn.amount),
        note=payload.note,
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "amount": row.amount}


@router.post("/transactions/{txn_id}/debt-ignore")
def ignore_for_debts(txn_id: int, db: Session = Depends(get_db)) -> dict:
    """Stop offering to file this payment against a debt."""
    if db.get(Transaction, txn_id) is None:
        raise HTTPException(404, "Transaction not found")
    if not db.scalar(
        select(DebtExclusion).where(DebtExclusion.transaction_id == txn_id)
    ):
        db.add(DebtExclusion(transaction_id=txn_id))
        db.commit()
    return {"ignored": txn_id}
