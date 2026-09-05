"""Settings loaded from environment / .env.

Secrets are never committed. Plaid access tokens are encrypted at rest (see crypto.py);
this module only holds the API credentials needed to talk to Plaid.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _data_dir() -> Path:
    """Where this install keeps the things it writes.

    Running from a checkout that is the repo itself, which keeps development
    self-contained and matches what the scripts and README expect. Inside a .app
    the bundle is read-only and may be replaced wholesale on the next update, so
    the database and credentials have to live outside it or a drag-to-Applications
    upgrade would silently discard two years of history.
    """
    if getattr(sys, "frozen", False):
        path = Path.home() / "Library" / "Application Support" / "Talents"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return REPO_ROOT


DATA_DIR = _data_dir()


def _resource_dir() -> Path:
    """Where the read-only files that ship with the app live.

    Separate from DATA_DIR because these travel in the opposite direction: icons
    and the built UI are part of the bundle and are replaced on every update,
    while the database must survive one. PyInstaller unpacks bundled data under
    `sys._MEIPASS`, which has nothing to do with where this file appears to sit,
    so deriving it from `__file__` finds nothing once frozen.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        return Path(meipass) if meipass else Path(sys.executable).parent
    return REPO_ROOT


RESOURCE_DIR = _resource_dir()


def _use_bundled_certificates() -> None:
    """Point TLS verification at the certificates shipped inside the app.

    Python resolves its default CA file to a path inside the python.org framework
    - `/Library/Frameworks/Python.framework/.../cert.pem`. That exists on a machine
    with python.org Python installed, which means the developer's, and on nobody
    else's. Frozen and left alone, every HTTPS call fails certificate verification,
    which surfaces as a connection error and reads as though the user's internet is
    at fault rather than the app shipping no root certificates.

    Set through the environment because urllib3, requests and ssl all read these,
    and the Plaid SDK builds its own pool manager where passing a path is not an
    option. Anything already set by the user is left alone.
    """
    if not getattr(sys, "frozen", False):
        return
    bundled = RESOURCE_DIR / "certifi" / "cacert.pem"
    if not bundled.is_file():
        return
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        os.environ.setdefault(name, str(bundled))


_use_bundled_certificates()

# One definition: the file the settings are read from is the same one the in-app
# setup writes to. Kept above Settings so the model can point at it directly
# rather than restating the path.
ENV_PATH = DATA_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_PATH, env_file_encoding="utf-8", extra="ignore"
    )

    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "production"

    database_url: str = f"sqlite:///{DATA_DIR / 'talents.db'}"

    # Bind to loopback only. Phone access goes through Tailscale, never a public port.
    host: str = "127.0.0.1"
    port: int = 8787

    # Categorizer LLM fallback is opt-in; rules run fully offline by default.
    enable_llm_categorizer: bool = False

    @property
    def plaid_ready(self) -> bool:
        return bool(self.plaid_client_id and self.plaid_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Re-read the environment after `.env` changes.

    Settings are cached for the life of the process, so credentials saved through
    the UI would otherwise not take effect until the app was restarted — which is
    exactly the friction the in-app setup exists to remove.
    """
    get_settings.cache_clear()
    return get_settings()


def write_env_values(values: dict[str, str], path: Path | None = None) -> Path:
    """Update keys in `.env`, leaving everything else in it alone.

    Rewriting the file wholesale would discard comments and any setting the user
    had edited by hand. Written 0600 because this is where the Plaid secret lives.
    """
    path = path or ENV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)

    updated: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            updated.append(f"{key}={remaining.pop(key)}")
        else:
            updated.append(line)
    updated.extend(f"{key}={value}" for key, value in remaining.items())

    path.write_text("\n".join(updated) + "\n")
    path.chmod(0o600)
    return path
