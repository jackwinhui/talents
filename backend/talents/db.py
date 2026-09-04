"""Database session management and seed data."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base, Category

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Mirrors the 19 Tags + 6 Income tags already in the Notion tracker, so migrated
# history maps 1:1 with no category invention.
EXPENSE_CATEGORIES = [
    "Rent/Mortgage", "Utilities", "Groceries", "Dining Out", "Fun", "Subscriptions",
    "Insurance", "Retail", "Donations", "Other", "Transportation", "Healthcare",
    "Personal Care", "Travel", "Education", "Gifts", "Fitness", "Taxes",
]
INCOME_CATEGORIES = ["Salary", "Rent", "Freelance", "Dividends", "Interest", "Other Income"]
TRANSFER_CATEGORIES = ["Transfers"]

# Distinct hues per category so a color identifies a category at a glance in
# tables, pills and charts. Income shades are deliberately green, transfers grey.
CATEGORY_COLORS = {
    "Rent/Mortgage": "#c4472c",
    "Utilities": "#5f9eaa",
    "Groceries": "#7fb069",
    "Dining Out": "#e3b341",
    "Fun": "#9b7fd4",
    "Subscriptions": "#e08a3c",
    "Insurance": "#4a7fb5",
    "Retail": "#d4633a",
    "Donations": "#2e7268",
    "Other": "#94a3b8",
    "Transportation": "#3f8fa8",
    "Healthcare": "#e05c6e",
    "Personal Care": "#d478a8",
    "Travel": "#7c5cc4",
    "Education": "#b8873f",
    "Gifts": "#5fb98a",
    "Fitness": "#e0724a",
    "Taxes": "#8b5e3c",
    "Transfers": "#9aa1a9",
    "Salary": "#1e9e6a",
    "Rent": "#2e9e8f",
    "Freelance": "#4aa06a",
    "Dividends": "#3d9e57",
    "Interest": "#6ab07f",
    "Other Income": "#7fb89a",
}


def _add_missing_columns() -> None:
    """Lightweight additive migration.

    The schema is created with create_all, so a newly added column would be missing
    from an existing database. Adding it here keeps local data intact rather than
    requiring the file to be deleted and every Plaid Item re-linked.
    """
    from sqlalchemy import inspect, text

    wanted = {
        "transactions": {"plaid_category": "VARCHAR(64)"},
        "debts": {"annual_rate": "FLOAT"},
        "category_rules": {"is_user_defined": "BOOLEAN DEFAULT 0 NOT NULL"},
    }
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if table not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _drop_legacy_hash_unique() -> None:
    """Remove the old UNIQUE(hash_key) constraint.

    SQLite cannot drop a constraint in place, so the table is rebuilt. hash_key was
    never a valid identity: two separate transactions can share a date, amount and
    merchant, and enforcing uniqueness rejected legitimate history on import.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='transactions'")
        ).fetchone()
        if not row or "uq_txn_hash" not in (row[0] or ""):
            return

        columns = [r[1] for r in conn.execute(text("PRAGMA table_info(transactions)"))]
        cols = ", ".join(f'"{c}"' for c in columns)
        conn.execute(text("ALTER TABLE transactions RENAME TO transactions_legacy"))
        # Indexes follow the table through a rename, so they must go before the
        # replacement table tries to create indexes of the same name.
        legacy_indexes = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='transactions_legacy' AND name NOT LIKE 'sqlite_%'"
            )
        ).fetchall()
        for (index_name,) in legacy_indexes:
            conn.execute(text(f'DROP INDEX "{index_name}"'))

        Base.metadata.tables["transactions"].create(conn)
        conn.execute(text(f"INSERT INTO transactions ({cols}) SELECT {cols} FROM transactions_legacy"))
        conn.execute(text("DROP TABLE transactions_legacy"))
        print("migrated: dropped UNIQUE(hash_key)")


def _sync_transfer_flag() -> None:
    """Force `is_transfer` and the Transfers category to agree, in both directions.

    Recurring detection, insights and budgets filter on the flag, while the summary
    filters on the category, so a row where the two disagree behaves one way and
    reads another. A Fidelity stock-sale credit sat flagged as a transfer - and so
    correctly kept out of income - while still displaying as "Other Income".
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        transfers = conn.execute(
            text("SELECT id FROM categories WHERE name = 'Transfers'")
        ).scalar()
        if transfers is None:
            return
        flagged = conn.execute(
            text("UPDATE transactions SET is_transfer = 1 WHERE is_transfer = 0 AND category_id = :c"),
            {"c": transfers},
        )
        if flagged.rowcount:
            print(f"migrated: set is_transfer on {flagged.rowcount} transfer rows")
        recategorized = conn.execute(
            text("UPDATE transactions SET category_id = :c WHERE is_transfer = 1 AND category_id != :c"),
            {"c": transfers},
        )
        if recategorized.rowcount:
            print(f"migrated: moved {recategorized.rowcount} flagged rows to Transfers")


def _spell_canceled_the_american_way() -> None:
    """Rename the stored status after the codebase moved to US spelling.

    The status is compared as a literal, so a row left saying "cancelled" would
    match nothing: a bill the user had marked as no longer paid would quietly
    return to the upcoming list.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        result = conn.execute(
            text("UPDATE recurring_series SET status = 'canceled' WHERE status = 'cancelled'")
        )
        if result.rowcount:
            print(f"migrated: respelled {result.rowcount} canceled series")


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()
    _drop_legacy_hash_unique()
    with SessionLocal() as db:
        existing = {c.name: c for c in db.scalars(select(Category)).all()}
        for names, kind in (
            (EXPENSE_CATEGORIES, "expense"),
            (INCOME_CATEGORIES, "income"),
            (TRANSFER_CATEGORIES, "transfer"),
        ):
            for name in names:
                row = existing.get(name)
                if row is None:
                    db.add(Category(name=name, kind=kind, color=CATEGORY_COLORS.get(name)))
                elif not row.color:
                    row.color = CATEGORY_COLORS.get(name)
        db.commit()
    _sync_transfer_flag()
    _spell_canceled_the_american_way()
