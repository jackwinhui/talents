"""Categorization and merchant-name normalization."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Category, CategoryRule
from .merchant_rules import all_rules

# Plaid's own taxonomy -> our categories, used when no local rule matches.
PLAID_CATEGORY_MAP = {
    "FOOD_AND_DRINK": "Dining Out",
    "GROCERIES": "Groceries",
    "TRANSPORTATION": "Transportation",
    "TRAVEL": "Travel",
    "RENT_AND_UTILITIES": "Utilities",
    "MEDICAL": "Healthcare",
    "PERSONAL_CARE": "Personal Care",
    "GENERAL_MERCHANDISE": "Retail",
    "ENTERTAINMENT": "Fun",
    "HOME_IMPROVEMENT": "Retail",
    "GENERAL_SERVICES": "Other",
    "GOVERNMENT_AND_NON_PROFIT": "Donations",
    "LOAN_PAYMENTS": "Other",
    "TRANSFER_IN": "Transfers",
    "TRANSFER_OUT": "Transfers",
    "BANK_FEES": "Other",
    "INCOME": "Other",
}

# Income rules, applied to inflows only.
INCOME_RULES: list[tuple[str, str]] = [
    ("microsoft", "Salary"),
    ("payroll", "Salary"),
    ("direct dep", "Salary"),
    ("dividend", "Dividends"),
    ("interest paid", "Interest"),
    ("interest payment", "Interest"),
]

# Trailing confirmation/reference ids that differ on every occurrence. Without
# stripping these, a monthly Zelle rent looks like a new merchant each month and
# recurring detection never fires.
_REF_SUFFIX = re.compile(
    r"\s+(?:[A-Z]{2,4}\d{4,}|JPM\w{6,}|\d{6,}|#\s*\d+|conf(?:irmation)?\s*\S+)\s*$",
    re.IGNORECASE,
)
_MULTISPACE = re.compile(r"\s+")


def normalize_merchant(description: str | None) -> str:
    """Stable key for grouping the same merchant across transactions."""
    if not description:
        return ""
    text = description.strip()
    for _ in range(3):  # descriptions can carry more than one trailing reference
        stripped = _REF_SUFFIX.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"[*#]+", " ", text)
    return _MULTISPACE.sub(" ", text).strip().lower()


def seed_rules(db: Session) -> int:
    """Load the ported merchant rules. Longer patterns win, so priority is length.

    Only the ported rules count as "already seeded". A database holding nothing but
    rules the user made by hand still needs the ported list loading, so the check
    deliberately ignores those.
    """
    if db.scalar(select(CategoryRule).where(CategoryRule.is_user_defined.is_(False)).limit(1)):
        return 0
    by_name = {c.name: c.id for c in db.scalars(select(Category)).all()}
    added = 0
    for pattern, category in all_rules():
        cid = by_name.get(category)
        if cid is None:
            continue
        db.add(CategoryRule(pattern=pattern, category_id=cid, priority=len(pattern)))
        added += 1
    db.commit()
    return added


TRANSFER_CATEGORY = "Transfers"

# Money arriving on one of these is someone settling up, not earnings. Everywhere
# else an unrecognised credit is most likely income; here it is almost never that,
# and booking it as income invents earnings out of split dinners.
P2P_INSTITUTIONS = ("venmo", "cash app", "square cash", "paypal", "zelle")


def is_peer_to_peer(institution_name: str | None) -> bool:
    name = (institution_name or "").lower()
    return any(marker in name for marker in P2P_INSTITUTIONS)


def is_transfer_category(db: Session, category_id: int | None) -> bool:
    """True when `category_id` is the Transfers category.

    The `is_transfer` flag and the Transfers category have to agree. Recurring
    detection, insights and budgets filter on the flag alone, so a card payment
    that was categorized as a transfer but left with the flag unset still showed
    up as a monthly bill and as spending to cut.
    """
    if category_id is None:
        return False
    cat = db.get(Category, category_id)
    return cat is not None and cat.name == TRANSFER_CATEGORY


def categorize(
    db: Session,
    description: str,
    plaid_category: str | None = None,
    fallback: str | None = "Other",
    is_inflow: bool = False,
    inflow_fallback: str = "Other Income",
) -> int | None:
    """Manual override > local rule (longest match) > income rule > Plaid > fallback.

    Local rules are consulted before income rules, in both directions. Paying a card
    produces two rows - money leaving checking and a credit arriving on the card -
    and both are the same transfer. Checking income rules first meant the card side
    never reached the transfer rules and was booked as income, overstating it by
    about $25,000.

    `inflow_fallback` is what an unrecognised credit becomes. On a bank account that
    is fairly called income; on Venmo it is a friend paying their half of dinner, and
    treating those as earnings added $8,483 of income that was never earned.
    """
    text = (description or "").lower()

    best: CategoryRule | None = None
    for rule in db.scalars(select(CategoryRule)).all():
        if rule.pattern in text and (best is None or rule.priority > best.priority):
            best = rule
    if best:
        return best.category_id

    if is_inflow:
        best_income = max(
            (r for r in INCOME_RULES if r[0] in text), key=lambda r: len(r[0]), default=None
        )
        name = best_income[1] if best_income else inflow_fallback
        cat = db.scalar(select(Category).where(Category.name == name))
        if cat:
            return cat.id

    if plaid_category:
        mapped = PLAID_CATEGORY_MAP.get(plaid_category.upper())
        if mapped:
            cat = db.scalar(select(Category).where(Category.name == mapped))
            if cat:
                return cat.id

    if fallback is None:
        # No positive signal. The caller decides whether to keep what is already
        # there, rather than silently demoting a good category to the fallback.
        return None
    cat = db.scalar(select(Category).where(Category.name == fallback))
    return cat.id if cat else None
