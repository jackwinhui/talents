"""Plaid Link: connect institutions and persist encrypted access tokens."""
from __future__ import annotations

import json
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import ENV_PATH, get_settings, reload_settings, write_env_values
from ..crypto import decrypt, encrypt
from ..db import get_db
from ..models import Account, Institution, Transaction
from ..providers import plaid_client

log = logging.getLogger("talents.link")
router = APIRouter(prefix="/api/link", tags=["link"])

# Everything in `products` is a hard requirement: Link refuses an institution that
# cannot serve all of them. Keep that list to what the app genuinely cannot work
# without, or savings-only banks are turned away at the door.
PRODUCTS_BY_KIND = {
    "bank": ["transactions"],
    # Brokerages only need `investments` — it already covers holdings and investment
    # transactions. Requesting `transactions` too adds no data for a brokerage and
    # only widens the surface for a Link failure.
    "investments": ["investments"],
}

# Wanted wherever it exists, never worth losing an institution over. Liabilities
# gives card due dates and APRs, which only a card issuer can answer: Marcus
# (ins_52) is savings-only and does not offer it, and requiring it made Link
# reject the institution with "Connectivity not supported". It stays requested so
# the data is there on the accounts that do have it — products cannot be added to
# an Item after the fact without relinking it.
IF_SUPPORTED_BY_KIND = {
    "bank": ["liabilities"],
    "investments": [],
}


class ExchangeRequest(BaseModel):
    public_token: str


class PlaidCredentials(BaseModel):
    client_id: str
    secret: str
    plaid_env: str = "production"


def _plaid_error_code(exc: Exception) -> str:
    """Plaid's own reason for a refusal, or "" when it did not give one.

    The SDK raises with the response body attached as JSON rather than putting
    anything useful in the message, so without unpacking it every failure looks
    identical to the caller.
    """
    body = getattr(exc, "body", None)
    if not body:
        return ""
    try:
        return str(json.loads(body).get("error_code") or "")
    except (ValueError, TypeError):
        return ""


def _diagnose(client_id: str, secret: str, env: str, exc: Exception) -> str:
    """Say what is actually wrong, rather than that something is.

    `INVALID_API_KEYS` covers both a mistyped key and a perfectly good key used
    against the wrong environment, and by far the most common version of the
    latter is pasting the Sandbox secret: it is the one the dashboard shows first,
    and it sits directly above the production secret it gets confused with. The
    two are told apart by trying the other environment - if the credentials work
    there, nothing is wrong with them except where they were pointed.
    """
    code = _plaid_error_code(exc)
    if code and code != "INVALID_API_KEYS":
        return f"Plaid refused these credentials: {code}."

    other = "sandbox" if env.lower() == "production" else "production"
    try:
        plaid_client.create_link_token(
            plaid_client.make_client(_Credentials(client_id, secret, other)),
            PRODUCTS_BY_KIND["bank"],
            required_if_supported=IF_SUPPORTED_BY_KIND["bank"],
        )
    except Exception:
        return (
            "Plaid does not recognise that client ID and secret. Copy them again "
            "from Developers → Keys, and check the client ID has not picked up a "
            "stray space."
        )

    if other == "sandbox":
        return (
            "Those are your Sandbox keys. Sandbox only returns made-up test data, "
            "so Talents needs the Production secret instead — same page, same "
            "client ID, but the secret listed under Production. If there is no "
            "production secret there yet, apply for the free Trial plan first."
        )
    return "Those keys work in Production. Switch the environment to production."


class _Credentials:
    """The two fields make_client actually reads, without touching the real settings."""

    def __init__(self, client_id: str, secret: str, env: str):
        self.plaid_client_id = client_id
        self.plaid_secret = secret
        self.plaid_env = env


