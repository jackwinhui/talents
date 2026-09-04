"""Example: set up two debts owed to a person.

Edit the values below to match your own before running. Nothing here is required
by the app - it is a worked example of the shape a seeded debt takes, including
one that carries interest and one that does not.
Original author's figures have been replaced with round placeholders.

Amounts and the payment schedule came from the user. Where the transactions can
corroborate them they do: $1,000 car installments appear in August and September
2024, stop for October to January exactly as described, resume in February 2025
and stop again after May 2025.

Installments 1 to 11 fall before any account was connected, so they are recorded
by hand from the stated schedule. Everything from August 2024 onwards is matched
against real transactions instead.

Run once:  ../.venv/bin/python scripts/seed_debts.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select  # noqa: E402

from talents.db import SessionLocal, init_db  # noqa: E402
from talents.models import Debt, DebtPayment, Transaction  # noqa: E402
from talents.services import debts as debts_service  # noqa: E402

# The car payments before any account was connected, taken from the "JH Auto
# Payment" sheet. Not a regular series: it opened with two $4,000 payments, and
# generating uniform $1,000 installments from a start date lost $7,000 of it.
CAR_PAYMENTS_BEFORE_DATA = [
    (date(2023, 7, 16), 4_000.0, "Initial payment (Zelle)"),
    (date(2023, 8, 16), 4_000.0, "Instalment 1"),
    (date(2023, 9, 15), 1_000.0, "Instalment 2"),
    (date(2023, 10, 15), 1_000.0, "Instalment 3"),
    (date(2023, 11, 16), 1_000.0, "Instalment 4"),
    # The sheet dates this one 2024-12-15, but it sits between instalments 4 and 6
    # and carries their running total, so the year is a typo for 2023.
    (date(2023, 12, 15), 1_000.0, "Instalment 5"),
    (date(2024, 1, 15), 1_000.0, "Instalment 6"),
    (date(2024, 2, 15), 1_000.0, "Instalment 7"),
    (date(2024, 3, 15), 1_000.0, "Instalment 8"),
    (date(2024, 4, 15), 1_000.0, "Instalment 9"),
    (date(2024, 5, 15), 1_000.0, "Instalment 10"),
    (date(2024, 6, 15), 1_000.0, "Instalment 11"),
]


def main() -> None:
    init_db()
    db = SessionLocal()

    if db.scalar(select(func.count()).select_from(Debt)):
        print("Debts already exist; nothing to do.")
        return

    house = Debt(
        name="123 Example Street, Anytown, ST 00000",
        payee="Jane Doe",
        detail=(
            "House, paid back monthly at 4.5% interest. Original loan amount "
            "$800,000.00 from October 2024 over 30 years."
        ),
        total_amount=800_000.00,
        monthly_payment=4_500.0,
        annual_rate=4.5,
        match_merchant="jane doe",
        priority=0,
    )
    car = Debt(
        name="2023 Tesla Model 3",
        payee="Jane Doe",
        detail=(
            "Car, paid back monthly with no interest. Opened with $4,000 in July 2023 and "
            "another $4,000 in August 2023, then $1,000 a month. Nothing was paid from "
            "October 2024 to January 2025; instalment 15 was February 2025 and 18 was "
            "May 2025, since when it has been paused."
        ),
        total_amount=50_000.0,
        monthly_payment=1_000.0,
        match_merchant="jane doe",
        priority=1,
    )
    db.add_all([house, car])
    db.flush()

    # The house payment for October 2024 went out as $4,000 on the 1st and $500 on
    # the 7th. Neither is a whole installment, so the matcher will not claim them and
    # they are recorded here against the earlier transaction.
    for when, amount in ((date(2024, 10, 1), 4_000.0), (date(2024, 10, 7), 500.0)):
        txn = db.scalar(
            select(Transaction).where(
                Transaction.date == when,
                func.lower(Transaction.merchant_name).like("%jane doe%"),
            )
        )
        db.add(
            DebtPayment(
                debt_id=house.id,
                transaction_id=txn.id if txn else None,
                paid_on=when,
                amount=amount,
                note="Part of the October 2024 installment, paid in two goes.",
            )
        )

    for when, amount, label in CAR_PAYMENTS_BEFORE_DATA:
        db.add(
            DebtPayment(
                debt_id=car.id,
                paid_on=when,
                amount=amount,
                note=f"{label}, before the accounts were connected.",
            )
        )
    db.commit()

    linked = debts_service.link_new_payments(db)
    print(f"Created 2 debts, recorded {len(CAR_PAYMENTS_BEFORE_DATA)} earlier car payments, "
          f"linked {linked} payments from transactions.")

    for row in debts_service.listing(db)["debts"]:
        print(
            f"  {row['name'][:44]:44} {row['payments_made']:>3} paid  "
            f"${row['paid']:>10,.2f} of ${row['total']:>10,.2f}  "
            f"({row['percent']}%)  ${row['remaining']:,.2f} left"
        )
    left_over = debts_service.unallocated(db)
    if left_over:
        print("\nNot allocated to a debt:")
        for row in left_over:
            print(f"  {row['date']}  ${row['amount']:,.2f}  {row['merchant'][:50]}")


if __name__ == "__main__":
    main()
