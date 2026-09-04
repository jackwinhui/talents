"""Card benefit cycles.

The reset windows do not agree with each other: monthly DoorDash credits, a travel
credit on the cardmember anniversary, a Costco certificate that dies on 31 December,
and a Global Entry credit every four years. One calculation has to cover all of it.
"""
from __future__ import annotations

from datetime import date

from talents.models import Account, BenefitClaim, CardBenefit
from talents.services.benefits import current_period, listing, period_label, seed


def card(db, name="Venture X"):
    acct = Account(name=name, type="credit", is_asset=False, is_active=True, mask="6242")
    db.add(acct)
    db.flush()
    return acct


def benefit(db, acct, **kw):
    row = CardBenefit(
        account_id=acct.id, name=kw.pop("name", "Credit"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def test_monthly_credit_resets_each_month(db):
    b = benefit(db, card(db), period_months=1)
    period, starts, ends = current_period(b, date(2026, 8, 9))
    assert period == "2026-08"
    assert starts == date(2026, 8, 1)
    assert ends == date(2026, 8, 31)
    assert current_period(b, date(2026, 9, 1))[0] == "2026-09"


def test_calendar_year_credit_ends_on_new_years_eve(db):
    """The Costco certificate is void after 31 December whenever it was issued."""
    b = benefit(db, card(db, "Costco"), period_months=12, start_month=1)
    period, starts, ends = current_period(b, date(2026, 8, 9))
    assert (period, starts, ends) == ("2026-01", date(2026, 1, 1), date(2026, 12, 31))
    assert period_label(b, starts, ends) == "2026"


def test_anniversary_year_does_not_reset_in_january(db):
    """A Venture X travel credit runs from the cardmember anniversary."""
    b = benefit(db, card(db), period_months=12, start_month=3)
    period, starts, ends = current_period(b, date(2026, 8, 9))
    assert (starts, ends) == (date(2026, 3, 1), date(2027, 2, 28))
    # Still the same cycle in January, which a calendar year would have rolled over.
    assert current_period(b, date(2027, 1, 15))[0] == period
    assert current_period(b, date(2027, 3, 1))[0] == "2027-03"
    assert period_label(b, starts, ends) == "Mar 2026 – Feb 2027"


def test_four_year_cycle_counts_from_its_anchor(db):
    """Global Entry comes round every four years and must not drift."""
    b = benefit(db, card(db), period_months=48, start_month=1, anchor_year=2024)
    assert current_period(b, date(2026, 8, 9))[0] == "2024-01"
    assert current_period(b, date(2027, 12, 31))[0] == "2024-01"
    assert current_period(b, date(2028, 1, 1))[0] == "2028-01"


def test_a_date_before_the_anchor_still_lands_in_a_cycle(db):
    b = benefit(db, card(db), period_months=12, start_month=6, anchor_year=2026)
    period, starts, ends = current_period(b, date(2026, 2, 1))
    assert (starts, ends) == (date(2025, 6, 1), date(2026, 5, 31))
    assert period == "2025-06"


def test_claiming_is_recorded_against_the_cycle_not_the_benefit(db):
    """Last year's travel credit stays used; this year starts unticked."""
    acct = card(db)
    b = benefit(db, acct, period_months=12, start_month=1, value=300.0)
    db.add(BenefitClaim(benefit_id=b.id, period="2025-01", claimed_on=date(2025, 5, 1)))
    db.commit()

    out = listing(db, date(2026, 8, 9))
    row = out["cards"][0]["benefits"][0]
    assert row["claimed"] is False
    assert out["value_left"] == 300.0

    db.add(BenefitClaim(benefit_id=b.id, period="2026-01", claimed_on=date(2026, 8, 9)))
    db.commit()
    out = listing(db, date(2026, 8, 9))
    assert out["cards"][0]["benefits"][0]["claimed"] is True
    assert out["value_left"] == 0.0
    assert out["value_claimed"] == 300.0


def test_expiring_lists_only_unclaimed_perks_running_out(db):
    acct = card(db)
    soon = benefit(db, acct, name="Soon", period_months=12, start_month=1, value=300.0)
    benefit(db, acct, name="Later", period_months=48, start_month=1,
            anchor_year=2026, value=100.0)

    out = listing(db, date(2026, 12, 1))
    assert [b["name"] for b in out["expiring"]] == ["Soon"]

    db.add(BenefitClaim(benefit_id=soon.id, period="2026-01", claimed_on=date(2026, 12, 1)))
    db.commit()
    assert listing(db, date(2026, 12, 1))["expiring"] == []


def test_seeding_never_overwrites_an_edited_card(db):
    acct = card(db)
    benefit(db, acct, name="My own", period_months=12)
    assert seed(db) == 0
    assert len(listing(db)["cards"][0]["benefits"]) == 1


def test_anniversary_month_is_read_from_the_annual_fee(db):
    """A credit resetting on the cardmember year does not reset in January.

    Defaulting to January would have shown a full year left on a Venture X travel
    credit that actually runs out in April.
    """
    from datetime import date as _date

    from talents.models import Transaction
    from talents.services.benefits import listing as _listing

    acct = card(db, "Venture X")
    db.add(
        Transaction(
            account_id=acct.id, date=_date(2026, 4, 14), effective_month="2026-04",
            amount=-395.0, merchant_name="CAPITAL ONE MEMBER FEE", source="test",
        )
    )
    db.commit()

    assert seed(db, _date(2026, 8, 9)) > 0
    rows = {b["name"]: b for b in _listing(db, _date(2026, 8, 9))["cards"][0]["benefits"]}
    travel = rows["$300 travel credit"]
    assert travel["period"] == "2026-04"
    assert travel["ends_on"] == "2027-03-31"


def test_a_calendar_deadline_is_not_moved_by_the_anniversary(db):
    """The Costco certificate dies on 31 December whatever the account's cycle is."""
    from datetime import date as _date

    from talents.models import Transaction
    from talents.services.benefits import listing as _listing

    acct = card(db, "Costco Anywhere Visa")
    db.add(
        Transaction(
            account_id=acct.id, date=_date(2026, 4, 14), effective_month="2026-04",
            amount=-60.0, merchant_name="MEMBERSHIP FEE", source="test",
        )
    )
    db.commit()

    seed(db, _date(2026, 8, 9))
    row = _listing(db, _date(2026, 8, 9))["cards"][0]["benefits"][0]
    assert row["ends_on"] == "2026-12-31"


def test_coffee_is_not_mistaken_for_an_annual_fee(db):
    """Matching "%fee%" finds every coffee shop, and cof-FEE is not a membership fee."""
    from datetime import date as _date

    from talents.models import Transaction
    from talents.services.benefits import _anniversary_month

    acct = card(db, "Venture X")
    db.add(
        Transaction(
            account_id=acct.id, date=_date(2026, 6, 29), effective_month="2026-06",
            amount=-51.16, merchant_name="Ceremony Coffee Roasters", source="test",
        )
    )
    db.add(
        Transaction(
            account_id=acct.id, date=_date(2026, 4, 14), effective_month="2026-04",
            amount=-395.0, merchant_name="CAPITAL ONE MEMBER FEE", source="test",
        )
    )
    db.commit()
    assert _anniversary_month(db, acct.id) == 4
