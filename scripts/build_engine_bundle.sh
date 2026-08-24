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
  --collect-submodules src.deep_research \
  --distpath "$DIST_ROOT" --workpath "$BUILD_ROOT/work" --specpath "$BUILD_ROOT" \
  "$PROJECT_ROOT/scripts/engine_entry.py"
"$PYTHON" "$PROJECT_ROOT/scripts/engine_manifest.py" \
  --root "$PROJECT_ROOT" --executable "$DIST_ROOT/nowcaster-engine" --output "$DIST_ROOT/engine-manifest.json"
print "$DIST_ROOT"
