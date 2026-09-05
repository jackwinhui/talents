"""First-run setup.

Somebody opening the app for the first time has no credentials, and the only way
in used to be writing a dotfile by hand in a directory they had to be told about.
These cover the path that replaced it, and in particular that a bad pair is never
left behind: the rollback is the whole reason validation happens before saving.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from talents import config
from talents.main import app
from talents.routers import link


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    """Point both the settings reader and the writer at a throwaway .env.

    Without this the tests would rewrite the developer's own credentials, since
    the app deliberately keeps one .env per install rather than per run. The
    reader's path is baked into the model config when the class is defined, so
    it has to be redirected alongside the module constant.
    """
    path = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", path)
    monkeypatch.setattr(link, "ENV_PATH", path)
    monkeypatch.setitem(config.Settings.model_config, "env_file", path)
    config.get_settings.cache_clear()
    yield path
    config.get_settings.cache_clear()


@pytest.fixture()
def client(env_file, monkeypatch):
    # A fresh install has no credentials *anywhere*. Setting these to "" instead
    # would not simulate that: real environment variables outrank the .env file,
    # so an empty one would mask whatever the setup endpoint just wrote.
    monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAID_SECRET", raising=False)
    config.get_settings.cache_clear()
    return TestClient(app)


def test_a_fresh_install_reports_itself_unconfigured(client):
    body = client.get("/api/link/setup").json()
    assert body["configured"] is False
    assert body["client_id_tail"] == ""


def test_connecting_before_setup_is_not_an_error_page(client):
    """409, not 500: nothing is broken, the app just has not been told anything yet."""
    resp = client.get("/api/link/token?kind=bank")
    assert resp.status_code == 409
    assert "not set up" in resp.json()["detail"].lower()


def test_credentials_that_plaid_rejects_are_not_kept(client, env_file, monkeypatch):
    """A typo must not be written to disk and discovered later at the bank."""
    def refuse(*args, **kwargs):
        raise plaid_refusal("INVALID_API_KEYS")

    monkeypatch.setattr(link.plaid_client, "make_client", lambda s: object())
    monkeypatch.setattr(link.plaid_client, "create_link_token", refuse)

    resp = client.post("/api/link/setup", json={"client_id": FAKE_ID, "secret": FAKE_SECRET})
    assert resp.status_code == 400
    assert "Production access" in resp.json()["detail"]
    # Rolled back, so the next launch is not left holding a pair known to fail.
    assert client.get("/api/link/setup").json()["configured"] is False
    assert FAKE_ID not in env_file.read_text()


def test_working_credentials_are_saved_and_take_effect_immediately(
    client, env_file, monkeypatch,
):
    """Settings are cached for the process, so saving must also refresh them."""
    monkeypatch.setattr(link.plaid_client, "create_link_token", lambda *a, **k: "link-test")
    monkeypatch.setattr(link.plaid_client, "make_client", lambda s: object())

    resp = client.post(
        "/api/link/setup", json={"client_id": "abc123", "secret": "s3cret"}
    )
    assert resp.status_code == 200
    assert resp.json()["configured"] is True

    # No restart: the running process reports itself configured straight away.
    status = client.get("/api/link/setup").json()
    assert status["configured"] is True
    assert status["client_id_tail"] == "c123"
    assert "PLAID_CLIENT_ID=abc123" in env_file.read_text()


def test_saving_keeps_the_rest_of_the_file(client, env_file, monkeypatch):
    """The file is shared with hand-edited settings and comments."""
    env_file.write_text("# my notes\nHOST=0.0.0.0\nPLAID_CLIENT_ID=old\n")
    monkeypatch.setattr(link.plaid_client, "create_link_token", lambda *a, **k: "link-test")
    monkeypatch.setattr(link.plaid_client, "make_client", lambda s: object())

    client.post("/api/link/setup", json={"client_id": "new", "secret": "s"})

    text = env_file.read_text()
    assert "# my notes" in text
    assert "HOST=0.0.0.0" in text
    assert "PLAID_CLIENT_ID=new" in text
    assert "PLAID_CLIENT_ID=old" not in text


def test_blank_input_is_refused_before_anything_is_written(client, env_file):
    resp = client.post("/api/link/setup", json={"client_id": "  ", "secret": ""})
    assert resp.status_code == 400
    assert not env_file.exists() or "PLAID_CLIENT_ID=" not in env_file.read_text()


def test_the_secret_is_never_sent_back(client, env_file, monkeypatch):
    """The status endpoint is read by the UI; it must not hand the secret out."""
    monkeypatch.setattr(link.plaid_client, "create_link_token", lambda *a, **k: "link-test")
    monkeypatch.setattr(link.plaid_client, "make_client", lambda s: object())
    client.post("/api/link/setup", json={"client_id": "abc123", "secret": "topsecret"})

    body = client.get("/api/link/setup").json()
    assert "topsecret" not in str(body)
    assert "abc123" not in str(body)


# Plaid issues 24-hex client IDs and 30-hex secrets. Tests use correctly shaped
# fakes so they exercise the refusal path rather than the format guard.
FAKE_ID = "a1b2c3d4e5f60718293a4b5c"
FAKE_SECRET = "0f1e2d3c4b5a69788796a5b4c3d2e1"


def plaid_refusal(code: str) -> Exception:
    """A refusal shaped like the SDK's, which carries its reason in `body`."""
    exc = RuntimeError("plaid said no")
    exc.body = f'{{"error_code": "{code}", "error_type": "INVALID_INPUT"}}'
    return exc


