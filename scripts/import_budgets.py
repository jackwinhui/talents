"""Import the monthly budgets carried over from the Notion tracker.

The figures live in notion_budgets.json so the source of the numbers is reviewable
rather than buried in code. Budgets are stored with month=NULL, which the app treats
as the recurring default for every month.

Idempotent: re-running overwrites the same rows rather than duplicating them.

Run:  ../.venv/bin/python scripts/import_budgets.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from talents.db import SessionLocal, init_db  # noqa: E402
from talents.models import Budget, Category  # noqa: E402


def main() -> None:
    data = json.loads((Path(__file__).parent / "notion_budgets.json").read_text())
    budgets: dict[str, float] = data["budgets"]

    init_db()
    created = updated = 0
    missing: list[str] = []

    with SessionLocal() as db:
        by_name = {c.name: c for c in db.scalars(select(Category)).all()}
        for name, amount in budgets.items():
            category = by_name.get(name)
            if category is None:
                missing.append(name)
                continue
            row = db.scalar(
                select(Budget).where(Budget.category_id == category.id, Budget.month.is_(None))
            )
            if row is None:
                db.add(Budget(category_id=category.id, amount=float(amount)))
                created += 1
            elif row.amount != float(amount):
                row.amount = float(amount)
                updated += 1
        db.commit()

    total = sum(budgets.values())
    print(f"created {created}, updated {updated}, total ${total:,.0f}/mo")
    if missing:
        print(f"no matching category for: {', '.join(missing)}")


if __name__ == "__main__":
    main()
