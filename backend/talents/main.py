"""Talents — local-first personal finance app.

Binds to 127.0.0.1 by default. Phone access is via Tailscale, never a public port.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import RESOURCE_DIR, get_settings
from .crypto import rotate_stored_tokens
from .db import init_db
from .routers import data, link

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="Talents", version="0.1.2")
app.include_router(link.router)
app.include_router(data.router)

_assets = RESOURCE_DIR / "assets"
if _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=_assets), name="assets")

# Built React app. Vite writes here with base=/app/, so one origin serves both the
# UI and the API and the whole thing works over Tailscale unchanged.
_static = Path(__file__).parent / "static"
if _static.is_dir():
    app.mount("/app", StaticFiles(directory=_static, html=True), name="app")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    rotated = rotate_stored_tokens()
    if rotated:
        logging.getLogger("talents").info("Re-encrypted %d token(s) onto the current key", rotated)


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "plaid_configured": settings.plaid_ready,
        "plaid_env": settings.plaid_env,
        "llm_categorizer": settings.enable_llm_categorizer,
    }


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/app/" if _static.is_dir() else "/api/link/")


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("talents.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
