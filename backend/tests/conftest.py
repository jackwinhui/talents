"""Test fixtures.

DATABASE_URL is set before importing the app, because the engine is created at
import time from settings. Each test gets a fresh temporary database.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}"

from talents.db import Base, SessionLocal, engine, init_db  # noqa: E402
from talents.models import Account, Institution  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def account(db):
    inst = Institution(name="Test Bank", provider="plaid")
    db.add(inst)
    db.flush()
    acct = Account(institution_id=inst.id, name="Checking", type="depository", is_asset=True)
    db.add(acct)
    db.commit()
    return acct


@pytest.fixture()
def personal_rules(tmp_path, monkeypatch):
    """Point the personal-rules loader at a throwaway file.

    Tests must pass on a fresh clone, where no personal_rules.json exists. Any test
    that needs one writes it here rather than relying on the machine it runs on.
    """
    import json

    from talents.services import merchant_rules

    def write(mapping: dict[str, str]):
        path = tmp_path / "personal_rules.json"
        path.write_text(json.dumps(mapping))
        monkeypatch.setattr(merchant_rules, "PERSONAL_RULES_PATH", path)
        return path

    return write
