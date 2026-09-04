"""Merchant substring -> category.

Ported from the notion-finance-sync config.yaml. These were tuned by hand against
two years of real statements, so they are the most valuable asset carried over from
the Notion setup. Longest match wins, matching the original behavior.

Only rules that would help anyone live here. A church, a landlord or a family member
you pay by name is specific to one person and belongs in `personal_rules.json`,
which is git-ignored — see `personal_rules.example.json`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import DATA_DIR

log = logging.getLogger("talents.rules")

PERSONAL_RULES_PATH = DATA_DIR / "personal_rules.json"

MERCHANT_RULES: list[tuple[str, str]] = [
    ('bilt rent', 'Rent/Mortgage'),
    ('bps bilt', 'Rent/Mortgage'),
    ('biltprotect', 'Rent/Mortgage'),
    ('bilt tech', 'Rent/Mortgage'),
    ('trader joe', 'Groceries'),
    ('whole foods', 'Groceries'),
    ('safeway', 'Groceries'),
    ('giant food', 'Groceries'),
    ('wegmans', 'Groceries'),
    ('h mart', 'Groceries'),
    ('costco whse', 'Groceries'),
    ('www costco', 'Groceries'),
    ('costco gas', 'Transportation'),
    ('costco annual membership', 'Subscriptions'),
    ('costco membership', 'Subscriptions'),
    ('costco', 'Groceries'),
    ('starbucks', 'Dining Out'),
    ('chipotle', 'Dining Out'),
    ('doordash', 'Dining Out'),
    ('uber eats', 'Dining Out'),
    ('chick-fil-a', 'Dining Out'),
    ('mcdonald', 'Dining Out'),
    ('cava', 'Dining Out'),
    ('panera', 'Dining Out'),
    ('subway', 'Dining Out'),
    ('shake shack', 'Dining Out'),
    ('yifang', 'Dining Out'),
    ('molly tea', 'Dining Out'),
    ('cuppa tea', 'Dining Out'),
    ('bobapop', 'Dining Out'),
    ('snack tea', 'Dining Out'),
    ('ding cafe', 'Dining Out'),
    ('first sight', 'Dining Out'),
    ('tous les', 'Dining Out'),
    ('myung ga', 'Dining Out'),
    ('pine & crane', 'Dining Out'),
    ('tesla supercharger', 'Transportation'),
    ('driveezmd', 'Transportation'),
    ('spothero', 'Transportation'),
    ('transportfornsw', 'Transportation'),
    ('ladot', 'Transportation'),
    ('uber', 'Transportation'),
    ('lyft', 'Transportation'),
    ('metro', 'Transportation'),
    ('first sight vision', 'Healthcare'),
    ('vision care', 'Healthcare'),
    ('optometr', 'Healthcare'),
    ('quest diagnostics', 'Healthcare'),
    ('labcorp', 'Healthcare'),
    ('johns hopkins', 'Healthcare'),
    ('my chart', 'Healthcare'),
    ('dentistry', 'Healthcare'),
    ('dental', 'Healthcare'),
    ('pharmacy', 'Healthcare'),
    ('cvs', 'Healthcare'),
    ('walgreens', 'Healthcare'),
    ('hair', 'Personal Care'),
    ('salon', 'Personal Care'),
    ('barber', 'Personal Care'),
    ('gym', 'Fitness'),
    ('fitness', 'Fitness'),
    ('planet fit', 'Fitness'),
    ('qantas', 'Travel'),
    ('airbnb', 'Travel'),
    ('hotel', 'Travel'),
    ('marriott', 'Travel'),
    ('hilton', 'Travel'),
    ('united air', 'Travel'),
    ('united      0', 'Travel'),
    ('amtrak', 'Travel'),
    ('delta air', 'Travel'),
    ('irs', 'Taxes'),
    ('netflix', 'Subscriptions'),
    ('spotify', 'Subscriptions'),
    ('openai', 'Subscriptions'),
    ('google gsuite', 'Subscriptions'),
    ('google workspace', 'Subscriptions'),
    ('tesla subscription', 'Subscriptions'),
    ('ouraring', 'Subscriptions'),
    ('capital one member fee', 'Subscriptions'),
    ('pg&e', 'Utilities'),
    ('comcast', 'Utilities'),
    ('pepco', 'Utilities'),
    ('washington gas', 'Utilities'),
    ('verizon', 'Utilities'),
    ('theoharismana', 'Utilities'),
    ('theoharis', 'Utilities'),
    ('eb *', 'Fun'),
    ('eventbrite', 'Fun'),
    ('night of worsh', 'Fun'),
    ('geico', 'Insurance'),
    ('state farm', 'Insurance'),
    # Car insurance sold under a brand that is not obviously an insurer. Without
    # this it lands in Subscriptions and never reaches the Insurance budget.
    ('property casualty', 'Insurance'),
    ('amazon', 'Retail'),
    ('target', 'Retail'),
    ('the home depot', 'Retail'),
    ('home depot', 'Retail'),
    ('uniqlo', 'Retail'),
    ('apple.com', 'Retail'),
    ('first watch', 'Dining Out'),
    ('steam', 'Fun'),
    ('amc', 'Fun'),
    # A rent or mortgage paid to a person by name is a personal rule: Plaid calls
    # it a TRANSFER_OUT, so without one the largest recurring expense in the whole
    # dataset is excluded from spending entirely. See personal_rules.example.json.
    ('crcardpmt', 'Transfers'),
    ('card payment', 'Transfers'),
    ('autopay', 'Transfers'),
    # Credits that are really the other half of a transfer, not income. A card
    # payment shows as money out of checking and a credit on the card; brokerage
    # and wallet withdrawals are your own money moving between accounts.
    ('payment thank you', 'Transfers'),
    ('automatic payment - thank', 'Transfers'),
    ('autopay pymt', 'Transfers'),
    ('mobile pymt', 'Transfers'),
    ('book transfer credit', 'Transfers'),
    ('national financial', 'Transfers'),
    ('venmo            cashout', 'Transfers'),
    ('online transfer', 'Transfers'),
    # Marcus's wording for the same thing. The outgoing leg from Chase is already
    # a transfer, so without this the money is counted as income at the receiving
    # end and a move between your own accounts looks like a $10,000 payday.
    ('internet transfer', 'Transfers'),
    # Cashing out of Venmo is your own money arriving, and the Venmo side already
    # records it leaving. The imported Notion categories had the outgoing leg as a
    # transfer but the incoming one as income, counting $4,178 twice.
    ('venmo', 'Transfers'),
]


def load_personal_rules(path: Path | None = None) -> list[tuple[str, str]]:
    """Rules for merchants only you would recognise, kept out of the repo.

    A missing file is the normal case, not an error: most people will never write
    one. A malformed file is worth complaining about, but not worth refusing to
    start over — the shipped rules still categorise almost everything.
    """
    path = path or PERSONAL_RULES_PATH
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
        return [(str(k).lower(), str(v)) for k, v in raw.items() if k and v]
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        log.warning("Ignoring %s: %s", path.name, exc)
        return []


def all_rules() -> list[tuple[str, str]]:
    """The shipped rules plus anything personal.

    A personal rule for a pattern the shipped list already covers replaces it
    outright. Both would otherwise be stored with the same priority - priority is
    pattern length - and the shipped one would win on seeding order, silently
    ignoring the rule the user wrote to override it.
    """
    personal = load_personal_rules()
    overridden = {pattern for pattern, _ in personal}
    return [(p, c) for p, c in MERCHANT_RULES if p not in overridden] + personal
