#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
# --clean must not invalidate another project's simultaneous build.
export PYINSTALLER_CONFIG_DIR="$PROJECT_ROOT/build/pyinstaller-cache"
PYTHON=${NOWCASTER_BUILD_PYTHON:-$PROJECT_ROOT/.venv/bin/python}
BUILD_ROOT=$PROJECT_ROOT/build/engine
DIST_ROOT=$BUILD_ROOT/dist

test -x "$PYTHON"
if [[ "${NOWCASTER_REUSE_ENGINE_BUNDLE:-0}" == "1" && -x "$DIST_ROOT/nowcaster-engine" ]]; then
  "$PYTHON" "$PROJECT_ROOT/scripts/engine_manifest.py" \
    --root "$PROJECT_ROOT" --executable "$DIST_ROOT/nowcaster-engine" --verify "$DIST_ROOT/engine-manifest.json"
  print "$DIST_ROOT"
  exit 0
fi

rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT"
"$PYTHON" "$PROJECT_ROOT/scripts/engine_manifest.py" --root "$PROJECT_ROOT" --build-output "$BUILD_ROOT/engine-build.json"
"$PYTHON" -m PyInstaller --clean --noconfirm --onefile --name nowcaster-engine \
  --paths "$PROJECT_ROOT" \
  --add-data "$BUILD_ROOT/engine-build.json:." \
  --add-data "$PROJECT_ROOT/config:config" \
  --collect-submodules src.live_monitor \
  --hidden-import websockets.asyncio.client \
  --hidden-import pytz \
  --exclude-module IPython \
  --exclude-module PIL \
  --exclude-module ipykernel \
  --exclude-module jupyter_client \
  --exclude-module matplotlib \
  --exclude-module nbformat \
  --exclude-module pyarrow \
  --exclude-module pytest \
  --exclude-module tkinter \
  --exclude-module tornado \
  --exclude-module traitlets \
  --exclude-module zmq \
  --distpath "$DIST_ROOT" --workpath "$BUILD_ROOT/work" --specpath "$BUILD_ROOT" \
  "$PROJECT_ROOT/scripts/live_engine_entry.py"
"$PYTHON" "$PROJECT_ROOT/scripts/engine_manifest.py" \
  --root "$PROJECT_ROOT" --executable "$DIST_ROOT/nowcaster-engine" --output "$DIST_ROOT/engine-manifest.json" \
  --retained-build "$BUILD_ROOT/engine-build.json"
print "$DIST_ROOT"