@router.get("/setup")
def setup_status() -> dict:
    """Whether the app can talk to Plaid yet, and where its settings live.

    The UI asks this before offering to connect anything. Without it the first
    thing a new user does is press a button that returns a 500, which reads as a
    broken app rather than one waiting to be told its credentials.
    """
    settings = get_settings()
    return {
        "configured": settings.plaid_ready,
        "plaid_env": settings.plaid_env,
        "env_path": str(ENV_PATH),
        # Enough to confirm which account is in use, never the secret.
        "client_id_tail": settings.plaid_client_id[-4:] if settings.plaid_client_id else "",
    }


@router.post("/setup")
def save_credentials(payload: PlaidCredentials) -> dict:
    """Store Plaid credentials and prove they work before keeping them.

    Validated by asking Plaid for a link token, because the alternative is writing
    a typo to disk and only discovering it later at the point of connecting a bank,
    where the failure looks like it belongs to the bank rather than the setup.
    """
    client_id = payload.client_id.strip()
    secret = payload.secret.strip()
    if not client_id or not secret:
        raise HTTPException(400, "Both the client ID and the secret are required")

    previous = get_settings()
    write_env_values({
        "PLAID_CLIENT_ID": client_id,
        "PLAID_SECRET": secret,
        "PLAID_ENV": payload.plaid_env.strip() or "production",
    })
    settings = reload_settings()

    try:
        plaid_client.create_link_token(
            plaid_client.make_client(settings), PRODUCTS_BY_KIND["bank"],
            required_if_supported=IF_SUPPORTED_BY_KIND["bank"],
        )
    except Exception as exc:
        # Put back whatever was there rather than leaving the app holding
        # credentials that have already been shown not to work.
        write_env_values({
            "PLAID_CLIENT_ID": previous.plaid_client_id,
            "PLAID_SECRET": previous.plaid_secret,
            "PLAID_ENV": previous.plaid_env,
        })
        reload_settings()
        log.warning("Rejected Plaid credentials (%s): %s",
                    _plaid_error_code(exc) or "no code", str(exc)[:200])
        raise HTTPException(400, _diagnose(client_id, secret, payload.plaid_env, exc)) from exc

    log.info("Plaid credentials saved to %s", ENV_PATH)
    return {"configured": True, "plaid_env": settings.plaid_env}


@router.get("/token")
def link_token(kind: str = "bank") -> dict:
    settings = get_settings()
    if not settings.plaid_ready:
        # 409, not 500: nothing has gone wrong, the app is simply not set up yet.
        raise HTTPException(409, "Plaid is not set up yet. Add your credentials first.")
    client = plaid_client.make_client(settings)
    products = PRODUCTS_BY_KIND.get(kind, PRODUCTS_BY_KIND["bank"])
    if_supported = IF_SUPPORTED_BY_KIND.get(kind, IF_SUPPORTED_BY_KIND["bank"])
    token = plaid_client.create_link_token(
        client, products, required_if_supported=if_supported
    )
    return {"link_token": token, "products": products, "if_supported": if_supported}


@router.post("/exchange")
def exchange(payload: ExchangeRequest, db: Session = Depends(get_db)) -> dict:
    """Consumes one Trial Item slot — only called after a successful Link."""
    settings = get_settings()
    client = plaid_client.make_client(settings)

    access_token, item_id = plaid_client.exchange_public_token(client, payload.public_token)

    existing = db.scalar(select(Institution).where(Institution.plaid_item_id == item_id))
    if existing:
        existing.access_token_enc = encrypt(access_token)
        existing.status = "active"
        inst = existing
    else:
        item = plaid_client.get_item(client, access_token)
        inst_id = getattr(item, "institution_id", None)
        name = plaid_client.get_institution_name(client, inst_id) if inst_id else None
        inst = Institution(
            name=name or "Unknown institution",
            provider="plaid",
            plaid_item_id=item_id,
            plaid_institution_id=inst_id,
            access_token_enc=encrypt(access_token),
        )
        db.add(inst)
    db.flush()

    created = 0
    for acct in plaid_client.get_accounts(client, access_token):
        acct_id = str(acct.account_id)
        if db.scalar(select(Account).where(Account.plaid_account_id == acct_id)):
            continue
        acct_type = str(acct.type)
        balances = acct.balances
        db.add(
            Account(
                institution_id=inst.id,
                plaid_account_id=acct_id,
                name=str(acct.name),
                official_name=str(acct.official_name) if acct.official_name else None,
                mask=str(acct.mask) if acct.mask else None,
                type=acct_type,
                subtype=str(acct.subtype) if acct.subtype else None,
                current_balance=balances.current,
                available_balance=balances.available,
                credit_limit=balances.limit,
                # Credit cards and loans are liabilities; everything else is an asset.
                is_asset=acct_type not in ("credit", "loan"),
            )
        )
        created += 1

    db.commit()
    return {"institution": inst.name, "item_id": item_id, "accounts_added": created}


