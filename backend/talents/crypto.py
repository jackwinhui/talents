"""Encryption for Plaid access tokens.

The Fernet key lives in the macOS Keychain, not in .env and not in the database.
That way a leaked repo or a stolen talents.db still does not expose bank tokens.

Renaming the app also renames the Keychain service, which strands tokens encrypted
under the old name. `MultiFernet` therefore decrypts against legacy keys too, and
`rotate_stored_tokens()` re-encrypts them under the current key — recovering the
Items instead of re-linking, which would burn irrecoverable Plaid Trial slots.
"""
from __future__ import annotations

import logging
import subprocess

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

log = logging.getLogger("talents.crypto")

_SERVICE = "talents-finance-app"
_LEGACY_SERVICES = ("ledger-finance-app",)
_ACCOUNT = "token-encryption-key"


def _keychain_get(service: str) -> str | None:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", _ACCOUNT, "-w"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _keychain_set(service: str, key: str) -> None:
    subprocess.run(
        ["security", "add-generic-password", "-s", service, "-a", _ACCOUNT, "-w", key, "-U"],
        check=True,
        capture_output=True,
    )


def get_or_create_key() -> bytes:
    existing = _keychain_get(_SERVICE)
    if existing:
        return existing.encode()
    key = Fernet.generate_key()
    _keychain_set(_SERVICE, key.decode())
    return key


def _cipher() -> MultiFernet:
    """Current key first (so it is what encrypts), legacy keys available to decrypt."""
    keys = [Fernet(get_or_create_key())]
    for service in _LEGACY_SERVICES:
        legacy = _keychain_get(service)
        if legacy:
            keys.append(Fernet(legacy.encode()))
    return MultiFernet(keys)


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _cipher().decrypt(ciphertext.encode()).decode()


def rotate_stored_tokens() -> int:
    """Re-encrypt tokens still held under a legacy key. Returns the number rotated."""
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import Institution

    current = Fernet(get_or_create_key())
    cipher = _cipher()
    rotated = 0

    with SessionLocal() as db:
        for inst in db.scalars(select(Institution)).all():
            if not inst.access_token_enc:
                continue
            try:
                current.decrypt(inst.access_token_enc.encode())
                continue  # already under the current key
            except InvalidToken:
                pass
            try:
                inst.access_token_enc = cipher.rotate(inst.access_token_enc.encode()).decode()
                rotated += 1
                log.info("Rotated token for %s onto the current key", inst.name)
            except InvalidToken:
                log.error("Token for %s cannot be decrypted by any known key", inst.name)
        if rotated:
            db.commit()
    return rotated
