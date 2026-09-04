"""Entry point for the packaged Mac app.

The UI is a React app served by the local API and shown in a native window rather
than a browser tab. Same origin, same bundle either way — the window is only a
shell around it, so nothing about the frontend or the API changes. macOS draws it
with its own WKWebView, so there is no second browser engine inside the app.

Two constraints shape the structure. macOS insists the GUI owns the main thread,
so uvicorn runs on a background thread and the window blocks on the main one. And
uvicorn only installs signal handlers from the main thread, so that is switched
off explicitly rather than left to raise.

Double-clicking a .app gives you no terminal, so anything a person needs to see
has to reach them another way: the log goes to Application Support beside the
database, and a failure to start raises a native dialog instead of dying silently
inside a bundle nobody can look into.
"""
from __future__ import annotations

import logging
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from .config import DATA_DIR, get_settings

DEFAULT_PORT = 8787
LOG_PATH = DATA_DIR / "talents.log"

WINDOW_TITLE = "Talents"
WINDOW_SIZE = (1280, 860)
# Narrower than this and the dashboard's two-column layout starts to collapse.
WINDOW_MIN_SIZE = (940, 600)

log = logging.getLogger("talents.launcher")


def _port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def choose_port(preferred: int = DEFAULT_PORT, host: str = "127.0.0.1") -> int:
    """The documented port when it is free, otherwise anything the OS offers."""
    if _port_is_free(preferred, host):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def already_running(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> bool:
    """True when *our* app is on the port, rather than something else entirely.

    Established by asking, so an unrelated service on 8787 is not mistaken for a
    running copy and handed a window onto it.
    """
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1.5) as resp:
            return b'"status"' in resp.read(200)
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def wait_for_health(url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1.0):
                return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.2)
    return False


def _alert(message: str) -> None:
    """Say something where a person double-clicking an icon will see it."""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display alert "Talents could not start" message "{message}" as critical'],
            capture_output=True, timeout=30,
        )
    except Exception:
        print(message, file=sys.stderr)


def serve(host: str, port: int) -> None:
    """Run the API. Called on a background thread, so it must not touch signals."""
    import uvicorn

    from .main import app

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    # Only the main thread may install these, and the window owns it.
    server.install_signal_handlers = lambda: None
    server.run()


def open_window(url: str) -> bool:
    """Show the UI in a native window. False if this machine cannot.

    Degrades rather than fails: the window is the nice-to-have, and the app is
    identical served into a browser.
    """
    try:
        import webview
    except ImportError:
        log.warning("pywebview is unavailable; falling back to the browser")
        return False

    try:
        webview.create_window(
            WINDOW_TITLE,
            f"{url}/app/",
            width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1],
            min_size=WINDOW_MIN_SIZE,
            # Off by default in a webview, and its absence is baffling in what
            # looks like an ordinary app window.
            text_select=True,
        )
        webview.start()
        return True
    except Exception:
        log.exception("Native window failed; falling back to the browser")
        return False


def open_browser(url: str) -> None:
    subprocess.run(["open", f"{url}/app/"], capture_output=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
    )
    settings = get_settings()
    host = settings.host

    # Something is already serving: show that rather than starting a second
    # server against the same database.
    if already_running(DEFAULT_PORT, host):
        url = f"http://{host}:{DEFAULT_PORT}"
        log.info("Already running on %d; opening a window onto it", DEFAULT_PORT)
        if not open_window(url):
            open_browser(url)
        return

    port = choose_port(DEFAULT_PORT, host)
    url = f"http://{host}:{port}"
    log.info("Starting Talents on %s (data in %s)", url, DATA_DIR)

    # Daemon: closing the window ends the process and takes the server with it.
    threading.Thread(target=serve, args=(host, port), daemon=True).start()

    if not wait_for_health(url):
        log.error("Server did not become healthy within 30s")
        _alert(f"The server did not start within 30 seconds. See {LOG_PATH}")
        return

    if not open_window(url):
        open_browser(url)
        # Nothing is blocking now, so hold the process open for the browser tab.
        log.info("Serving in the browser; press Ctrl-C to stop")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
