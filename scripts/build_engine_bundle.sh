#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
PYTHON=${NOWCASTER_BUILD_PYTHON:-$PROJECT_ROOT/.venv/bin/python}
BUILD_ROOT=$PROJECT_ROOT/build/engine
DIST_ROOT=$BUILD_ROOT/dist

test -x "$PYTHON"
if [[ "${NOWCASTER_REUSE_ENGINE_BUNDLE:-0}" == "1" && -x "$DIST_ROOT/nowcaster-engine" ]]; then
  "$PYTHON" "$PROJECT_ROOT/scripts/engine_manifest.py" \
    --root "$PROJECT_ROOT" --executable "$DIST_ROOT/nowcaster-engine" --output "$DIST_ROOT/engine-manifest.json"
  print "$DIST_ROOT"
  exit 0
fi

rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT"
"$PYTHON" -m PyInstaller --clean --noconfirm --onefile --name nowcaster-engine \
  --paths "$PROJECT_ROOT" \
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
  --exclude-module scipy \
  --exclude-module tkinter \
  --exclude-module tornado \
  --exclude-module traitlets \
  --exclude-module zmq \
  --distpath "$DIST_ROOT" --workpath "$BUILD_ROOT/work" --specpath "$BUILD_ROOT" \
  "$PROJECT_ROOT/scripts/live_engine_entry.py"
"$PYTHON" "$PROJECT_ROOT/scripts/engine_manifest.py" \
  --root "$PROJECT_ROOT" --executable "$DIST_ROOT/nowcaster-engine" --output "$DIST_ROOT/engine-manifest.json"
print "$DIST_ROOT"
