"""SQLAlchemy models — the local source of truth.

Conventions that matter (see plan §6):
  * `amount` is SIGNED: negative = money out, positive = money in.
    Plaid uses positive-for-outflow, so PlaidProvider negates on ingest.
    Notion stored positive expenses, so the migration negates those too.
  * `effective_month` allows attributing a transaction to a period other than its
    payment date (e.g. May rent paid in September). Mirrors Notion's `Month` relation.
  * `is_transfer` is only set when money moves between two accounts YOU own. An
    external Zelle (rent) is a real expense and must never be flagged.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(32), default="plaid")
    plaid_item_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    plaid_institution_id: Mapped[str | None] = mapped_column(String(64))
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    cursor: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    accounts: Mapped[list["Account"]] = relationship(back_populates="institution")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(ForeignKey("institutions.id"))
    plaid_account_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    display_name: Mapped[str | None] = mapped_column(String(120))
    official_name: Mapped[str | None] = mapped_column(String(200))
    mask: Mapped[str | None] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(32), default="depository")
    subtype: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    current_balance: Mapped[float | None] = mapped_column(Float)
    available_balance: Mapped[float | None] = mapped_column(Float)
    credit_limit: Mapped[float | None] = mapped_column(Float)
    is_asset: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    institution: Mapped[Institution | None] = relationship(back_populates="accounts")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="expense")  # expense|income|transfer
    color: Mapped[str | None] = mapped_column(String(24))


class CategoryRule(Base):
    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(200))
    match_type: Mapped[str] = mapped_column(String(16), default="substring")
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    # Longest match wins, mirroring the existing config.yaml behavior.
    priority: Mapped[int] = mapped_column(Integer, default=0)
    # Set when the rule came from "apply to all like this" rather than the ported
    # list. Re-seeding rebuilds the ported ones from source and would otherwise
    # throw away a choice the user made by hand.
    is_user_defined: Mapped[bool] = mapped_column(Boolean, default=False)


class Transaction(Base):
    __tablename__ = "transactions"
    # hash_key is deliberately not unique. It exists to match the same spend across
    # sources, and two genuinely separate transactions can share a date, amount and
    # merchant - two identical parking charges on one day, for instance. Identity
    # comes from plaid_transaction_id or notion_page_id, both of which are unique.

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    plaid_transaction_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    notion_page_id: Mapped[str | None] = mapped_column(String(64), unique=True)

    date: Mapped[date] = mapped_column(Date, index=True)
    effective_month: Mapped[str | None] = mapped_column(String(7), index=True)
    amount: Mapped[float] = mapped_column(Float)
    merchant_name: Mapped[str | None] = mapped_column(String(200))
    raw_description: Mapped[str | None] = mapped_column(Text)
    merchant_key: Mapped[str | None] = mapped_column(String(200), index=True)
    # Plaid's own classification, kept so re-running the local rules cannot discard it.
    plaid_category: Mapped[str | None] = mapped_column(String(64))

    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    is_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    is_transfer: Mapped[bool] = mapped_column(Boolean, default=False)
    is_manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    recurring_series_id: Mapped[int | None] = mapped_column(ForeignKey("recurring_series.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    hash_key: Mapped[str | None] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(24), default="plaid")


class RecurringSeries(Base):
    __tablename__ = "recurring_series"

    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_key: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    cadence: Mapped[str] = mapped_column(String(16), default="monthly")
    expected_amount: Mapped[float | None] = mapped_column(Float)
    next_due_date: Mapped[date | None] = mapped_column(Date)
    last_seen_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="active")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)


class PendingObligation(Base):
    """Incurred but unsettled — e.g. May 2026 rent, $4,500, not yet paid."""

    __tablename__ = "pending_obligations"

    id: Mapped[int] = mapped_column(primary_key=True)
    recurring_series_id: Mapped[int | None] = mapped_column(ForeignKey("recurring_series.id"))
    expected_period: Mapped[str] = mapped_column(String(7))
    expected_amount: Mapped[float] = mapped_column(Float)
    expected_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="outstanding")
    settled_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("category_id", "month", name="uq_budget_cat_month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    month: Mapped[str | None] = mapped_column(String(7))  # NULL = default template
    amount: Mapped[float] = mapped_column(Float)
    rollover: Mapped[bool] = mapped_column(Boolean, default=False)


class Security(Base):
    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(primary_key=True)
    plaid_security_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    ticker: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(200))
    type: Mapped[str | None] = mapped_column(String(32))
    close_price: Mapped[float | None] = mapped_column(Float)
    close_date: Mapped[date | None] = mapped_column(Date)


class Holding(Base):
    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    quantity: Mapped[float] = mapped_column(Float)
    # Plaid may return NULL cost basis for Fidelity; manual entry fills the gap.
    cost_basis: Mapped[float | None] = mapped_column(Float)
    cost_basis_is_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    institution_value: Mapped[float | None] = mapped_column(Float)
    as_of_date: Mapped[date | None] = mapped_column(Date)


class BalanceHistory(Base):
    __tablename__ = "balance_history"
    __table_args__ = (UniqueConstraint("account_id", "date", name="uq_balance_acct_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    date: Mapped[date] = mapped_column(Date)
    balance: Mapped[float] = mapped_column(Float)


class NetWorthSnapshot(Base):
    __tablename__ = "net_worth_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, unique=True)
    total_assets: Mapped[float] = mapped_column(Float)
    total_liabilities: Mapped[float] = mapped_column(Float)
    net_worth: Mapped[float] = mapped_column(Float)
    pending_obligations: Mapped[float] = mapped_column(Float, default=0.0)


class CardBenefit(Base):
    """A recurring perk on a card that has to be used before its window closes.

    `period_months` and `start_month` describe the reset cycle, because these do not
    all follow the calendar. A Venture X travel credit resets on the cardmember
    anniversary, the Costco certificate expires every 31 December whatever month it
    was issued, and a Global Entry credit comes round every four years.
    """

    __tablename__ = "card_benefits"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str | None] = mapped_column(Text)
    value: Mapped[float | None] = mapped_column(Float)
    period_months: Mapped[int] = mapped_column(Integer, default=12)
    # Month the cycle starts on, 1-12. January for a calendar-year credit, the
    # anniversary month for a cardmember-year one.
    start_month: Mapped[int] = mapped_column(Integer, default=1)
    # Cycles longer than a year need a fixed point to count from, or the four-year
    # Global Entry window would drift.
    anchor_year: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class BenefitClaim(Base):
    """One row per benefit per cycle. Its presence means the perk was used."""

    __tablename__ = "benefit_claims"
    __table_args__ = (UniqueConstraint("benefit_id", "period", name="uq_claim_benefit_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    benefit_id: Mapped[int] = mapped_column(ForeignKey("card_benefits.id"))
    period: Mapped[str] = mapped_column(String(16))
    claimed_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)


class Debt(Base):
    """Money owed to a person against an agreed total, paid down monthly.

    Not a bill: a bill recurs forever, this one ends. What matters is how much of
    the total is left and how long that will take at the current rate.
    """

    __tablename__ = "debts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    payee: Mapped[str | None] = mapped_column(String(120))
    detail: Mapped[str | None] = mapped_column(Text)
    total_amount: Mapped[float] = mapped_column(Float)
    monthly_payment: Mapped[float] = mapped_column(Float)
    # Nominal annual rate as a percent, e.g. 4.5. None means the debt carries no
    # interest and what is left is simply the total minus what has been paid.
    annual_rate: Mapped[float | None] = mapped_column(Float)
    # Matched against merchant text so payments recorded later are picked up
    # without being entered by hand.
    match_merchant: Mapped[str | None] = mapped_column(String(120))
    # Which debt gets first claim when one payment covers several.
    priority: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class DebtPayment(Base):
    """One installment. `transaction_id` is set when it is backed by real money moved.

    A single transaction can settle more than one debt - a $5,500 payment covering
    $4,500 of house and $1,000 of car is two rows pointing at the same transaction -
    and payments made before the account was ever connected have no transaction at all.
    """

    __tablename__ = "debt_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    debt_id: Mapped[int] = mapped_column(ForeignKey("debts.id"))
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    paid_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)


class DebtExclusion(Base):
    """A payment to a tracked payee that is deliberately not part of any debt.

    Settling up over something unrelated still shows as money to the same person,
    and without this it would sit in the unallocated list forever asking to be
    filed against a debt it has nothing to do with.
    """

    __tablename__ = "debt_exclusions"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"), unique=True)
    note: Mapped[str | None] = mapped_column(Text)