@router.get("/institutions")
def list_institutions(db: Session = Depends(get_db)) -> list[dict]:
    # Oldest Plaid transaction per institution. Plaid hands over only 90 days of
    # history unless 730 is requested explicitly at link time, so the span an Item
    # actually holds is the only way to tell whether it still needs update mode
    # run against it.
    today = date.today()
    oldest = {
        inst_id: first
        for inst_id, first in db.execute(
            select(Account.institution_id, func.min(Transaction.date))
            .join(Transaction, Transaction.account_id == Account.id)
            .where(Transaction.source == "plaid")
            .group_by(Account.institution_id)
        ).all()
    }
    out = []
    for inst in db.scalars(select(Institution)).all():
        accounts = db.scalars(select(Account).where(Account.institution_id == inst.id)).all()
        first = oldest.get(inst.id)
        out.append({
            "id": inst.id,
            "name": inst.name,
            "status": inst.status,
            "last_synced_at": inst.last_synced_at,
            "last_error": inst.last_error,
            "history_days": (today - first).days if first else None,
            "history_from": first.isoformat() if first else None,
            # Imported history has no Plaid Item behind it, so update mode does not
            # apply and the UI must not offer it.
            "linked": inst.access_token_enc is not None,
            "accounts": [
                {
                    "id": a.id,
                    "name": a.display_name or a.name,
                    "mask": a.mask,
                    "type": a.type,
                    "subtype": a.subtype,
                    "balance": a.current_balance,
                    "is_asset": a.is_asset,
                }
                for a in accounts
            ],
        })
    return out


