#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
APP_PATH=${1:?"Expected the built Nowcaster.app path"}
CONTENTS_PATH="$APP_PATH/Contents"
IDENTITY=${NOWCASTER_CODESIGN_IDENTITY:-${EXPANDED_CODE_SIGN_IDENTITY:--}}
PYTHON=${NOWCASTER_BUILD_PYTHON:-$PROJECT_ROOT/.venv/bin/python}
if [[ ! -x "$PYTHON" ]]; then
  PRIMARY_WORKTREE=$(git -C "$PROJECT_ROOT" worktree list --porcelain | sed -n '1s/^worktree //p')
  PYTHON="$PRIMARY_WORKTREE/.venv/bin/python"
fi
SIGN_OPTIONS=(--force --options runtime --sign "$IDENTITY")
[[ "$IDENTITY" == "-" ]] && SIGN_OPTIONS+=(--timestamp=none) || SIGN_OPTIONS+=(--timestamp)

[[ -d "$APP_PATH" ]] || { print -u2 "Missing app bundle: $APP_PATH"; exit 1; }
[[ -x "$PYTHON" ]] || { print -u2 "Missing build Python: $PYTHON"; exit 1; }
export NOWCASTER_BUILD_PYTHON="$PYTHON"
mkdir -p "$CONTENTS_PATH/Resources" "$CONTENTS_PATH/Helpers"

"$PYTHON" "$PROJECT_ROOT/scripts/generate_sbom.py" --root "$PROJECT_ROOT" \
  --output "$CONTENTS_PATH/Resources/nowcaster-sbom.cdx.json"
"$SCRIPT_DIR/generate_macos_icon.sh" \
  "$PROJECT_ROOT/macos/Nowcaster/Sources/NowcasterApp/Resources/AppIcon.png" \
  "$CONTENTS_PATH/Resources/AppIcon.icns"

if [[ "${NOWCASTER_SKIP_ENGINE_BUNDLE:-0}" == "1" ]]; then
  exit 0
fi

ENGINE_ROOT=$("$SCRIPT_DIR/build_engine_bundle.sh")
install -m 755 "$ENGINE_ROOT/nowcaster-engine" "$CONTENTS_PATH/Helpers/nowcaster-engine"
codesign "${SIGN_OPTIONS[@]}" --entitlements "$PROJECT_ROOT/macos/Nowcaster/Resources/Engine.entitlements" \
  "$CONTENTS_PATH/Helpers/nowcaster-engine"
"$PYTHON" "$PROJECT_ROOT/scripts/engine_manifest.py" --root "$PROJECT_ROOT" \
  --executable "$CONTENTS_PATH/Helpers/nowcaster-engine" \
  --output "$CONTENTS_PATH/Resources/engine-manifest.json" \
  --retained-build "$PROJECT_ROOT/build/engine/engine-build.json"

PAPER_ROOT=$(zsh "$SCRIPT_DIR/build_paper_signals_bundle.sh")
rm -rf "$CONTENTS_PATH/Helpers/nowcaster-paper-signals.app"
cp -R "$PAPER_ROOT" "$CONTENTS_PATH/Helpers/nowcaster-paper-signals.app"
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" \
  "$CONTENTS_PATH/Helpers/nowcaster-paper-signals.app/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" \
    "$CONTENTS_PATH/Helpers/nowcaster-paper-signals.app/Contents/Info.plist"
codesign "${SIGN_OPTIONS[@]}" --entitlements "$PROJECT_ROOT/macos/Nowcaster/Resources/Engine.entitlements" \
  "$CONTENTS_PATH/Helpers/nowcaster-paper-signals.app"

OANDA_ROOT=$(zsh "$SCRIPT_DIR/build_oanda_paper_bundle.sh")
rm -rf "$CONTENTS_PATH/Helpers/nowcaster-oanda-paper.app"
cp -R "$OANDA_ROOT" "$CONTENTS_PATH/Helpers/nowcaster-oanda-paper.app"
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" \
  "$CONTENTS_PATH/Helpers/nowcaster-oanda-paper.app/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" \
    "$CONTENTS_PATH/Helpers/nowcaster-oanda-paper.app/Contents/Info.plist"
codesign "${SIGN_OPTIONS[@]}" --entitlements "$PROJECT_ROOT/macos/Nowcaster/Resources/Engine.entitlements" \
  "$CONTENTS_PATH/Helpers/nowcaster-oanda-paper.app"
