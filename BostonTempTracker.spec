# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Boston Temp Tracker — macOS .app bundle
#
# Usage (called by build_mac.sh):
#   pyinstaller BostonTempTracker.spec --noconfirm
#
# Manual build:
#   pip install pyinstaller requests matplotlib certifi
#   pyinstaller BostonTempTracker.spec --noconfirm

import os
import certifi

block_cipher = None

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ["tracker.py"],
    pathex=[],
    binaries=[],
    datas=[
        # Certifi CA bundle — needed for requests HTTPS calls to NWS / WU
        (certifi.where(), "certifi"),
    ],
    hiddenimports=[
        # Matplotlib TkAgg backend (not auto-detected by PyInstaller)
        "matplotlib.backends.backend_tkagg",
        "matplotlib.backends._backend_tk",
        # Tkinter widgets used by tracker.py
        "tkinter",
        "tkinter.ttk",
        "tkinter.simpledialog",
        "tkinter.messagebox",
        # Timezone support
        "zoneinfo",
        "_zoneinfo",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim big packages we don't use to keep the app small
    excludes=[
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "wx", "gi",
        "IPython", "notebook", "jupyter",
        "scipy",
        "pandas",
        "PIL",           # not needed at runtime (only build-time for icon)
        "Pillow",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── Executable ────────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Boston Temp Tracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX compression can cause Gatekeeper issues on macOS
    console=False,        # no terminal window — pure GUI app
    argv_emulation=True,  # proper macOS Apple Events / drag-to-open support
    target_arch=None,     # None = match current machine; set "universal2" for fat binary
    codesign_identity=None,
    entitlements_file=None,
)

# ── Collect ───────────────────────────────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Boston Temp Tracker",
)

# ── App bundle ────────────────────────────────────────────────────────────────
_icon = "assets/icon.icns" if os.path.exists("assets/icon.icns") else None

app = BUNDLE(
    coll,
    name="Boston Temp Tracker.app",
    icon=_icon,
    bundle_identifier="com.bostontemp.tracker",
    version="1.0.0",
    info_plist={
        # Retina display support
        "NSHighResolutionCapable": True,
        # App metadata
        "CFBundleDisplayName": "Boston Temp Tracker",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        # Category (shows correct icon in Finder sidebar)
        "LSApplicationCategoryType": "public.app-category.weather",
        # Minimum macOS version (10.14 Mojave+ for zoneinfo support)
        "LSMinimumSystemVersion": "10.14.0",
        # Standard Cocoa app class
        "NSPrincipalClass": "NSApplication",
        "NSAppleScriptEnabled": False,
        # Network access — needed for NWS API and METAR fetches
        "NSAppTransportSecurity": {
            "NSAllowsArbitraryLoads": False,
            "NSExceptionDomains": {
                "api.weather.gov": {"NSExceptionAllowsInsecureHTTPLoads": False},
                "tgftp.weather.gov": {"NSExceptionAllowsInsecureHTTPLoads": False},
            },
        },
    },
)
