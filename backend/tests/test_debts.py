"""Paying a person back against an agreed total.

The tricky parts are that one payment can settle two debts, that installments made
before any account existed have no transaction behind them, and that a schedule
which stopped over a year ago must not be used to project a payoff date.
"""
from __future__ import annotations

from datetime import date

from talents.models import Debt, DebtPayment, Transaction
from talents.services.debts import link_new_payments, listing, scenarios, unallocated


def make_debt(db, **kw):
    row = Debt(
        name=kw.pop("name", "Debt"),
        total_amount=kw.pop("total_amount", 50_000.0),
        monthly_payment=kw.pop("monthly_payment", 1_000.0),
        match_merchant=kw.pop("match_merchant", "jane doe"),
        **kw,
    )
    db.add(row)
    db.commit()
    return row


def pay(db, account, when: date, amount: float, merchant="Zelle payment to Jane Doe 123"):
    db.add(
        Transaction(
            account_id=account.id, date=when, effective_month=when.strftime("%Y-%m"),
            amount=-abs(amount), merchant_name=merchant, source="test",
        )
    )
    db.commit()


def test_one_payment_can_settle_two_debts(db, account):
    """$5,500 is $4,500 of house and $1,000 of car, not one payment of either."""
    house = make_debt(db, name="House", total_amount=772_610.0, monthly_payment=4_500.0, priority=0)
    car = make_debt(db, name="Car", total_amount=50_000.0, monthly_payment=1_000.0, priority=1)
    pay(db, account, date(2025, 2, 18), 5_500.0)

    assert link_new_payments(db) == 2
    rows = {d["name"]: d for d in listing(db, date(2025, 3, 1))["debts"]}
    assert rows["House"]["paid"] == 4_500.0
    assert rows["Car"]["paid"] == 1_000.0


def test_an_amount_that_does_not_divide_is_left_alone(db, account):
    """$500 fits neither installment, so it is surfaced rather than guessed at."""
    make_debt(db, name="House", monthly_payment=4_500.0, total_amount=772_610.0)
    pay(db, account, date(2026, 3, 10), 500.0)

    assert link_new_payments(db) == 0
    assert listing(db)["debts"][0]["paid"] == 0
    assert [r["amount"] for r in unallocated(db)] == [500.0]


def test_a_transaction_is_only_counted_once(db, account):
    """Re-running the matcher on every request must not double count."""
    make_debt(db, name="House", monthly_payment=4_500.0, total_amount=772_610.0)
    pay(db, account, date(2026, 6, 1), 4_500.0)

    assert link_new_payments(db) == 1
    assert link_new_payments(db) == 0
    assert listing(db)["debts"][0]["paid"] == 4_500.0


def test_installments_are_counted_by_value_not_by_row(db, account):
    """October's house payment went out as $4,000 and $500: one installment, two rows."""
    house = make_debt(db, name="House", monthly_payment=4_500.0, total_amount=772_610.0)
    db.add(DebtPayment(debt_id=house.id, paid_on=date(2024, 10, 1), amount=4_000.0))
    db.add(DebtPayment(debt_id=house.id, paid_on=date(2024, 10, 7), amount=500.0))
    db.commit()

    row = listing(db, date(2024, 11, 1))["debts"][0]
    assert row["payments_made"] == 1
    assert row["entries"] == 2
    assert row["paid"] == 4_500.0


def test_payments_made_before_any_account_existed_still_count(db):
    """The first eleven car installments predate the data and have no transaction."""
    car = make_debt(db, name="Car", total_amount=50_000.0, monthly_payment=1_000.0)
    for i in range(11):
        db.add(DebtPayment(debt_id=car.id, paid_on=date(2023, 8, 1), amount=1_000.0))
    db.commit()

    row = listing(db, date(2024, 1, 1))["debts"][0]
    assert row["paid"] == 11_000.0
    assert row["remaining"] == 39_000.0
    assert row["payments_made"] == 11


def test_a_stalled_debt_gets_no_payoff_date(db):
    """Projecting from a schedule that stopped 15 months ago would be fiction."""
    car = make_debt(db, name="Car", total_amount=50_000.0, monthly_payment=1_000.0)
    db.add(DebtPayment(debt_id=car.id, paid_on=date(2025, 5, 7), amount=1_000.0))
    db.commit()

    row = listing(db, date(2026, 8, 9))["debts"][0]
    assert row["paying"] is False
    assert row["months_since_last"] == 15
    assert row["projected_payoff"] is None


def test_an_active_debt_projects_a_payoff_date(db):
    house = make_debt(db, name="House", total_amount=13_500.0, monthly_payment=4_500.0)
    db.add(DebtPayment(debt_id=house.id, paid_on=date(2026, 8, 3), amount=4_500.0))
    db.commit()

    row = listing(db, date(2026, 8, 9))["debts"][0]
    assert row["paying"] is True
    assert row["payments_left"] == 2
    assert row["projected_payoff"] == "2026-10-01"


def test_only_debts_being_paid_count_towards_the_monthly_commitment(db):
    house = make_debt(db, name="House", total_amount=772_610.0, monthly_payment=4_500.0)
    car = make_debt(db, name="Car", total_amount=50_000.0, monthly_payment=1_000.0)
    db.add(DebtPayment(debt_id=house.id, paid_on=date(2026, 8, 3), amount=4_500.0))
    db.add(DebtPayment(debt_id=car.id, paid_on=date(2025, 5, 7), amount=1_000.0))
    db.commit()

    out = listing(db, date(2026, 8, 9))
    assert out["monthly_committed"] == 4_500.0


