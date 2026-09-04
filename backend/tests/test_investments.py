"""Investment holdings.

Cost basis is the fragile part: Plaid returns NULL for plan-administered Fidelity
funds, and treating an unknown basis as zero would invent a large fake profit.
"""
from __future__ import annotations

from datetime import date

from talents.models import Account, Holding, Security
from talents.routers.data import investments


def brokerage(db, name="Individual - TOD", subtype="brokerage", balance=100.0):
    acct = Account(
        name=name, type="investment", subtype=subtype,
        is_asset=True, is_active=True, current_balance=balance,
    )
    db.add(acct)
    db.flush()
    return acct


def hold(db, acct, ticker, qty, value, basis, price=None, sec_type="equity"):
    sec = Security(ticker=ticker, name=f"{ticker} Inc", type=sec_type, close_price=price)
    db.add(sec)
    db.flush()
    db.add(
        Holding(
            account_id=acct.id, security_id=sec.id, quantity=qty,
            cost_basis=basis, institution_value=value, as_of_date=date.today(),
        )
    )
    db.commit()


def test_unknown_cost_basis_does_not_invent_a_gain(db):
    """Fidelity reports no basis for 401(k) blended funds.

    Counting that as a zero basis would have reported the whole balance as profit.
    """
    acct = brokerage(db, balance=114_783.01)
    hold(db, acct, "MSFT", 10, 5000.0, 4000.0, price=500.0)
    hold(db, acct, "BTC.LPATH.IDX.2065.M", 6045.0, 109_783.01, None)

    out = investments(db=db)
    assert out["unrealized_gain"] == 1000.0
    assert out["cost_basis_known"] == 4000.0
    assert out["unpriced_value"] == 109_783.01
    unpriced = next(p for p in out["positions"] if p["ticker"].startswith("BTC"))
    assert unpriced["gain"] is None


def test_a_loss_is_reported_as_a_loss(db):
    acct = brokerage(db, balance=7278.68)
    hold(db, acct, "META", 12.293, 7278.68, 9075.68, price=592.1)

    out = investments(db=db)
    assert out["unrealized_gain"] == -1797.0
    assert out["positions"][0]["gain_pct"] < 0


def test_same_holding_in_two_accounts_is_one_position_for_concentration(db):
    """SOXX sits in both the brokerage and the HSA, so per-row shares understate it."""
    a = brokerage(db, name="Individual - TOD", balance=23_946.25)
    b = brokerage(db, name="Health Savings Account", subtype="hsa", balance=3482.90)
    hold(db, a, "SOXX", 44.078, 23_946.25, 16_685.44, price=543.27, sec_type="etf")
    hold(db, b, "SOXX", 6.411, 3482.90, 3072.60, price=543.27, sec_type="etf")

    out = investments(db=db)
    soxx = [t for t in out["by_ticker"] if t["ticker"] == "SOXX"]
    assert len(soxx) == 1
    assert soxx[0]["value"] == 27_429.15
    assert len(out["positions"]) == 2


def test_account_balance_beats_the_sum_of_positions(db):
    """An ESPP account mid-purchase holds cash the broker reports no position for."""
    brokerage(db, name="MICROSOFT ESPP PLAN", subtype="stock plan", balance=2110.0)

    out = investments(db=db)
    assert out["portfolio_value"] == 2110.0
    assert out["holdings_value"] == 0.0
    assert out["accounts"][0]["uninvested"] == 2110.0
