#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
PYTHON=${NOWCASTER_BUILD_PYTHON:-$PROJECT_ROOT/.venv/bin/python}
IDENTITY=${NOWCASTER_CODESIGN_IDENTITY:--}
BUILD_ROOT="$PROJECT_ROOT/build/oanda-paper"
export PYINSTALLER_CONFIG_DIR="$PROJECT_ROOT/build/pyinstaller-cache"

[[ -x "$PYTHON" ]] || { print -u2 "Missing build Python"; exit 1; }
mkdir -p "$BUILD_ROOT"
"$PYTHON" -m PyInstaller --clean --noconfirm --onedir --windowed --name nowcaster-oanda-paper \
  --osx-bundle-identifier com.james8464.nowcaster.oanda-paper \
  --codesign-identity "$IDENTITY" \
  --osx-entitlements-file "$PROJECT_ROOT/macos/Nowcaster/Resources/Engine.entitlements" \
  --paths "$PROJECT_ROOT" \
  --exclude-module IPython --exclude-module PIL --exclude-module ipykernel \
  --exclude-module jupyter_client --exclude-module matplotlib --exclude-module nbformat \
  --exclude-module pyarrow --exclude-module pytest --exclude-module tkinter \
  --distpath "$BUILD_ROOT/dist" --workpath "$BUILD_ROOT/work" --specpath "$BUILD_ROOT" \
  "$PROJECT_ROOT/scripts/intraday_service_entry.py"
print "$BUILD_ROOT/dist/nowcaster-oanda-paper.app"
