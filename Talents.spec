# PyInstaller spec for the Talents Mac app.
#
# Two things here are not optional. The built React app under talents/static is
# data, not code, so PyInstaller will not find it by following imports and it has
# to be listed. And uvicorn loads its protocol implementations by string name at
# runtime, which the dependency analyser cannot see, so those modules are named
# explicitly or the server starts and then fails to serve anything.
from pathlib import Path

import certifi
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH)
PKG = ROOT / "backend" / "talents"

datas = [
    (str(PKG / "static"), "talents/static"),
    (str(ROOT / "assets"), "assets"),
    # Root certificates. Python resolves these to a path inside the python.org
    # framework, which is present on a developer's Mac and on nobody else's, so
    # without shipping them every HTTPS call fails with a certificate error that
    # looks like the user's internet is broken.
    (certifi.where(), "certifi"),
]

hiddenimports = [
    # Chosen by name at runtime by uvicorn, so nothing imports them directly.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # SQLAlchemy resolves its dialect from the URL string.
    "sqlalchemy.dialects.sqlite",
    "certifi",
    *collect_submodules("plaid"),
    *collect_submodules("pydantic_settings"),
    # The native window. pywebview picks its backend at runtime by name, and the
    # Cocoa one reaches WKWebView through pyobjc, so none of this is reachable by
    # following imports from the entry point.
    "webview.platforms.cocoa",
    "objc",
    "Foundation",
    "AppKit",
    "WebKit",
    "Quartz",
]

a = Analysis(
    [str(ROOT / "backend" / "run_talents.py")],
    pathex=[str(ROOT / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Pulled in by matplotlib-adjacent dependencies and never used; excluding them
    # keeps the bundle from doubling in size.
    excludes=["tkinter", "matplotlib", "PIL", "pytest", "IPython", "numpy.testing"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Talents",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Talents",
)

app = BUNDLE(
    coll,
    name="Talents.app",
    icon=str(ROOT / "assets" / "Talents.icns"),
    bundle_identifier="com.talents.finance",
    info_plist={
        "CFBundleName": "Talents",
        "CFBundleDisplayName": "Talents",
        "CFBundleShortVersionString": "0.1.3",
        "CFBundleVersion": "0.1.3",
        # A real windowed app, not a background helper: it owns a Dock icon and a
        # menu bar, and the window is the app rather than a browser tab.
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "Local-first. Your data never leaves your Mac.",
    },
)