LINK_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Talents — Connect accounts</title>
<link rel="icon" href="/assets/icon-64.png">
<link rel="apple-touch-icon" href="/assets/icon-180.png">
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<style>
 body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:760px;
      margin:48px auto;padding:0 24px;color:#111;line-height:1.5}
 button{font-size:15px;padding:11px 20px;border-radius:8px;border:1px solid #d0d0d5;
        background:#fff;cursor:pointer;margin:6px 8px 6px 0}
 button.primary{background:#111;color:#fff;border-color:#111}
 button:disabled{opacity:.5;cursor:default}
 pre{background:#f6f6f8;padding:14px;border-radius:8px;overflow:auto;font-size:13px}
 .note{color:#666;font-size:14px}
 .verse{color:#5b6470;font-size:15px;font-style:italic;border-left:3px solid #dfe3e8;
        padding-left:14px;margin:10px 0 0}
 header{display:flex;align-items:center;gap:20px;margin-bottom:22px}
 header h1{margin:0;font-size:34px;letter-spacing:-0.5px}
 .warn{background:#fff8e5;border:1px solid #f0d999;padding:12px 14px;border-radius:8px}
</style></head><body>
<header>
  <img src="/assets/logo-256.png" width="96" height="96" alt="">
  <div>
    <h1>Talents</h1>
    <p class="verse">&ldquo;Well done, good and faithful servant! You have been faithful
    with a few things; I will put you in charge of many things.&rdquo;
    &mdash; Matthew 25:21</p>
  </div>
</header>
<p class="note">Runs entirely on your Mac. Tokens are encrypted with a key held in the
macOS Keychain.</p>
<div class="warn"><strong>Plaid Trial: 10 Items total, and removing an Item does not
free a slot.</strong> Plan: Chase (checking + both cards = 1), Capital One, Citi,
Fidelity &rarr; 4 of 10.</div>
<h3>1. Banks &amp; credit cards</h3>
<button class="primary" onclick="open_('bank')">Connect a bank / card</button>
<span class="note">Chase &rarr; Citi &rarr; Capital One</span>
<h3>2. Investments</h3>
<button onclick="open_('investments')">Connect Fidelity</button>
<span class="note">Check both brokerage/IRA and NetBenefits/401(k)</span>
<h3>Connected</h3>
<pre id="out">Loading…</pre>
<script>
async function refresh(){
  const r = await fetch('/api/link/institutions');
  const d = await r.json();
  document.getElementById('out').textContent =
    d.length ? JSON.stringify(d,null,2) : 'Nothing connected yet.';
}
async function open_(kind){
  const out = document.getElementById('out');
  out.textContent = 'Requesting link token…';
  const r = await fetch('/api/link/token?kind='+kind);
  if(!r.ok){ out.textContent = 'Error: '+await r.text(); return; }
  const {link_token} = await r.json();
  out.textContent = 'Opening Plaid…';
  Plaid.create({
    token: link_token,
    onSuccess: async (public_token) => {
      out.textContent = 'Exchanging token…';
      const e = await fetch('/api/link/exchange',{
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({public_token})
      });
      out.textContent = e.ok ? 'Connected.\\n'+JSON.stringify(await e.json(),null,2)
                             : 'Exchange failed: '+await e.text();
      setTimeout(refresh, 1200);
    },
    onExit: (err) => { out.textContent = err ? 'Exited: '+JSON.stringify(err,null,2)
                                             : 'Closed without connecting.'; refresh(); }
  }).open();
}
refresh();
</script></body></html>"""


@router.get("/reconnect")
def reconnect(institution_id: int, db: Session = Depends(get_db)) -> dict:
    """Link token in update mode, to re-authorize an existing Item.

    Use when a bank login breaks (ITEM_LOGIN_REQUIRED), a password changes, or MFA
    needs re-doing. Update mode reuses the same Item, so it does **not** consume
    another Trial Item slot.

    This deliberately cannot extend transaction history. Plaid fixes the history
    length when the Item is created: "once an Item is created and has received its
    historical pull of transaction information, its transaction history length
    cannot be extended except by deleting and then recreating the Item". Recreating
    costs a Trial slot, and the slot is not returned by /item/remove.
    """
    settings = get_settings()
    inst = db.get(Institution, institution_id)
    if inst is None or not inst.access_token_enc:
        raise HTTPException(404, "Institution not found")
    client = plaid_client.make_client(settings)
    token = plaid_client.create_link_token(
        client, access_token=decrypt(inst.access_token_enc)
    )
    return {"link_token": token, "institution": inst.name, "mode": "update"}


@router.get("/institution-status")
def institution_status(institution_id: str = "ins_12") -> dict:
    """Plaid's own health for an institution.

    `item_logins` is the one that matters when Link fails: it tracks whether *new*
    connections can be established, independently of whether data updates are healthy.
    """
    settings = get_settings()
    if not settings.plaid_ready:
        raise HTTPException(500, "Plaid is not configured")
    client = plaid_client.make_client(settings)
    inst = client.institutions_get_by_id(
        InstitutionsGetByIdRequest(
            institution_id=institution_id,
            country_codes=[CountryCode("US")],
            options={"include_status": True},
        )
    ).institution

    status = getattr(inst, "status", None)
    out = {
        "institution_id": institution_id,
        "name": str(inst.name),
        "oauth": bool(getattr(inst, "oauth", False)),
        "products": [str(p) for p in inst.products],
        "health": {},
    }
    for key in ("item_logins", "transactions_updates", "investments_updates", "auth", "balance"):
        section = getattr(status, key, None) if status else None
        if section is not None:
            out["health"][key] = {
                "status": str(getattr(section, "status", "")),
                "last_status_change": str(getattr(section, "last_status_change", "")),
            }
    return out


@router.get("/", response_class=HTMLResponse)
def link_page() -> str:
    return LINK_PAGE
