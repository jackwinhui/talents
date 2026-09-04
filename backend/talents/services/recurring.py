"""Detect recurring bills, and surface the ones that have not been paid.

Detection is deliberately conservative: a series needs at least three sightings at a
consistent interval and a consistent amount before it is trusted. Merchant names are
normalized first (see categorizer.normalize_merchant) because descriptions such as
"Zelle payment to Jane Doe 29437791268" carry a different reference every month and
would otherwise look like a new merchant each time.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PendingObligation, RecurringSeries, Transaction

CADENCES = [("weekly", 7), ("biweekly", 14), ("monthly", 30), ("quarterly", 91), ("annual", 365)]
MIN_OCCURRENCES = 3
# A monthly bill drifts with weekends and month lengths, so intervals are matched
# proportionally rather than to an exact day count.
INTERVAL_TOLERANCE = 0.25
AMOUNT_TOLERANCE = 0.15


def _classify(median_gap: float) -> tuple[str, int] | None:
    for name, days in CADENCES:
        if abs(median_gap - days) <= days * INTERVAL_TOLERANCE:
            return name, days
    return None


def detect(db: Session) -> int:
    """Rebuild recurring series from transaction history. Returns series found."""
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for txn in db.scalars(
        select(Transaction).where(Transaction.amount < 0, Transaction.is_transfer.is_(False))
    ).all():
        key = txn.merchant_key or ""
        if key:
            groups[key].append(txn)

    # Detection rebuilds every series, so remember which merchants the user has
    # already said are not bills. Without this a rejected false positive returns on
    # the next sync.
    rejected = {
        r.merchant_key
        for r in db.scalars(
            select(RecurringSeries).where(RecurringSeries.status == "rejected")
        ).all()
    }
    # Canceled is not the same as rejected: the bill was real, it is just no longer
    # paid - an insurer switched, a subscription closed. The date it was last seen
    # when canceled is kept so that resuming payments revives the series instead of
    # silently hiding it forever.
    canceled = {
        r.merchant_key: r.last_seen_date
        for r in db.scalars(
            select(RecurringSeries).where(RecurringSeries.status == "canceled")
        ).all()
    }

    db.query(PendingObligation).delete()
    db.query(RecurringSeries).delete()
    db.commit()

    found = 0
    for key, txns in groups.items():
        if len(txns) < MIN_OCCURRENCES:
            continue
        txns.sort(key=lambda t: t.date)
        dates = [t.date for t in txns]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
        if len(gaps) < MIN_OCCURRENCES - 1:
            continue

        median_gap = statistics.median(gaps)
        classified = _classify(median_gap)
        if not classified:
            continue
        cadence, period = classified

        amounts = [abs(t.amount) for t in txns]
        expected = statistics.median(amounts)
        if expected <= 0:
            continue
        # Judge consistency by how many payments cluster around the median, not by
        # the worst one. Requiring every amount to agree let a single odd payment to
        # the same payee discard an otherwise obvious bill - one $1,000 transfer was
        # enough to hide $4,500 monthly rent.
        consistent = sum(1 for a in amounts if abs(a - expected) / expected <= AMOUNT_TOLERANCE)
        share_consistent = consistent / len(amounts)
        if share_consistent < 0.6:
            continue

        gap_spread = (
            statistics.pstdev(gaps) / median_gap if len(gaps) > 1 and median_gap else 0.0
        )
        confidence = max(0.0, min(1.0, (1 - gap_spread) * 0.6 + share_consistent * 0.4))

        last = txns[-1]
        # A series unseen for several cycles has ended rather than fallen behind.
        # Two years of history contains many closed subscriptions and a retired
        # card, and calling those overdue would invent debts that do not exist.
        cycles_missed = (date.today() - last.date).days / period
        if key in rejected:
            status = "rejected"
        elif key in canceled and not (
            canceled[key] and last.date > canceled[key]
        ):
            # Still canceled. A payment newer than the one it was canceled on
            # means it has restarted, so fall through and let it go active again.
            status = "canceled"
        elif cycles_missed > 3:
            status = "ended"
        else:
            status = "active"
        # The raw name still carries the per-payment reference, so present the
        # normalized key instead: "Zelle payment to Jane Doe 2982..." -> "Zelle Payment To Jane Doe".
        raw_name = (last.merchant_name or last.raw_description or key).strip()
        # Trim the trailing per-payment reference while keeping the original casing:
        # "Zelle payment to Jane Doe 29827669250" -> "Zelle payment to Jane Doe".
        lowered = raw_name.lower()
        if key and lowered.startswith(key) and len(lowered) > len(key):
            display = raw_name[: len(key)].strip()
        elif key and key not in lowered:
            display = key.title()
        else:
            display = raw_name
        series = RecurringSeries(
            merchant_key=key,
            display_name=display,
            category_id=last.category_id,
            account_id=last.account_id,
            cadence=cadence,
            expected_amount=round(expected, 2),
            last_seen_date=last.date,
            next_due_date=last.date + timedelta(days=period),
            status=status,
            confidence=round(confidence, 2),
        )
        db.add(series)
        db.flush()
        found += 1

        for txn in txns:
            txn.recurring_series_id = series.id

        # Overdue by more than a grace period: treat as incurred but unsettled rather
        # than as an error. A late rent payment is still owed.
        grace = max(5, int(period * 0.35))
        if (
            series.status == "active"
            and series.next_due_date
            and series.next_due_date < date.today() - timedelta(days=grace)
        ):
            db.add(
                PendingObligation(
                    recurring_series_id=series.id,
                    expected_period=series.next_due_date.strftime("%Y-%m"),
                    expected_amount=series.expected_amount,
                    expected_date=series.next_due_date,
                    status="outstanding",
                )
            )

    db.commit()
    return found


def upcoming(db: Session, days: int = 45) -> list[dict]:
    horizon = date.today() + timedelta(days=days)
    rows = db.scalars(
        select(RecurringSeries)
        .where(RecurringSeries.status == "active")
        .order_by(RecurringSeries.next_due_date.asc())
    ).all()
    return [
        {
            "id": r.id,
            "name": r.display_name,
            "cadence": r.cadence,
            "amount": r.expected_amount,
            "next_due": r.next_due_date.isoformat() if r.next_due_date else None,
            "last_seen": r.last_seen_date.isoformat() if r.last_seen_date else None,
            "confidence": r.confidence,
        }
        for r in rows
        if r.next_due_date and r.next_due_date <= horizon
    ]


def rejected(db: Session) -> list[dict]:
    rows = db.scalars(
        select(RecurringSeries).where(RecurringSeries.status == "rejected")
    ).all()
    return [
        {"id": r.id, "name": r.display_name, "cadence": r.cadence,
         "amount": r.expected_amount, "next_due": None, "last_seen": None,
         "confidence": r.confidence}
        for r in rows
    ]


def canceled(db: Session) -> list[dict]:
    """Bills that were real but are no longer paid, so they can be restored."""
    rows = db.scalars(
        select(RecurringSeries).where(RecurringSeries.status == "canceled")
    ).all()
    return [
        {"id": r.id, "name": r.display_name, "cadence": r.cadence,
         "amount": r.expected_amount, "next_due": None,
         "last_seen": r.last_seen_date.isoformat() if r.last_seen_date else None,
         "confidence": r.confidence}
        for r in rows
    ]


def outstanding(db: Session) -> list[dict]:
    rows = db.execute(
        select(PendingObligation, RecurringSeries)
        .join(RecurringSeries, PendingObligation.recurring_series_id == RecurringSeries.id)
        .where(PendingObligation.status == "outstanding")
    ).all()
    return [
        {
            "id": o.id,
            "series_id": s.id,
            "name": s.display_name,
            "period": o.expected_period,
            "amount": o.expected_amount,
            "expected_date": o.expected_date.isoformat() if o.expected_date else None,
        }
        for o, s in rows
    ]


def monthly_total(db: Session) -> float:
    """Everything recurring, normalized to a monthly figure."""
    per_month = {"weekly": 52 / 12, "biweekly": 26 / 12, "monthly": 1, "quarterly": 1 / 3,
                 "annual": 1 / 12}
    total = 0.0
    for r in db.scalars(select(RecurringSeries).where(RecurringSeries.status == "active")).all():
        total += (r.expected_amount or 0) * per_month.get(r.cadence, 1)
    return round(total, 2)
