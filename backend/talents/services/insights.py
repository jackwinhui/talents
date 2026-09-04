"""Savings advice.

Deterministic rules rather than a model, so every figure on screen can be traced to
a specific set of transactions. Each insight carries an estimated monthly saving so
the advice is actionable rather than merely descriptive.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, Budget, Category, RecurringSeries, Transaction

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def generate(db: Session) -> list[dict]:
    today = date.today()
    this_month = _month_key(today)
    out: list[dict] = []

    transfers = db.scalar(select(Category.id).where(Category.name == "Transfers"))
    rows = db.execute(
        select(Transaction, Category)
        .join(Category, Transaction.category_id == Category.id, isouter=True)
        .where(Transaction.amount < 0, Transaction.is_transfer.is_(False))
    ).all()

    by_cat_month: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    # Recurring spend that has already landed this month. It must not be extrapolated:
    # rent is paid once, so scaling it by the remaining days invents money.
    fixed_this_month: dict[str, float] = defaultdict(float)
    for txn, cat in rows:
        if cat and transfers and cat.id == transfers:
            continue
        name = cat.name if cat else "Other"
        month = txn.effective_month or _month_key(txn.date)
        by_cat_month[name][month] += -txn.amount
        if month == this_month and txn.recurring_series_id is not None:
            fixed_this_month[name] += -txn.amount

    # 1. Categories running above their own recent average.
    for name, months in by_cat_month.items():
        current = months.get(this_month, 0.0)
        prior = [v for m, v in months.items() if m != this_month]
        if len(prior) < 2 or current <= 0:
            continue
        avg = statistics.mean(prior)
        if avg > 0 and current > avg * 1.25 and current - avg > 40:
            out.append({
                "type": "category_spike",
                "severity": "high" if current > avg * 1.6 else "medium",
                "title": f"{name} is running hot",
                "body": (
                    f"You have spent ${current:,.0f} on {name} this month against an average "
                    f"of ${avg:,.0f}. Returning to your usual level frees about "
                    f"${current - avg:,.0f}."
                ),
                "estimated_monthly_savings": round(current - avg, 2),
            })

    # 2. Dining out relative to groceries.
    dining = by_cat_month.get("Dining Out", {}).get(this_month, 0.0)
    groceries = by_cat_month.get("Groceries", {}).get(this_month, 0.0)
    if dining > 150 and dining > groceries * 1.5:
        target = max(groceries, dining * 0.6)
        out.append({
            "type": "dining_ratio",
            "severity": "medium",
            "title": "Dining out is outpacing groceries",
            "body": (
                f"${dining:,.0f} on dining out versus ${groceries:,.0f} on groceries this month. "
                f"Shifting even a few meals home is worth roughly ${dining - target:,.0f}."
            ),
            "estimated_monthly_savings": round(dining - target, 2),
        })

    # 3. Subscriptions, which are easy to forget and easy to cancel.
    subs = db.scalars(
        select(RecurringSeries).where(RecurringSeries.status == "active")
    ).all()
    sub_cat = db.scalar(select(Category.id).where(Category.name == "Subscriptions"))
    sub_series = [s for s in subs if s.category_id == sub_cat]
    if sub_series:
        total = sum(s.expected_amount or 0 for s in sub_series)
        if total > 30:
            names = ", ".join(s.display_name for s in sub_series[:4])
            out.append({
                "type": "subscriptions",
                "severity": "medium" if total > 100 else "low",
                "title": f"{len(sub_series)} subscriptions costing ${total:,.0f}/mo",
                "body": (
                    f"{names}. Canceling the ones you no longer use is the simplest saving "
                    f"available, and it recurs every month."
                ),
                "estimated_monthly_savings": round(total * 0.3, 2),
            })

    # 4. Dormant recurring charges: still billing, not seen recently.
    stale = [
        s for s in subs
        if s.last_seen_date and s.cadence == "monthly"
        and s.last_seen_date < today - timedelta(days=75)
    ]
    for s in stale:
        out.append({
            "type": "zombie",
            "severity": "medium",
            "title": f"{s.display_name} may be dormant",
            "body": (
                f"Last charged {s.last_seen_date:%d %b} at ${s.expected_amount:,.0f}. "
                "Worth confirming it is still wanted before it renews."
            ),
            "estimated_monthly_savings": round(s.expected_amount or 0, 2),
        })

    # 5. Interest actually being paid on revolving balances.
    for acct in db.scalars(select(Account).where(Account.is_asset.is_(False))).all():
        balance = acct.current_balance or 0
        if balance > 500:
            out.append({
                "type": "card_balance",
                "severity": "high" if balance > 2000 else "medium",
                "title": f"{acct.name} is carrying ${balance:,.0f}",
                "body": (
                    "Card interest is usually the most expensive money you borrow. At a typical "
                    f"22% APR this balance costs about ${balance * 0.22 / 12:,.0f} a month while "
                    "it is outstanding."
                ),
                "estimated_monthly_savings": round(balance * 0.22 / 12, 2),
            })

    # 6. Budgets: report what has already happened, and only project once there is
    #    enough of the month to project from.
    day = today.day
    last_day = ((today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
    remaining = last_day - day
    for budget, cat in db.execute(
        select(Budget, Category).join(Category, Budget.category_id == Category.id)
    ).all():
        if budget.month not in (None, this_month) or not budget.amount:
            continue
        spent = by_cat_month.get(cat.name, {}).get(this_month, 0.0)

        if spent > budget.amount:
            out.append({
                "type": "budget_overrun",
                "severity": "high",
                "title": f"{cat.name} is over budget",
                "body": (
                    f"${spent:,.0f} spent against a ${budget.amount:,.0f} budget, "
                    f"${spent - budget.amount:,.0f} over with {remaining} days still to go."
                ),
                "estimated_monthly_savings": round(spent - budget.amount, 2),
            })
            continue

        # Fixed costs have already been paid in full, so only the variable part is
        # extrapolated. Before the month is a third gone the estimate is too noisy.
        variable = max(0.0, spent - fixed_this_month.get(cat.name, 0.0))
        if day < 10 or variable <= 0:
            continue
        projected = fixed_this_month.get(cat.name, 0.0) + variable * (last_day / day)
        if projected > budget.amount * 1.15:
            out.append({
                "type": "budget_pace",
                "severity": "medium",
                "title": f"{cat.name} is on pace to overrun",
                "body": (
                    f"${spent:,.0f} of ${budget.amount:,.0f} used by day {day}, tracking to "
                    f"about ${projected:,.0f} by month end."
                ),
                "estimated_monthly_savings": round(projected - budget.amount, 2),
            })

    # 7. Savings rate, stated plainly.
    income_rows = db.scalars(
        select(Transaction).where(Transaction.amount > 0, Transaction.is_transfer.is_(False))
    ).all()
    income = sum(t.amount for t in income_rows if (t.effective_month or "") == this_month)
    spent_total = sum(v.get(this_month, 0.0) for v in by_cat_month.values())
    if income > 0:
        rate = (income - spent_total) / income * 100
        if rate < 20:
            out.append({
                "type": "savings_rate",
                "severity": "high" if rate < 5 else "medium",
                "title": f"Savings rate is {rate:.0f}%",
                "body": (
                    f"You kept ${income - spent_total:,.0f} of ${income:,.0f} earned this month. "
                    "Reaching 20% would mean setting aside "
                    f"${income * 0.2 - (income - spent_total):,.0f} more."
                ),
                "estimated_monthly_savings": round(max(0.0, income * 0.2 - (income - spent_total)), 2),
            })

    out.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 3), -i["estimated_monthly_savings"]))
    return out
