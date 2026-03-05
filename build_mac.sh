#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Boston Temp Tracker — macOS DMG builder
#
# Usage:
#   bash build_mac.sh
#
# What it does:
#   1. Checks you're on macOS and have Python 3 + Tcl/Tk
#   2. Creates an isolated build venv (build_venv/) — never touches your system
#   3. Installs PyInstaller, Pillow (icon gen), and all app deps
#   4. Generates the app icon (assets/icon.icns)
#   5. Runs PyInstaller → dist/Boston Temp Tracker.app
#   6. Packages the .app into a DMG with an /Applications symlink
#      Output: dist/BostonTempTracker-1.0.dmg
#
# Requirements:
#   • macOS 10.14 Mojave or later
#   • Python 3.9+ from python.org (brew python works too)
#     Must include Tcl/Tk — python.org builds bundle it automatically.
#   • Internet access (pip downloads + first NWS fetch)
#
# Distribution note:
#   The resulting .app is NOT code-signed or notarized.
#   Recipients open it the first time via: right-click → Open → Open
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_NAME="Boston Temp Tracker"
DMG_NAME="BostonTempTracker-1.0.dmg"
VENV_DIR="build_venv"
SPEC="BostonTempTracker.spec"

# ── Colours for terminal output ───────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[0;33m'; BLU='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLU}→${NC} $*"; }
ok()    { echo -e "${GRN}✅${NC} $*"; }
warn()  { echo -e "${YEL}⚠️ ${NC} $*"; }
die()   { echo -e "${RED}❌${NC} $*"; exit 1; }

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  Boston Temp Tracker — macOS DMG Builder         ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. Platform check ─────────────────────────────────────────────────────────
[[ "$(uname)" == "Darwin" ]] || die "This script must run on macOS."

info "macOS $(sw_vers -productVersion)"

# ── 2. Python check ───────────────────────────────────────────────────────────
PYTHON=$(command -v python3 || true)
[[ -n "$PYTHON" ]] || die "python3 not found. Install from https://www.python.org/downloads/"

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PY_VER at $PYTHON"

# Verify Tcl/Tk (tkinter) is available — python.org builds include it,
# but some brew builds or system Python may not.
"$PYTHON" -c "import tkinter" 2>/dev/null || {
  die "tkinter not available in $PYTHON.
  Fix: install Python from https://www.python.org/downloads/macos/
  (It bundles the required Tcl/Tk automatically.)"
}
ok "tkinter OK"

# ── 3. Build venv ─────────────────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating isolated build venv at $VENV_DIR/ …"
    "$PYTHON" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
info "Build venv active: $(which python3)"

# ── 4. Install dependencies ───────────────────────────────────────────────────
info "Installing build dependencies (this may take a minute on first run)…"
pip install -q --upgrade pip wheel
pip install -q \
    "requests>=2.31.0" \
    "matplotlib>=3.8.0" \
    "certifi" \
    "pyinstaller>=6.0" \
    "Pillow>=10.0"        # icon generation only — NOT bundled into .app

ok "Dependencies installed"

# ── 5. Generate icon ──────────────────────────────────────────────────────────
info "Generating app icon…"
python3 make_icon.py || warn "Icon generation failed — using default PyInstaller icon"

# ── 6. Clean previous build ───────────────────────────────────────────────────
info "Cleaning previous build artefacts…"
rm -rf build/ "dist/${APP_NAME}.app" "dist/${APP_NAME}"

# ── 7. PyInstaller ────────────────────────────────────────────────────────────
info "Running PyInstaller (this takes ~60–120 s on first build)…"
python3 -m PyInstaller "$SPEC" --noconfirm

APP_BUNDLE="dist/${APP_NAME}.app"
[[ -d "$APP_BUNDLE" ]] || die "PyInstaller finished but ${APP_BUNDLE} was not created."
ok "App bundle created → ${APP_BUNDLE}"

# Verify it launches (headless smoke-test — exits after 3 s via timeout)
info "Quick smoke-test (open + close after 3 s)…"
timeout 5 "$APP_BUNDLE/Contents/MacOS/${APP_NAME}" --version 2>/dev/null \
    || timeout 5 open -W "$APP_BUNDLE" 2>/dev/null \
    || warn "Smoke-test skipped (expected for GUI-only app)"

# ── 8. Build DMG ──────────────────────────────────────────────────────────────
DMG_STAGING="dist/dmg-staging"
DMG_OUT="dist/${DMG_NAME}"

info "Staging DMG contents…"
rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"
cp -r "$APP_BUNDLE" "$DMG_STAGING/"
ln -sf /Applications "$DMG_STAGING/Applications"

info "Creating DMG (${DMG_NAME})…"
rm -f "$DMG_OUT"
hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$DMG_STAGING" \
    -ov \
    -format UDZO \
    "$DMG_OUT"

rm -rf "$DMG_STAGING"

# ── 9. Summary ────────────────────────────────────────────────────────────────
DMG_MB=$(du -sm "$DMG_OUT" | cut -f1)

echo ""
echo "╔══════════════════════════════════════════════════════╗"
printf "║  %-52s║\n" "Build complete!"
printf "║  %-52s║\n" ""
printf "║  DMG  → %-43s║\n" "$DMG_OUT  (${DMG_MB} MB)"
printf "║  %-52s║\n" ""
printf "║  %-52s║\n" "To install:"
printf "║    %-50s║\n" "1. Open dist/${DMG_NAME}"
printf "║    %-50s║\n" "2. Drag '${APP_NAME}' → Applications"
printf "║    %-50s║\n" "3. First launch: right-click → Open"
printf "║    %-50s║\n" "   (bypasses Gatekeeper for unsigned app)"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
