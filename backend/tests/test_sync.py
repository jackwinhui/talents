"""Sync conventions and net worth arithmetic."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from talents.models import Account, Institution, Transaction
from talents.services.sync import _hash_key, snapshot_net_worth


def test_hash_key_is_stable_across_reference_noise():
    """The same spend must hash alike even when the description carries a new id."""
    a = _hash_key(1, date(2026, 8, 3), -4500.0, "Zelle payment to Jane Doe 29827669250")
    b = _hash_key(1, date(2026, 8, 3), -4500.0, "Zelle payment to Jane Doe 11112222333")
    assert a == b


def test_hash_key_separates_different_accounts():
    a = _hash_key(1, date(2026, 8, 3), -20.0, "Trader Joe's")
    b = _hash_key(2, date(2026, 8, 3), -20.0, "Trader Joe's")
    assert a != b


def test_identical_charges_may_share_a_hash(db, account):
    """Two genuine charges can collide, which is why hash_key is not a unique key.

    Enforcing uniqueness rejected legitimate history on import.
    """
    for _ in range(2):
        db.add(
            Transaction(
                account_id=account.id, date=date(2026, 8, 3),
                effective_month="2026-08", amount=-6.40,
                merchant_name="PARKMOBILE", raw_description="PARKMOBILE",
                merchant_key="parkmobile", source="test",
                hash_key=_hash_key(account.id, date(2026, 8, 3), -6.40, "PARKMOBILE"),
            )
        )
    db.commit()
    assert db.scalar(select(Transaction).where(Transaction.merchant_key == "parkmobile"))
    assert len(db.scalars(select(Transaction)).all()) == 2


def test_net_worth_subtracts_card_balances(db):
    inst = Institution(name="Bank", provider="plaid")
    db.add(inst)
    db.flush()
    db.add(Account(institution_id=inst.id, name="Checking", type="depository",
                   is_asset=True, current_balance=5000.0))
    db.add(Account(institution_id=inst.id, name="Card", type="credit",
                   is_asset=False, current_balance=1200.0))
    db.commit()

    snap = snapshot_net_worth(db)
    assert snap.total_assets == 5000.0
    assert snap.total_liabilities == 1200.0
    assert snap.net_worth == 3800.0


def test_inactive_accounts_are_left_out_of_net_worth(db):
    """A retired card exists for history only and must not move today's figures."""
    inst = Institution(name="Bank", provider="plaid")
    db.add(inst)
    db.flush()
    db.add(Account(institution_id=inst.id, name="Checking", type="depository",
                   is_asset=True, current_balance=1000.0))
    db.add(Account(institution_id=inst.id, name="Bilt Card", type="credit",
                   is_asset=False, current_balance=800.0, is_active=False))
    db.commit()

    snap = snapshot_net_worth(db)
    assert snap.total_liabilities == 0.0
    assert snap.net_worth == 1000.0
