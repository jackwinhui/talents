"""Card perks and the windows they have to be used in.

The awkward part is that reset cycles do not agree with each other. A Venture X
travel credit runs from the cardmember anniversary, the Costco certificate expires
every 31 December regardless of when it was issued, DoorDash credits reset monthly,
and a Global Entry credit comes round every four years.

All of it is expressed as a number of months and the month the cycle starts on, so
one calculation covers every case.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import Account, BenefitClaim, CardBenefit, Transaction

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _add_months(when: date, months: int) -> date:
    total = when.year * 12 + (when.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def current_period(benefit: CardBenefit, today: date | None = None) -> tuple[str, date, date]:
    """The cycle `today` falls in, as (label, starts_on, ends_on).

    Counted from the anchor so that cycles longer than a year stay put instead of
    drifting a few months every time they are recalculated.
    """
    today = today or date.today()
    length = max(1, benefit.period_months)
    anchor_year = benefit.anchor_year or today.year
    anchor = date(anchor_year, min(max(benefit.start_month, 1), 12), 1)

    elapsed = (today.year * 12 + today.month) - (anchor.year * 12 + anchor.month)
    index = elapsed // length  # floors towards minus infinity for dates before the anchor
    starts = _add_months(anchor, index * length)
    ends = _add_months(starts, length) - timedelta(days=1)
    return starts.strftime("%Y-%m"), starts, ends


def period_label(benefit: CardBenefit, starts: date, ends: date) -> str:
    if benefit.period_months == 1:
        return f"{MONTHS[starts.month - 1]} {starts.year}"
    if benefit.period_months == 12 and starts.month == 1:
        return str(starts.year)
    return f"{MONTHS[starts.month - 1]} {starts.year} – {MONTHS[ends.month - 1]} {ends.year}"


def cadence_label(benefit: CardBenefit) -> str:
    return {
        1: "monthly", 3: "quarterly", 6: "twice a year", 12: "yearly", 48: "every 4 years",
    }.get(benefit.period_months, f"every {benefit.period_months} months")


def listing(db: Session, today: date | None = None) -> dict:
    today = today or date.today()
    rows = db.execute(
        select(CardBenefit, Account)
        .join(Account, CardBenefit.account_id == Account.id)
        .where(CardBenefit.is_active.is_(True))
        .order_by(CardBenefit.account_id, CardBenefit.sort_order, CardBenefit.id)
    ).all()

    claims = {
        (c.benefit_id, c.period): c
        for c in db.scalars(select(BenefitClaim)).all()
    }

    cards: dict[int, dict] = {}
    for benefit, account in rows:
        period, starts, ends = current_period(benefit, today)
        claim = claims.get((benefit.id, period))
        card = cards.setdefault(account.id, {
            "account_id": account.id,
            "card": account.display_name or account.name,
            "mask": account.mask,
            "benefits": [],
        })
        card["benefits"].append({
            "id": benefit.id,
            "name": benefit.name,
            "detail": benefit.detail,
            "value": benefit.value,
            "period": period,
            "period_label": period_label(benefit, starts, ends),
            "cadence": cadence_label(benefit),
            "period_months": benefit.period_months,
            "start_month": benefit.start_month,
            "ends_on": ends.isoformat(),
            "days_left": (ends - today).days,
            "claimed": claim is not None,
            "claimed_on": claim.claimed_on.isoformat() if claim else None,
            "note": claim.note if claim else None,
        })

    out = list(cards.values())
    every = [b for c in out for b in c["benefits"]]
    unclaimed = [b for b in every if not b["claimed"] and b["value"]]
    for card in out:
        card["value_left"] = round(
            sum(b["value"] or 0 for b in card["benefits"] if not b["claimed"]), 2
        )
    return {
        "cards": out,
        # Only value that can still be captured in the cycle currently open. A
        # yearly credit already used is not money left on the table.
        "value_left": round(sum(b["value"] or 0 for b in unclaimed), 2),
        "value_claimed": round(
            sum(b["value"] or 0 for b in every if b["claimed"]), 2
        ),
        "expiring": sorted(
            (b for b in unclaimed if b["days_left"] <= 60),
            key=lambda b: b["days_left"],
        ),
    }


# Starting points only. Card terms change, and they differ depending on when an
# account was opened - the Sapphire Preferred was reworked in June 2026 and older
# cardholders keep some of the previous terms - so everything here is editable and
# should be checked against the card's own benefits page.
SEEDS: dict[str, list[dict]] = {
    "venture x": [
        {"anniversary": True, "name": "$300 travel credit", "value": 300, "period_months": 12,
         "detail": "Bookings made through Capital One Travel. Resets on the cardmember anniversary, not in January."},
        {"anniversary": True, "name": "10,000 anniversary miles", "value": 100, "period_months": 12,
         "detail": "Posts after each account anniversary. Worth about $100 through the travel portal."},
        {"anniversary": True, "name": "Global Entry / TSA PreCheck credit", "value": 100, "period_months": 48,
         "detail": "Statement credit for the application fee, once every four years."},
        {"anniversary": True, "name": "Annual fee charged", "value": None, "period_months": 12,
         "detail": "$395. Tick it off when it posts so the credits above can be weighed against it."},
    ],
    "chase preferred": [
        {"anniversary": True, "name": "Hotel credit", "value": 100, "period_months": 12,
         "detail": "Prepaid hotel bookings through Chase Travel. Raised from $50 in the June 2026 refresh; verify which applies to your account."},
        {"anniversary": True, "name": "10% anniversary points bonus", "value": None, "period_months": 12,
         "detail": "Being retired for accounts opened after 15 June 2026. Existing cardholders get a final bonus on purchases through 1 October 2026."},
        {"name": "DoorDash monthly credit", "value": 10, "period_months": 1,
         "detail": "Does not roll over, so it is the easiest one to lose."},
        {"anniversary": True, "name": "Annual fee charged", "value": None, "period_months": 12,
         "detail": "$95."},
    ],
    "chase freedom unlimited": [
        {"name": "DoorDash monthly credit", "value": 10, "period_months": 1,
         "detail": "Check whether your account still carries this; it has moved around between Chase cards."},
    ],
    "costco": [
        {"name": "2% reward certificate — redeem", "value": None, "period_months": 12,
         "detail": "Issued with the February statement and void after 31 December. Redeem in a warehouse; it cannot be used online, at Costco Travel, or against the card balance."},
    ],
}


def _anniversary_month(db: Session, account_id: int) -> int | None:
    """The month the card's annual fee lands, which is the cardmember year.

    Credits that reset on the anniversary do not follow the calendar, and guessing
    January would show a full year remaining on a credit that actually expires in
    weeks. The fee already appears in the transactions, so it can be read rather
    than asked for.
    """
    # Matching "%fee%" alone finds every coffee shop, which set one card's
    # anniversary to the month of a cappuccino.
    patterns = ("%annual%fee%", "%member%fee%", "%membership fee%")
    row = db.scalars(
        select(Transaction)
        .where(
            Transaction.account_id == account_id,
            Transaction.amount < 0,
            or_(*(func.lower(Transaction.merchant_name).like(p) for p in patterns)),
        )
        .order_by(Transaction.date.desc())
        .limit(1)
    ).first()
    return row.date.month if row else None


def seed(db: Session, today: date | None = None) -> int:
    """Add starting benefits for cards that have none yet. Never overwrites edits."""
    today = today or date.today()
    added = 0
    for account in db.scalars(
        select(Account).where(Account.type == "credit", Account.is_active.is_(True))
    ).all():
        if db.scalar(
            select(CardBenefit).where(CardBenefit.account_id == account.id).limit(1)
        ):
            continue
        name = f"{account.display_name or account.name}".lower()
        match = next((v for k, v in SEEDS.items() if k in name), None)
        if not match:
            continue
        anniversary_month = _anniversary_month(db, account.id) or 1
        for order, item in enumerate(match):
            # The Costco certificate is the one benefit with a fixed calendar
            # deadline rather than a rolling window, so it anchors to January and
            # runs out on 31 December.
            db.add(
                CardBenefit(
                    account_id=account.id,
                    name=item["name"],
                    detail=item.get("detail"),
                    value=item.get("value"),
                    period_months=item.get("period_months", 12),
                    start_month=(
                        anniversary_month if item.get("anniversary") else item.get("start_month", 1)
                    ),
                    anchor_year=today.year if item.get("period_months") == 48 else None,
                    sort_order=order,
                )
            )
            added += 1
    db.commit()
    return added
