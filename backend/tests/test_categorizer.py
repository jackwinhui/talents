"""Categorization rules.

Each test here corresponds to a bug found against real data, where trusting Plaid's
own classification would have silently corrupted the totals.
"""
from __future__ import annotations

from sqlalchemy import select

from talents.models import Category
from talents.services.categorizer import categorize, normalize_merchant, seed_rules


def name_of(db, category_id):
    return db.get(Category, category_id).name if category_id else None


def test_external_zelle_rent_is_an_expense_not_a_transfer(db, personal_rules):
    """Plaid labels a rent Zelle paid to a person TRANSFER_OUT.

    Transfers are excluded from spending, so trusting that would drop $4,500 a
    month - $54k a year - out of the figures entirely. Who you pay rent to is
    personal, so the rule that rescues it lives in personal_rules.json.
    """
    personal_rules({"zelle payment to jane doe": "Rent/Mortgage"})
    seed_rules(db)
    cid = categorize(db, "Zelle payment to Jane Doe 29437791268", "TRANSFER_OUT")
    assert name_of(db, cid) == "Rent/Mortgage"


def test_payroll_inflow_is_income_not_an_expense_category(db):
    """Salary arrives as TRANSFER_IN and previously landed in the expense fallback."""
    seed_rules(db)
    cid = categorize(db, "Microsoft", "TRANSFER_IN", is_inflow=True)
    assert name_of(db, cid) == "Salary"


def test_card_payment_is_a_transfer(db):
    seed_rules(db)
    cid = categorize(db, "CAPITAL ONE CRCARDPMT WEB ID", None)
    assert name_of(db, cid) == "Transfers"


def test_plaid_category_is_used_when_no_local_rule_matches(db):
    seed_rules(db)
    cid = categorize(db, "Yechon", "FOOD_AND_DRINK")
    assert name_of(db, cid) == "Dining Out"


def test_no_signal_declines_rather_than_demoting(db):
    """Re-running the rules must not pull a well-classified row back to "Other".

    Plaid enriches a merchant when a charge posts; without this the next
    recategorize would discard that work.
    """
    seed_rules(db)
    assert categorize(db, "Some Unknown Merchant", None, fallback=None) is None


def test_local_rule_beats_plaid_category(db):
    """Plaid files the warehouse as GENERAL_SERVICES ("Other"); the rule knows better."""
    seed_rules(db)
    cid = categorize(db, "COSTCO WHSE #456", "GENERAL_SERVICES")
    assert name_of(db, cid) == "Groceries"


def test_a_personal_rule_beats_a_shipped_one(db, personal_rules):
    """Your own rule is the last word: the shipped list cannot know your merchants."""
    personal_rules({"costco whse": "Retail"})
    seed_rules(db)
    assert name_of(db, categorize(db, "COSTCO WHSE #456", None)) == "Retail"


def test_longest_rule_wins(db):
    """"costco gas" is fuel while plain "costco" is groceries."""
    seed_rules(db)
    assert name_of(db, categorize(db, "COSTCO GAS #123", None)) == "Transportation"
    assert name_of(db, categorize(db, "COSTCO WHSE #456", None)) == "Groceries"


def test_normalize_strips_per_payment_references():
    """Without this the rent looks like a new merchant every month."""
    a = normalize_merchant("Zelle payment to Jane Doe 29437791268")
    b = normalize_merchant("Zelle payment to Jane Doe 29827669250")
    assert a == b == "zelle payment to jane doe"


def test_normalize_groups_branches_of_the_same_merchant():
    """Branch numbers are stripped on purpose.

    "TRADER JOE S #648" and "#036" are the same merchant, and grouping them is what
    lets recurring detection and merchant rules see one shop rather than many.
    """
    assert normalize_merchant("Trader Joe's #648") == normalize_merchant("Trader Joe's #036")


def test_normalize_keeps_distinct_merchants_apart():
    assert normalize_merchant("Trader Joe's") != normalize_merchant("Whole Foods")


def test_card_payment_credit_is_a_transfer_not_income(db):
    """Paying a card writes two rows: the debit on checking and the credit on the card.

    Income rules used to be consulted before the local rules for any inflow, so the
    card side never reached the transfer rules and was booked as "Other Income".
    That inflated income by about $25,000 and turned cumulative savings positive.
    """
    seed_rules(db)
    for text in (
        "CAPITAL ONE AUTOPAY PYMT",
        "Payment Thank You-Mobile",
        "AUTOMATIC PAYMENT - THANK YOU",
        "BOOK TRANSFER CREDIT B/O: NATIONAL FINANCIAL",
    ):
        cid = categorize(db, text, "TRANSFER_IN", is_inflow=True)
        assert name_of(db, cid) == "Transfers", text


def test_salary_still_wins_for_a_genuine_inflow(db):
    """The reordering above must not stop real income being recognized."""
    seed_rules(db)
    cid = categorize(db, "Microsoft payroll", "TRANSFER_IN", is_inflow=True)
    assert name_of(db, cid) == "Salary"


def test_transfer_category_drives_the_transfer_flag(db):
    """Recurring detection and insights filter on `is_transfer`, not on the category.

    A row categorized as Transfers but left with the flag unset still showed up as
    a monthly bill, so the two have to be kept in step.
    """
    from talents.services.categorizer import is_transfer_category

    seed_rules(db)
    transfers = categorize(db, "CAPITAL ONE AUTOPAY PYMT", None, is_inflow=True)
    groceries = db.scalar(select(Category).where(Category.name == "Groceries"))
    assert is_transfer_category(db, transfers) is True
    assert is_transfer_category(db, groceries.id) is False
    assert is_transfer_category(db, None) is False
