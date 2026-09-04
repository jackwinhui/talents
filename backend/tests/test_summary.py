"""Summary rollups.

The dashboard reads its figures from here, so the rules that decide what counts
as spending are pinned down by tests rather than by inspection.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from talents.models import Category, Transaction
from talents.routers.data import summary


def add(db, account, when: date, amount: float, category: str | None = None, transfer=False):
    cat = db.scalar(select(Category).where(Category.name == category)) if category else None
    db.add(
        Transaction(
            account_id=account.id,
            date=when,
            effective_month=when.strftime("%Y-%m"),
            amount=amount,
            merchant_name="Test",
            category_id=cat.id if cat else None,
            is_transfer=transfer,
            source="test",
        )
    )
    db.commit()


def test_current_month_is_bucketed_by_day(db, account):
    """A single month is one point when bucketed by month, so days are needed too.

    Selecting "This month" plots a running total within the month, which only
    works if the API breaks the current month down by date.
    """
    today = date.today()
    first = today.replace(day=1)
    add(db, account, first, -100.0, "Groceries")
    add(db, account, first, -50.0, "Dining Out")
    add(db, account, today, -25.0, "Groceries")

    out = summary(period="current", db=db)
    days = {d["date"]: d for d in out["days"]}

    assert days[first.isoformat()]["spent"] == 150.0
    if today != first:
        assert days[today.isoformat()]["spent"] == 25.0
    # The running total has to land on the headline figure, or the chart and the
    # number above it disagree.
    assert round(sum(d["net"] for d in out["days"]), 2) == out["period_totals"]["net"]


def test_days_exclude_transfers(db, account):
    """Card payments are not spending, in the daily view as much as the monthly one."""
    today = date.today().replace(day=1)
    add(db, account, today, -100.0, "Groceries")
    add(db, account, today, -900.0, "Transfers", transfer=True)

    out = summary(period="current", db=db)
    assert out["days"][0]["spent"] == 100.0
    assert out["period_totals"]["transfers"] == 900.0


def test_refund_reduces_its_category_rather_than_counting_as_income(db, account):
    """A returned jacket is not earnings."""
    today = date.today().replace(day=1)
    add(db, account, today, -200.0, "Retail")
    add(db, account, today, 80.0, "Retail")

    out = summary(period="current", db=db)
    assert out["period_totals"]["spent"] == 120.0
    assert out["period_totals"]["income"] == 0.0


def test_transfer_flag_and_category_cannot_disagree(db, account):
    """One is read by the summary, the other by recurring detection and insights.

    A Fidelity stock-sale credit was flagged as a transfer - so correctly kept out
    of income - while still displaying as "Other Income".
    """
    from talents.db import _sync_transfer_flag

    today = date.today().replace(day=1)
    add(db, account, today, 9345.50, "Other Income", transfer=True)
    db.commit()

    _sync_transfer_flag()
    db.expire_all()

    txn = db.scalar(select(Transaction))
    assert db.get(Category, txn.category_id).name == "Transfers"
    assert txn.is_transfer is True

    out = summary(period="current", db=db)
    assert out["period_totals"]["income"] == 0.0


def test_filtered_transactions_are_summed_across_every_page(db, account):
    """Answering "how much have I paid this person" should not mean adding up rows.

    The sum covers everything the filter matches, not just the page on screen.
    """
    from talents.routers.data import list_transactions

    for i in range(3):
        add(db, account, date(2026, 3, i + 1), -4500.0, "Rent/Mortgage")
    add(db, account, date(2026, 3, 9), 1000.0, "Salary")
    for txn in db.scalars(select(Transaction)).all():
        txn.merchant_name = "Zelle payment to Jane Doe" if txn.amount < 0 else "Payroll"
    db.commit()

    out = list_transactions(limit=1, search="jane doe", db=db)
    assert out["total"] == 3
    assert len(out["items"]) == 1  # one page
    assert out["sum_out"] == 13500.0  # but summed over all three
    assert out["sum_in"] == 0.0

    every = list_transactions(limit=100, db=db)
    assert every["sum_out"] == 13500.0
    assert every["sum_in"] == 1000.0
