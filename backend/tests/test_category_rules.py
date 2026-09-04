"""Rules the user made by hand must survive a rebuild of the ported ones.

`/recategorize` rebuilds the ported merchant list from source. "Apply to all like
this" writes a rule that is *not* in that source, so clearing the table wholesale
threw the user's own work away and quietly demoted their transactions to Other.
"""
from __future__ import annotations

from sqlalchemy import select

from talents.models import Category, CategoryRule
from talents.services.categorizer import seed_rules


def user_rule(db, pattern: str, category: str) -> CategoryRule:
    cid = db.scalar(select(Category.id).where(Category.name == category))
    rule = CategoryRule(
        pattern=pattern, category_id=cid, priority=len(pattern), is_user_defined=True
    )
    db.add(rule)
    db.commit()
    return rule


def rebuild(db) -> None:
    """What /recategorize does to the rule table."""
    db.query(CategoryRule).filter(CategoryRule.is_user_defined.is_(False)).delete()
    db.commit()
    seed_rules(db)


def test_a_hand_made_rule_survives_a_rebuild(db):
    user_rule(db, "affirm", "Retail")
    rebuild(db)

    kept = db.scalar(select(CategoryRule).where(CategoryRule.pattern == "affirm"))
    assert kept is not None
    assert db.get(Category, kept.category_id).name == "Retail"


def test_the_ported_rules_are_still_rebuilt_alongside_it(db):
    """Seeding must not consider itself done just because a user rule exists."""
    db.query(CategoryRule).delete()
    db.commit()
    user_rule(db, "affirm", "Retail")

    rebuild(db)
    ported = db.scalars(
        select(CategoryRule).where(CategoryRule.is_user_defined.is_(False))
    ).all()
    assert len(ported) > 100
    assert db.scalar(select(CategoryRule).where(CategoryRule.pattern == "affirm")) is not None


def test_rebuilding_twice_does_not_duplicate_the_ported_rules(db):
    rebuild(db)
    first = db.scalar(select(CategoryRule.id).where(CategoryRule.pattern == "trader joe"))
    rebuild(db)
    patterns = [p for (p,) in db.execute(select(CategoryRule.pattern)).all()]
    assert patterns.count("trader joe") == 1
    assert first is not None


def test_a_transfer_into_savings_is_not_income(db):
    """Marcus calls it an "Internet transfer"; the leg out of Chase is a transfer."""
    from talents.services.categorizer import categorize

    rebuild(db)
    cid = categorize(
        db,
        "Internet transfer from JPMORGAN CHASE BANK, NA DDA account ****5750",
        None,
        fallback=None,
        is_inflow=True,
    )
    assert cid is not None
    assert db.get(Category, cid).name == "Transfers"


def test_a_friend_paying_you_back_on_venmo_is_not_income(db):
    """Splitting a bowling night is not earnings, however the credit is worded."""
    from talents.services.categorizer import categorize

    rebuild(db)
    cid = categorize(
        db, 'Howard Shi "Bowling"', "OTHER", fallback=None, is_inflow=True,
        inflow_fallback="Transfers",
    )
    assert db.get(Category, cid).name == "Transfers"


def test_an_unrecognised_credit_at_a_bank_is_still_income(db):
    """The Venmo rule must not quietly reclassify every deposit everywhere."""
    from talents.services.categorizer import categorize

    rebuild(db)
    cid = categorize(db, "SOME UNKNOWN DEPOSIT", "OTHER", fallback=None, is_inflow=True)
    assert db.get(Category, cid).name == "Other Income"


def test_peer_to_peer_institutions_are_recognised():
    from talents.services.categorizer import is_peer_to_peer

    assert is_peer_to_peer("Venmo - Personal")
    assert is_peer_to_peer("Cash App")
    assert not is_peer_to_peer("Chase")
    assert not is_peer_to_peer("Marcus by Goldman Sachs")
    assert not is_peer_to_peer(None)
