"""Paying a person back against an agreed total.

Different from a recurring bill, which goes on forever. Here the question is how
much of the total is left, and — since payments have been paused for months at a
time — whether anything is being paid at all.

Where a debt carries a rate, what is left is not the total minus what was paid:
interest accrues every month, including the months nothing went out, so the
balance is replayed month by month rather than subtracted.

Payments are stored as explicit rows rather than derived on the fly, because the
earliest installments were made long before any account was connected and exist
only as something the user knows happened.
"""
from __future__ import annotations

import math
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Debt, DebtExclusion, DebtPayment, Transaction


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def _add_months(when: date, months: int) -> date:
    total = when.year * 12 + (when.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _amortise(
    principal: float, payments: list[DebtPayment], annual_rate: float, today: date
) -> tuple[float, float]:
    """Replay the payments against the balance, charging interest every month.

    Returns the balance still owed and the interest charged to get there. Interest
    accrues whether or not anything was paid that month, which is the point of
    tracking a rate at all: the months nothing went out still cost money, and a
    payment is worth far less than its face value against the total.
    """
    rate = annual_rate / 100 / 12
    by_month: dict[tuple[int, int], float] = {}
    for payment in payments:
        key = (payment.paid_on.year, payment.paid_on.month)
        by_month[key] = by_month.get(key, 0.0) + payment.amount

    balance = principal
    interest = 0.0
    for step in range(_months_between(payments[0].paid_on, today) + 1):
        month = _add_months(payments[0].paid_on, step)
        charge = balance * rate
        interest += charge
        balance += charge - by_month.get((month.year, month.month), 0.0)
        if balance <= 0:
            return 0.0, round(interest, 2)
    return round(balance, 2), round(interest, 2)


def _payments_to_clear(balance: float, monthly: float, annual_rate: float) -> int | None:
    """Installments needed to clear the balance, or None if it never clears.

    Without a rate this is simple division. With one, a payment that does not even
    cover the month's interest leaves the balance growing, and no number of them
    ever pays it off - better to say so than to print a date that will never come.
    """
    if balance <= 0:
        return 0
    if monthly <= 0:
        return None
    if not annual_rate:
        return int(-(-balance // monthly))
    rate = annual_rate / 100 / 12
    if monthly <= balance * rate:
        return None
    return math.ceil(-math.log1p(-rate * balance / monthly) / math.log1p(rate))


def _interest_to_clear(balance: float, monthly: float, annual_rate: float) -> float:
    """Interest still to be paid if nothing about the schedule changes."""
    months = _payments_to_clear(balance, monthly, annual_rate)
    if not months or not annual_rate:
        return 0.0
    rate = annual_rate / 100 / 12
    total = 0.0
    for _ in range(months):
        charge = balance * rate
        total += charge
        balance += charge - monthly
        if balance <= 0:
            break
    return round(total, 2)


# Round numbers someone might plausibly free up, rather than a slider nobody moves.
EXTRA_STEPS = (100.0, 250.0, 500.0, 1_000.0)


def scenarios(
    balance: float, monthly: float, annual_rate: float, today: date
) -> list[dict]:
    """What paying a bit more each month is worth, against paying the same forever.

    Only meaningful with a rate: without one, paying more just gets to the same
    total sooner and saves nothing, which the UI would be wrong to dress up as a
    saving. The comparison is always against the current payment, so the first row
    is the do-nothing case and reads as zero.
    """
    if not annual_rate or monthly <= 0 or balance <= 0:
        return []
    base_months = _payments_to_clear(balance, monthly, annual_rate)
    if base_months is None:
        return []
    base_interest = _interest_to_clear(balance, monthly, annual_rate)

    out = []
    for extra in (0.0, *EXTRA_STEPS):
        months = _payments_to_clear(balance, monthly + extra, annual_rate)
        if months is None:
            continue
        interest = _interest_to_clear(balance, monthly + extra, annual_rate)
        out.append({
            "extra": extra,
            "monthly": round(monthly + extra, 2),
            "months": months,
            "payoff": _add_months(today, months).isoformat(),
            "interest": interest,
            "saved": round(base_interest - interest, 2),
            "months_earlier": base_months - months,
        })
    return out


def listing(db: Session, today: date | None = None) -> dict:
    today = today or date.today()
    out = []
    for debt in db.scalars(
        select(Debt).where(Debt.is_active.is_(True)).order_by(Debt.priority, Debt.id)
    ).all():
        payments = db.scalars(
            select(DebtPayment)
            .where(DebtPayment.debt_id == debt.id)
            .order_by(DebtPayment.paid_on)
        ).all()
        paid = round(sum(p.amount for p in payments), 2)
        monthly = debt.monthly_payment or 0
        last = payments[-1].paid_on if payments else None
        rate = debt.annual_rate or 0.0

        if rate and payments:
            remaining, interest = _amortise(debt.total_amount, payments, rate, today)
        else:
            remaining, interest = round(max(debt.total_amount - paid, 0), 2), 0.0
        # What the payments actually bought. With interest running, most of an early
        # installment is rent on the money and never touches the total.
        principal_paid = round(debt.total_amount - remaining, 2)

        # Counted by value, not by row: October's house payment went out as $4,000
        # and $500 on different days, which is one installment and two transactions.
        made = int(round(paid / monthly)) if monthly else len(payments)
        # Whole installments left, rounded up so a part-payment still counts as one.
        left = _payments_to_clear(remaining, monthly, rate) if monthly else None
        # Only meaningful while payments are actually being made; projecting a
        # payoff date from a schedule that stopped 15 months ago is fiction.
        stalled_months = _months_between(last, today) if last else None
        paying = stalled_months is not None and stalled_months <= 2
        payoff = _add_months(today, left) if (paying and left) else None

        out.append({
            "id": debt.id,
            "name": debt.name,
            "payee": debt.payee,
            "detail": debt.detail,
            "total": round(debt.total_amount, 2),
            "monthly": monthly,
            "rate": debt.annual_rate,
            "paid": paid,
            "interest_paid": interest,
            "principal_paid": principal_paid,
            "remaining": remaining,
            "interest_left": _interest_to_clear(remaining, monthly, rate),
            "scenarios": scenarios(remaining, monthly, rate, today),
            "percent": (
                round(principal_paid / debt.total_amount * 100, 1) if debt.total_amount else 0
            ),
            "payments_made": made,
            "entries": len(payments),
            "payments_left": left,
            "last_paid_on": last.isoformat() if last else None,
            "months_since_last": stalled_months,
            "paying": paying,
            "projected_payoff": payoff.isoformat() if payoff else None,
            "payments": [
                {
                    "id": p.id,
                    "paid_on": p.paid_on.isoformat(),
                    "amount": p.amount,
                    "note": p.note,
                    "linked": p.transaction_id is not None,
                }
                for p in reversed(payments)
            ],
        })

    return {
        "debts": out,
        "total_remaining": round(sum(d["remaining"] for d in out), 2),
        "total_paid": round(sum(d["paid"] for d in out), 2),
        "total_interest": round(sum(d["interest_paid"] for d in out), 2),
        "monthly_committed": round(sum(d["monthly"] for d in out if d["paying"]), 2),
    }


def link_new_payments(db: Session, today: date | None = None) -> int:
    """Turn matching transactions into installments, newest first.

    A payment is only split across debts when it divides exactly into whole
    installments — $5,500 is unambiguously $4,500 of house plus $1,000 of car.
    Anything left over is not guessed at; it stays unallocated and visible rather
    than being quietly attached to the nearest debt.
    """
    debts = db.scalars(
        select(Debt)
        .where(Debt.is_active.is_(True), Debt.match_merchant.is_not(None))
        .order_by(Debt.priority, Debt.id)
    ).all()
    if not debts:
        return 0

    linked = {
        row for (row,) in db.execute(
            select(DebtPayment.transaction_id).where(DebtPayment.transaction_id.is_not(None))
        ).all()
    }

    added = 0
    for debt in debts:
        pattern = f"%{debt.match_merchant.lower()}%"
        candidates = db.scalars(
            select(Transaction).where(
                Transaction.amount < 0,
                func.lower(Transaction.merchant_name).like(pattern),
            )
        ).all()
        for txn in candidates:
            if txn.id in linked:
                continue
            amount = abs(txn.amount)
            # Allocate to this debt and any lower-priority debt sharing the payee,
            # but only if the whole amount is accounted for.
            plan: list[tuple[Debt, int]] = []
            rest = amount
            for candidate in debts:
                if candidate.match_merchant.lower() != debt.match_merchant.lower():
                    continue
                units = int(rest // candidate.monthly_payment) if candidate.monthly_payment else 0
                if units:
                    plan.append((candidate, units))
                    rest -= units * candidate.monthly_payment
            if round(rest, 2) != 0 or not plan:
                continue
            for candidate, units in plan:
                for _ in range(units):
                    db.add(
                        DebtPayment(
                            debt_id=candidate.id,
                            transaction_id=txn.id,
                            paid_on=txn.date,
                            amount=candidate.monthly_payment,
                        )
                    )
                    added += 1
            linked.add(txn.id)
    db.commit()
    return added


def unallocated(db: Session) -> list[dict]:
    """Payments to a tracked payee that do not divide into whole installments."""
    debts = db.scalars(
        select(Debt).where(Debt.is_active.is_(True), Debt.match_merchant.is_not(None))
    ).all()
    linked = {
        row for (row,) in db.execute(
            select(DebtPayment.transaction_id).where(DebtPayment.transaction_id.is_not(None))
        ).all()
    }
    linked |= {row for (row,) in db.execute(select(DebtExclusion.transaction_id)).all()}
    seen: dict[int, Transaction] = {}
    for debt in debts:
        for txn in db.scalars(
            select(Transaction).where(
                Transaction.amount < 0,
                func.lower(Transaction.merchant_name).like(f"%{debt.match_merchant.lower()}%"),
            )
        ).all():
            if txn.id not in linked:
                seen[txn.id] = txn
    return [
        {"id": t.id, "date": t.date.isoformat(), "amount": abs(t.amount),
         "merchant": t.merchant_name}
        for t in sorted(seen.values(), key=lambda t: t.date, reverse=True)
    ]
