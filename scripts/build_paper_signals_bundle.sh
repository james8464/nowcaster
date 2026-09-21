#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
PYTHON=${NOWCASTER_BUILD_PYTHON:-$PROJECT_ROOT/.venv/bin/python}
IDENTITY=${NOWCASTER_CODESIGN_IDENTITY:--}
BUILD_ROOT="$PROJECT_ROOT/build/paper-signals"
DIST_ROOT="$BUILD_ROOT/dist"

test -x "$PYTHON"
mkdir -p "$BUILD_ROOT"
# Exact source bytes are inputs to the evaluator's immutable identity checks.
RESOURCE_ARGS=(--add-data "$PROJECT_ROOT/config/strategies.yaml:config"
  --add-data "$PROJECT_ROOT/src/research/round_two_walkforward.py:src/research")
for source in "$PROJECT_ROOT"/src/strategies/**/*.py(N); do
  relative=${source#$PROJECT_ROOT/}
  RESOURCE_ARGS+=(--add-data "$source:${relative:h}")
done
"$PYTHON" -m PyInstaller --clean --noconfirm --onedir --windowed --name nowcaster-paper-signals \
  --osx-bundle-identifier com.james8464.nowcaster.paper-signals \
  --codesign-identity "$IDENTITY" \
  --osx-entitlements-file "$PROJECT_ROOT/macos/Nowcaster/Resources/Engine.entitlements" \
  --paths "$PROJECT_ROOT" \
  "${RESOURCE_ARGS[@]}" \
  --exclude-module IPython --exclude-module PIL --exclude-module ipykernel \
  --exclude-module jupyter_client --exclude-module matplotlib --exclude-module nbformat \
  --exclude-module pyarrow --exclude-module pytest \
  --exclude-module tkinter --exclude-module tornado --exclude-module traitlets --exclude-module zmq \
  --distpath "$DIST_ROOT" --workpath "$BUILD_ROOT/work" --specpath "$BUILD_ROOT" \
  "$PROJECT_ROOT/scripts/run_live_paper_signals.py"
print "$DIST_ROOT/nowcaster-paper-signals.app"