def test_sandbox_keys_are_named_as_such_rather_than_called_invalid(client, monkeypatch):
    """The most common first-run mistake, and the least obvious from the error.

    Plaid answers INVALID_API_KEYS whether the keys are wrong or merely pointed at
    the wrong environment, so being told they are invalid sends someone off to
    re-copy keys that were right all along.
    """
    def only_sandbox_works(plaid_api, *args, **kwargs):
        if getattr(plaid_api, "_env", None) == "sandbox":
            return "link-sandbox-token"
        raise plaid_refusal("INVALID_API_KEYS")

    monkeypatch.setattr(
        link.plaid_client, "make_client",
        lambda s: type("C", (), {"_env": s.plaid_env})(),
    )
    monkeypatch.setattr(link.plaid_client, "create_link_token", only_sandbox_works)

    resp = client.post("/api/link/setup", json={"client_id": FAKE_ID, "secret": FAKE_SECRET})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Sandbox" in detail
    assert "Production secret" in detail


def test_keys_that_work_nowhere_are_reported_as_wrong(client, monkeypatch):
    monkeypatch.setattr(link.plaid_client, "make_client", lambda s: object())
    monkeypatch.setattr(
        link.plaid_client, "create_link_token",
        lambda *a, **k: (_ for _ in ()).throw(plaid_refusal("INVALID_API_KEYS")),
    )

    detail = client.post(
        "/api/link/setup", json={"client_id": FAKE_ID, "secret": FAKE_SECRET}
    ).json()["detail"]
    # Correctly shaped keys that work nowhere: the account, not the typing.
    assert "Production access" in detail
    assert "Trial plan" in detail


def test_a_different_refusal_is_passed_through_not_guessed_at(client, monkeypatch):
    """Not every rejection is about the keys; saying so would send people in circles."""
    monkeypatch.setattr(link.plaid_client, "make_client", lambda s: object())
    monkeypatch.setattr(
        link.plaid_client, "create_link_token",
        lambda *a, **k: (_ for _ in ()).throw(plaid_refusal("PRODUCTS_NOT_ENABLED")),
    )

    detail = client.post(
        "/api/link/setup", json={"client_id": FAKE_ID, "secret": FAKE_SECRET}
    ).json()["detail"]
    assert "PRODUCTS_NOT_ENABLED" in detail


def test_a_failed_attempt_still_leaves_nothing_behind(client, env_file, monkeypatch):
    """Diagnosis must not come at the cost of the rollback."""
    monkeypatch.setattr(link.plaid_client, "make_client", lambda s: object())
    monkeypatch.setattr(
        link.plaid_client, "create_link_token",
        lambda *a, **k: (_ for _ in ()).throw(plaid_refusal("INVALID_API_KEYS")),
    )

    client.post("/api/link/setup", json={"client_id": FAKE_ID, "secret": FAKE_SECRET})
    assert client.get("/api/link/setup").json()["configured"] is False
    assert FAKE_ID not in env_file.read_text()
