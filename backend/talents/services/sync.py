"""Pull accounts, balances and transactions from Plaid into the local database."""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..crypto import decrypt
from ..models import (
    Account, BalanceHistory, Holding, Institution, NetWorthSnapshot, Security, Transaction,
)
from ..providers import plaid_client
from .categorizer import (
    TRANSFER_CATEGORY,
    categorize,
    is_peer_to_peer,
    is_transfer_category,
    normalize_merchant,
)

log = logging.getLogger("talents.sync")


def _hash_key(account_id: int, when: date, amount: float, merchant: str) -> str:
    """Identity independent of the provider, so a CSV import cannot double-count."""
    raw = f"{account_id}|{when.isoformat()}|{amount:.2f}|{normalize_merchant(merchant)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def _is_product_unavailable(exc: Exception) -> bool:
    """True when Plaid is saying the Item was never linked for this product.

    Distinct from a transient failure: retrying will never help, because the Item
    holds no consent for it. Matched on the error code in the response body rather
    than the status, since 400 covers many unrelated problems.
    """
    body = str(getattr(exc, "body", "") or exc)
    return any(
        code in body
        for code in (
            "ADDITIONAL_CONSENT_REQUIRED",
            "PRODUCTS_NOT_SUPPORTED",
            "PRODUCT_NOT_ENABLED",
        )
    )


def sync_institution(db: Session, inst: Institution) -> dict:
    """Sync one Item: refresh balances, then apply the transactions delta."""
    settings = get_settings()
    client = plaid_client.make_client(settings)
    token = decrypt(inst.access_token_enc)
    result = {"institution": inst.name, "added": 0, "updated": 0, "removed": 0, "accounts": 0}

    # --- balances (free, cached) ---
    by_plaid_id: dict[str, Account] = {
        a.plaid_account_id: a
        for a in db.scalars(select(Account).where(Account.institution_id == inst.id)).all()
        if a.plaid_account_id
    }
    for acct in plaid_client.get_accounts(client, token):
        row = by_plaid_id.get(str(acct.account_id))
        if row is None:
            acct_type = str(acct.type)
            row = Account(
                institution_id=inst.id,
                plaid_account_id=str(acct.account_id),
                name=str(acct.name),
                official_name=str(acct.official_name) if acct.official_name else None,
                mask=str(acct.mask) if acct.mask else None,
                type=acct_type,
                subtype=str(acct.subtype) if acct.subtype else None,
                is_asset=acct_type not in ("credit", "loan"),
            )
            db.add(row)
            db.flush()
            by_plaid_id[row.plaid_account_id] = row
        row.current_balance = acct.balances.current
        row.available_balance = acct.balances.available
        row.credit_limit = acct.balances.limit
        result["accounts"] += 1

        if row.current_balance is not None:
            today = date.today()
            existing = db.scalar(
                select(BalanceHistory).where(
                    BalanceHistory.account_id == row.id, BalanceHistory.date == today
                )
            )
            if existing:
                existing.balance = row.current_balance
            else:
                db.add(BalanceHistory(account_id=row.id, date=today, balance=row.current_balance))

    # --- holdings, for brokerages only ---
    # Asking a bank that has no investment accounts would just raise
    # PRODUCTS_NOT_SUPPORTED, so the products the Item actually has decide this.
    if any(a.type == "investment" for a in by_plaid_id.values()):
        try:
            result["holdings"] = sync_holdings(db, inst, client, token)
        except Exception as exc:  # noqa: BLE001
            # A brokerage that will not return holdings should not lose us the
            # transactions we already pulled.
            log.warning("Holdings sync failed for %s: %s", inst.name, exc)
            result["holdings_error"] = str(exc)[:200]

    # --- transactions delta ---
    # A brokerage Item is linked for investments only, so asking it for
    # transactions returns ADDITIONAL_CONSENT_REQUIRED. That is the expected answer
    # for an account with no spending on it, not a sync failure, so it is skipped
    # rather than allowed to discard the holdings just pulled.
    try:
        added, modified, removed, cursor = plaid_client.sync_transactions(
            client, token, inst.cursor
        )
    except Exception as exc:  # noqa: BLE001
        if not _is_product_unavailable(exc):
            raise
        log.info("%s is not linked for transactions; holdings only", inst.name)
        result["transactions"] = "not enabled"
        inst.last_synced_at = datetime.now()
        inst.last_error = None
        db.commit()
        return result

    for txn in list(added) + list(modified):
        acct = by_plaid_id.get(str(txn.account_id))
        if acct is None:
            continue
        txn_id = str(txn.transaction_id)
        row = db.scalar(select(Transaction).where(Transaction.plaid_transaction_id == txn_id))

        when = _as_date(txn.date)
        merchant = str(getattr(txn, "merchant_name", None) or txn.name or "")
        # Plaid reports outflow as positive; we store signed amounts.
        amount = -float(txn.amount)
        pfc = getattr(txn, "personal_finance_category", None)
        primary = str(getattr(pfc, "primary", "")) if pfc else None

        if row is None:
            row = Transaction(plaid_transaction_id=txn_id, source="plaid")
            db.add(row)
            result["added"] += 1
        else:
            result["updated"] += 1

        row.account_id = acct.id
        row.date = when
        row.effective_month = when.strftime("%Y-%m")
        row.amount = amount
        row.merchant_name = merchant
        row.raw_description = str(txn.name or "")
        row.merchant_key = normalize_merchant(merchant or row.raw_description)
        row.is_pending = bool(getattr(txn, "pending", False))
        row.plaid_category = primary or row.plaid_category
        row.hash_key = _hash_key(acct.id, when, amount, merchant)
        if not row.is_manual_override:
            row.category_id = categorize(
                db, f"{merchant} {row.raw_description}", primary, is_inflow=amount > 0,
                inflow_fallback=(
                    TRANSFER_CATEGORY if is_peer_to_peer(inst.name) else "Other Income"
                ),
            )
            row.is_transfer = is_transfer_category(db, row.category_id)

    for txn in removed:
        row = db.scalar(
            select(Transaction).where(
                Transaction.plaid_transaction_id == str(txn.transaction_id)
            )
        )
        if row:
            db.delete(row)
            result["removed"] += 1

    inst.cursor = cursor
    inst.last_synced_at = datetime.now()
    inst.last_error = None
    db.commit()
    return result

