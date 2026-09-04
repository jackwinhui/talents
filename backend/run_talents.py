"""Entry point for the packaged app.

PyInstaller runs its entry script as a top-level module, not as part of a package,
so `talents/__main__.py` and its relative imports cannot be used directly. This
file exists to give the bundle something with an absolute import to start from.
"""
from __future__ import annotations

from talents.launcher import main

if __name__ == "__main__":
    main()
