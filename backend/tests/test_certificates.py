"""Root certificates inside the packaged app.

Python resolves its default CA file to a path inside the python.org framework,
which exists on a machine with python.org Python installed - the developer's -
and on nobody else's. The bundle therefore has to carry its own copy, or every
HTTPS call fails certificate verification and the app reports what looks like a
broken internet connection to a user whose internet is fine.

This is invisible on the machine that builds the app, so it needs a test.
"""
from __future__ import annotations

import importlib

import pytest

from talents import config


@pytest.fixture()
def frozen(tmp_path, monkeypatch):
    """Pretend to be a PyInstaller bundle with certificates unpacked into it."""
    bundled = tmp_path / "certifi" / "cacert.pem"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("-- not a real certificate --")
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config, "RESOURCE_DIR", tmp_path)
    return bundled


def test_the_bundled_certificates_are_used_when_frozen(frozen, monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

    config._use_bundled_certificates()

    import os

    assert os.environ["SSL_CERT_FILE"] == str(frozen)
    # requests keeps its own variable and ignores SSL_CERT_FILE entirely.
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(frozen)


def test_a_certificate_bundle_the_user_chose_is_left_alone(frozen, monkeypatch):
    """Someone behind a corporate proxy has their own root, and it must win."""
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/corporate/roots.pem")

    config._use_bundled_certificates()

    import os

    assert os.environ["SSL_CERT_FILE"] == "/etc/corporate/roots.pem"


def test_running_from_source_touches_nothing(tmp_path, monkeypatch):
    """A checkout already has working certificates; overriding them helps nobody."""
    monkeypatch.setattr(config.sys, "frozen", False, raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    config._use_bundled_certificates()

    import os

    assert "SSL_CERT_FILE" not in os.environ


def test_certifi_is_importable_so_the_spec_can_bundle_it():
    """The spec calls certifi.where() at build time; without it the build breaks."""
    certifi = importlib.import_module("certifi")
    assert certifi.where().endswith("cacert.pem")