def test_an_ignored_payment_stops_being_offered(db, account):
    """Settling up over something unrelated should not nag forever."""
    from talents.models import DebtExclusion

    make_debt(db, name="House", monthly_payment=4_500.0, total_amount=772_610.0)
    pay(db, account, date(2026, 3, 10), 500.0)
    txn_id = unallocated(db)[0]["id"]

    db.add(DebtExclusion(transaction_id=txn_id))
    db.commit()
    assert unallocated(db) == []
    # Ignoring is not the same as paying: nothing was credited to the debt.
    assert listing(db)["debts"][0]["paid"] == 0


def test_interest_is_charged_every_month_against_the_balance(db):
    """One month at 4.5% on $822,402.29 is $3,084.01, so $4,500 buys $1,415.99."""
    house = make_debt(
        db, name="House", total_amount=822_402.29, monthly_payment=4_500.0, annual_rate=4.5
    )
    db.add(DebtPayment(debt_id=house.id, paid_on=date(2024, 10, 1), amount=4_500.0))
    db.commit()

    row = listing(db, date(2024, 10, 15))["debts"][0]
    assert row["paid"] == 4_500.0
    assert row["interest_paid"] == 3_084.01
    assert row["principal_paid"] == 1_415.99
    assert row["remaining"] == 820_986.30


def test_a_month_with_no_payment_still_costs_interest(db):
    """The whole point of a rate: pausing does not pause what is owed."""
    house = make_debt(
        db, name="House", total_amount=100_000.0, monthly_payment=1_000.0, annual_rate=12.0
    )
    db.add(DebtPayment(debt_id=house.id, paid_on=date(2025, 1, 1), amount=1_000.0))
    db.commit()

    # 1% of 100,000 is exactly the payment, so January leaves the balance untouched
    # and February — nothing paid — adds another 1,000 on top.
    assert listing(db, date(2025, 1, 20))["debts"][0]["remaining"] == 100_000.0
    assert listing(db, date(2025, 2, 20))["debts"][0]["remaining"] == 101_000.0


def test_a_payment_that_cannot_cover_the_interest_never_clears(db):
    """No number of $500 payments clears $100,000 at 12%, so no date is invented."""
    house = make_debt(
        db, name="House", total_amount=100_000.0, monthly_payment=500.0, annual_rate=12.0
    )
    db.add(DebtPayment(debt_id=house.id, paid_on=date(2025, 1, 1), amount=500.0))
    db.commit()

    row = listing(db, date(2025, 1, 20))["debts"][0]
    assert row["payments_left"] is None
    assert row["projected_payoff"] is None


def test_payments_left_follows_the_amortisation_not_the_division(db):
    """$822,402.29 at 4.5% takes 309 payments of $4,500, not the 183 of plain division."""
    house = make_debt(
        db, name="House", total_amount=822_402.29, monthly_payment=4_500.0, annual_rate=4.5
    )
    db.add(DebtPayment(debt_id=house.id, paid_on=date(2024, 10, 1), amount=4_500.0))
    db.commit()

    row = listing(db, date(2024, 10, 15))["debts"][0]
    # One made, 308 to go. Plain division on the same balance would say 183.
    assert row["payments_left"] == 308
    assert row["projected_payoff"] == "2050-06-01"


def test_a_debt_without_a_rate_is_still_simple_subtraction(db):
    """The car carries no interest; adding rates must not change how it reads."""
    car = make_debt(db, name="Car", total_amount=50_000.0, monthly_payment=1_000.0)
    db.add(DebtPayment(debt_id=car.id, paid_on=date(2025, 5, 7), amount=1_000.0))
    db.commit()

    row = listing(db, date(2025, 5, 20))["debts"][0]
    assert row["rate"] is None
    assert row["interest_paid"] == 0
    assert row["remaining"] == 49_000.0
    assert row["payments_left"] == 49


def test_paying_more_is_only_offered_where_there_is_interest_to_save(db):
    """Without a rate, paying more saves nothing — offering a saving would be a lie."""
    car = make_debt(db, name="Car", total_amount=50_000.0, monthly_payment=1_000.0)
    db.add(DebtPayment(debt_id=car.id, paid_on=date(2025, 5, 7), amount=1_000.0))
    db.commit()

    assert listing(db, date(2025, 5, 20))["debts"][0]["scenarios"] == []


def test_paying_more_shortens_the_term_and_the_interest(db):
    """$500 a month more on the house is 46 months and $90,511 of interest."""
    rows = {
        s["extra"]: s
        for s in scenarios(786_998.14, 4_500.0, 4.5, date(2026, 9, 2))
    }
    # The do-nothing row anchors the comparison and must save nothing.
    assert rows[0.0]["months"] == 285
    assert rows[0.0]["saved"] == 0
    assert rows[0.0]["payoff"] == "2050-06-01"
    assert rows[500.0]["months"] == 239
    assert rows[500.0]["saved"] == 90_510.87
    assert rows[500.0]["months_earlier"] == 46
