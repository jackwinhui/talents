"""Plaid client.

Extends the original notion-finance-sync client with the products the app needs:
transactions, investments (Fidelity), liabilities (card due dates/APRs), and balances.

Plaid Trial notes baked in here:
  * An Item is consumed only on a SUCCESSFUL public_token exchange, and /item/remove
    does NOT free a slot. Never delete/recreate Items casually.
  * Do not upgrade off Trial: Fidelity then needs a manual request with no SLA.
"""
from __future__ import annotations

import logging

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.liabilities_get_request import LiabilitiesGetRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_transactions import LinkTokenTransactions
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from ..config import Settings

log = logging.getLogger("talents.plaid")

_HOSTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


def make_client(settings: Settings) -> plaid_api.PlaidApi:
    configuration = plaid.Configuration(
        host=_HOSTS.get(settings.plaid_env.lower(), plaid.Environment.Production),
        api_key={"clientId": settings.plaid_client_id, "secret": settings.plaid_secret},
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def create_link_token(
    client: plaid_api.PlaidApi,
    products: list[str] | None = None,
    days_requested: int = 730,
    access_token: str | None = None,
    required_if_supported: list[str] | None = None,
) -> str:
    """Create a Link token.

    No redirect_uri is supplied: the desktop-browser popup OAuth flow needs none,
    which is precisely why this app does not require a public HTTPS endpoint.

    `days_requested` matters a great deal and is easy to miss — Plaid defaults to
    only **90 days** of transaction history. 730 is the maximum, it is applied when
    the Item is created, and it is **permanent**: history not requested up front
    cannot be added later without destroying and recreating the Item, which costs
    another Trial slot.

    `required_if_supported` is for products that are wanted wherever they exist but
    must never stop an institution from linking. Anything in `products` is a hard
    requirement, and Link refuses the institution outright — "Connectivity not
    supported" — if it cannot serve every one of them. A savings-only bank has no
    liabilities to report, so asking for them there is asking to be turned away.
    Plaid requires these to be disjoint from `products`.

    Passing `access_token` opens Link in **update mode** against an existing Item,
    which re-authorizes it *without* consuming another Trial Item. Use this when a
    login breaks (ITEM_LOGIN_REQUIRED) or credentials change.

    Update mode must not send `products`, and deliberately does not send
    `days_requested`: per Plaid, once an Item has taken its historical pull the
    history length "cannot be extended except by deleting and then recreating the
    Item". Sending it in update mode is silently ignored, which is worse than not
    sending it, because it looks like it worked.
    """
    requested = products or ["transactions"]
    kwargs: dict = {
        "user": LinkTokenCreateRequestUser(client_user_id="talents-local-user"),
        "client_name": "Talents",
        "country_codes": [CountryCode("US")],
        "language": "en",
    }
    if access_token:
        kwargs["access_token"] = access_token
    else:
        kwargs["products"] = [Products(p) for p in requested]
        if required_if_supported:
            kwargs["required_if_supported_products"] = [
                Products(p) for p in required_if_supported if p not in requested
            ]
        if "transactions" in requested:
            kwargs["transactions"] = LinkTokenTransactions(days_requested=days_requested)

    return client.link_token_create(LinkTokenCreateRequest(**kwargs)).link_token


def exchange_public_token(client: plaid_api.PlaidApi, public_token: str) -> tuple[str, str]:
    """Exchange a public token. NOTE: this is what consumes a Trial Item slot."""
    resp = client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=public_token)
    )
    return resp.access_token, resp.item_id


def get_item(client: plaid_api.PlaidApi, access_token: str):
    return client.item_get(ItemGetRequest(access_token=access_token)).item


def get_institution_name(client: plaid_api.PlaidApi, institution_id: str) -> str | None:
    try:
        resp = client.institutions_get_by_id(
            InstitutionsGetByIdRequest(
                institution_id=institution_id, country_codes=[CountryCode("US")]
            )
        )
        return resp.institution.name
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fetch institution name: %s", exc)
        return None


def get_accounts(client: plaid_api.PlaidApi, access_token: str):
    """Free, cached balances. Use this for routine loads, not /accounts/balance/get."""
    return client.accounts_get(AccountsGetRequest(access_token=access_token)).accounts


def sync_transactions(client: plaid_api.PlaidApi, access_token: str, cursor: str | None):
    """Drain all pages of /transactions/sync."""
    added, modified, removed = [], [], []
    has_more, next_cursor = True, cursor

    while has_more:
        kwargs = {"access_token": access_token}
        if next_cursor:
            kwargs["cursor"] = next_cursor
        resp = client.transactions_sync(TransactionsSyncRequest(**kwargs))
        added.extend(resp.added)
        modified.extend(resp.modified)
        removed.extend(resp.removed)
        has_more, next_cursor = resp.has_more, resp.next_cursor

    return added, modified, removed, next_cursor


def get_holdings(client: plaid_api.PlaidApi, access_token: str):
    """Fidelity holdings. `cost_basis` may be NULL — the app supports manual entry."""
    resp = client.investments_holdings_get(
        InvestmentsHoldingsGetRequest(access_token=access_token)
    )
    return resp.accounts, resp.holdings, resp.securities


def get_liabilities(client: plaid_api.PlaidApi, access_token: str):
    """Card APRs, statement balances, minimum payments, due dates."""
    return client.liabilities_get(LiabilitiesGetRequest(access_token=access_token)).liabilities
