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
        raise RuntimeError("INVALID_API_KEYS")

    monkeypatch.setattr(link.plaid_client, "create_link_token", refuse)

    resp = client.post("/api/link/setup", json={"client_id": "typo", "secret": "wrong"})
    assert resp.status_code == 400
    assert "rejected" in resp.json()["detail"].lower()
    # Rolled back, so the next launch is not left holding a pair known to fail.
    assert client.get("/api/link/setup").json()["configured"] is False
    assert "typo" not in env_file.read_text()


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
