"""What Link is asked for when an institution is connected.

Everything in `products` is a hard requirement, so the list is not a wish list:
Plaid refuses an institution outright — "Connectivity not supported" — if it
cannot serve every entry. Marcus by Goldman Sachs (ins_52) is savings-only and
reports no liabilities, so requiring them there made the institution unlinkable.
"""
from __future__ import annotations

from talents.providers import plaid_client
from talents.routers.link import IF_SUPPORTED_BY_KIND, PRODUCTS_BY_KIND


class StubClient:
    """Captures the request instead of calling Plaid."""

    def __init__(self):
        self.request = None

    def link_token_create(self, request):
        self.request = request
        return type("Resp", (), {"link_token": "link-test-123"})()


def build(**kw) -> dict:
    client = StubClient()
    plaid_client.create_link_token(client, **kw)
    req = client.request
    return {
        "products": [str(p) for p in getattr(req, "products", [])],
        "if_supported": [str(p) for p in getattr(req, "required_if_supported_products", [])],
        "has_products": "products" in req,
    }


def test_a_savings_only_bank_is_not_turned_away_for_liabilities():
    """The Marcus case: liabilities must not be a condition of connecting a bank."""
    out = build(
        products=PRODUCTS_BY_KIND["bank"],
        required_if_supported=IF_SUPPORTED_BY_KIND["bank"],
    )
    assert out["products"] == ["transactions"]
    assert "liabilities" not in out["products"]
    # Still asked for, because a product cannot be added to an Item later.
    assert out["if_supported"] == ["liabilities"]


def test_liabilities_is_never_in_both_lists():
    """Plaid rejects a product that is both required and required-if-supported."""
    out = build(products=["transactions", "liabilities"], required_if_supported=["liabilities"])
    assert out["if_supported"] == []


def test_a_brokerage_asks_only_for_investments():
    out = build(
        products=PRODUCTS_BY_KIND["investments"],
        required_if_supported=IF_SUPPORTED_BY_KIND["investments"],
    )
    assert out["products"] == ["investments"]
    assert out["if_supported"] == []


def test_update_mode_sends_no_products_at_all():
    """Re-authorising an existing Item must not restate what it was created with."""
    out = build(products=["transactions"], required_if_supported=["liabilities"],
                access_token="access-test-abc")
    assert out["has_products"] is False
    assert out["if_supported"] == []