def sync_holdings(db: Session, inst: Institution, client, token) -> int:
    """Replace this institution's holdings with what the broker currently reports.

    Holdings are a snapshot rather than a ledger, so they are rebuilt each time.
    A manually entered cost basis is carried across, because Plaid returns NULL for
    some Fidelity positions - notably the 401(k) blended funds - and re-syncing
    should not throw away a figure the user typed in.
    """
    accounts, holdings, securities = plaid_client.get_holdings(client, token)

    by_plaid_id = {
        a.plaid_account_id: a
        for a in db.scalars(select(Account).where(Account.institution_id == inst.id)).all()
        if a.plaid_account_id
    }

    sec_rows: dict[str, Security] = {}
    for sec in securities:
        sid = str(sec.security_id)
        row = db.scalar(select(Security).where(Security.plaid_security_id == sid))
        if row is None:
            row = Security(plaid_security_id=sid)
            db.add(row)
        row.ticker = str(sec.ticker_symbol) if sec.ticker_symbol else None
        row.name = str(sec.name) if sec.name else None
        row.type = str(sec.type) if sec.type else None
        row.close_price = sec.close_price
        row.close_date = _as_date(sec.close_price_as_of) if sec.close_price_as_of else None
        db.flush()
        sec_rows[sid] = row

    account_ids = [a.id for a in by_plaid_id.values()]
    manual: dict[tuple[int, int], float] = {}
    if account_ids:
        for h in db.scalars(select(Holding).where(Holding.account_id.in_(account_ids))).all():
            if h.cost_basis_is_manual and h.cost_basis is not None:
                manual[(h.account_id, h.security_id)] = h.cost_basis
        db.query(Holding).filter(Holding.account_id.in_(account_ids)).delete(
            synchronize_session=False
        )

    kept = 0
    for h in holdings:
        acct = by_plaid_id.get(str(h.account_id))
        sec = sec_rows.get(str(h.security_id))
        if acct is None or sec is None:
            continue
        override = manual.get((acct.id, sec.id))
        db.add(
            Holding(
                account_id=acct.id,
                security_id=sec.id,
                quantity=h.quantity,
                cost_basis=override if override is not None else h.cost_basis,
                cost_basis_is_manual=override is not None,
                institution_value=h.institution_value,
                as_of_date=date.today(),
            )
        )
        kept += 1
    return kept


def sync_all(db: Session) -> list[dict]:
    results = []
    for inst in db.scalars(select(Institution)).all():
        if not inst.access_token_enc:
            continue
        try:
            results.append(sync_institution(db, inst))
        except Exception as exc:  # noqa: BLE001
            log.exception("Sync failed for %s", inst.name)
            inst.last_error = str(exc)[:500]
            db.commit()
            results.append({"institution": inst.name, "error": str(exc)[:300]})
    snapshot_net_worth(db)
    return results


def snapshot_net_worth(db: Session) -> NetWorthSnapshot:
    """Record assets minus liabilities once a day.

    Nothing displays this. Net worth was removed from the UI because the history
    could not be told honestly: brokerages report holdings but no activity, so
    every point before an account was connected had to be either a step change or
    an assumption.

    The recording continues so that the history becomes real from the day the last
    account was linked. Once there is a meaningful run of days where the account
    set has not changed, a net worth chart can come back and be true.
    """
    assets = liabilities = 0.0
    for acct in db.scalars(select(Account).where(Account.is_active.is_(True))).all():
        balance = acct.current_balance or 0.0
        if acct.is_asset:
            assets += balance
        else:
            # Credit balances come back positive when money is owed.
            liabilities += abs(balance) if balance > 0 else -abs(balance)

    today = date.today()
    snap = db.scalar(select(NetWorthSnapshot).where(NetWorthSnapshot.date == today))
    if snap is None:
        snap = NetWorthSnapshot(date=today)
        db.add(snap)
    snap.total_assets = round(assets, 2)
    snap.total_liabilities = round(liabilities, 2)
    snap.net_worth = round(assets - liabilities, 2)
    db.commit()
    return snap
