"""Recurring detection.

These cover the three ways detection went wrong against two years of real history:
an outlier hiding a large bill, closed series being reported as unpaid, and
rejections being undone by the next run.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from talents.models import PendingObligation, RecurringSeries, Transaction
from talents.services.categorizer import normalize_merchant
from talents.services.recurring import detect, monthly_total


def add(db, account, when: date, amount: float, merchant: str) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        date=when,
        effective_month=when.strftime("%Y-%m"),
        amount=amount,
        merchant_name=merchant,
        raw_description=merchant,
        merchant_key=normalize_merchant(merchant),
        source="test",
    )
    db.add(txn)
    return txn


def monthly(db, account, merchant, amount, count, *, end=None, day_jitter=0):
    end = end or date.today()
    for i in range(count):
        when = end - timedelta(days=30 * i + (day_jitter if i % 2 else 0))
        add(db, account, when, -abs(amount), merchant)
    db.commit()


def test_detects_a_simple_monthly_bill(db, account):
    monthly(db, account, "Spotify USA", 7.41, 6)
    assert detect(db) == 1
    series = db.scalar(select(RecurringSeries))
    assert series.cadence == "monthly"
    assert series.expected_amount == 7.41
    assert series.status == "active"


def test_one_odd_payment_does_not_hide_the_bill(db, account):
    """A single $1,000 transfer to the same payee was discarding $4,500 rent.

    Consistency is judged by the cluster around the median, not by the worst case.
    """
    monthly(db, account, "Zelle payment to Jane Doe", 4500, 10)
    add(db, account, date.today() - timedelta(days=45), -1000.0,
        "Zelle payment to Jane Doe 998877")
    db.commit()

    detect(db)
    series = db.scalar(
        select(RecurringSeries).where(RecurringSeries.expected_amount == 4500)
    )
    assert series is not None, "rent must still be detected despite the outlier"
    assert series.status == "active"


def test_two_occurrences_are_not_enough(db, account):
    monthly(db, account, "Random Shop", 20, 2)
    assert detect(db) == 0


def test_long_dormant_series_is_ended_not_overdue(db, account):
    """Closed subscriptions and a retired card must not be reported as debts."""
    monthly(db, account, "Old Subscription", 9.99, 5, end=date.today() - timedelta(days=400))
    detect(db)
    series = db.scalar(select(RecurringSeries))
    assert series.status == "ended"
    assert monthly_total(db) == 0.0


def test_rejection_survives_redetection(db, account):
    """Detection rebuilds every series, so a dismissal has to be remembered."""
    monthly(db, account, "Chipotle", 10.55, 5)
    detect(db)
    series = db.scalar(select(RecurringSeries))
    series.status = "rejected"
    db.commit()

    detect(db)
    again = db.scalar(select(RecurringSeries))
    assert again.status == "rejected"
    assert monthly_total(db) == 0.0


def test_display_name_drops_the_payment_reference(db, account):
    monthly(db, account, "Zelle payment to Jane Doe 29827669250", 4500, 5)
    detect(db)
    series = db.scalar(select(RecurringSeries))
    assert series.display_name == "Zelle payment to Jane Doe"


def test_transfers_are_excluded(db, account):
    monthly(db, account, "Card Payment", 500, 6)
    for txn in db.scalars(select(Transaction)).all():
        txn.is_transfer = True
    db.commit()
    assert detect(db) == 0


def test_canceled_bill_stays_canceled_and_stops_counting(db, account):
    """Switching insurer is not the same as a false positive.

    The bill was real, so it must not be marked "not a bill", but it must leave the
    upcoming list, the monthly total and any outstanding obligation it raised.
    """
    monthly(db, account, "Tesla Property Casualty", 173.55, 5, end=date.today() - timedelta(days=75))
    detect(db)
    series = db.scalar(select(RecurringSeries))
    series.status = "canceled"
    db.commit()

    detect(db)
    again = db.scalar(select(RecurringSeries))
    assert again.status == "canceled"
    assert monthly_total(db) == 0.0
    assert db.scalar(select(PendingObligation)) is None


def test_canceled_bill_revives_when_payments_resume(db, account):
    """Paying it again should bring it back, not leave it hidden for good."""
    monthly(db, account, "Tesla Property Casualty", 173.55, 5, end=date.today() - timedelta(days=75))
    detect(db)
    series = db.scalar(select(RecurringSeries))
    series.status = "canceled"
    db.commit()

    add(db, account, date.today(), -173.55, "Tesla Property Casualty")
    db.commit()
    detect(db)
    assert db.scalar(select(RecurringSeries)).status == "active"


def test_rejected_bill_does_not_revive(db, account):
    """A false positive stays dismissed however many more times it is seen."""
    monthly(db, account, "Chipotle", 10.55, 5)
    detect(db)
    series = db.scalar(select(RecurringSeries))
    series.status = "rejected"
    db.commit()

    add(db, account, date.today(), -10.55, "Chipotle")
    db.commit()
    detect(db)
    assert db.scalar(select(RecurringSeries)).status == "rejected"
