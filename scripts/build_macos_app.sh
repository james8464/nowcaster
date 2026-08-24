#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
PACKAGE_ROOT="$PROJECT_ROOT/macos/Nowcaster"
BUILD_ROOT="$PROJECT_ROOT/build"
APP_PATH="$BUILD_ROOT/Nowcaster.app"
CONTENTS_PATH="$APP_PATH/Contents"
IDENTITY=${NOWCASTER_CODESIGN_IDENTITY:--}
PYTHON=${NOWCASTER_BUILD_PYTHON:-$PROJECT_ROOT/.venv/bin/python}

if [[ "$APP_PATH" != "$PROJECT_ROOT/build/Nowcaster.app" ]]; then
    print -u2 "Refusing to build into an unexpected app path: $APP_PATH"
    exit 1
fi

swift build --package-path "$PACKAGE_ROOT" -c release --product NowcasterApp
BIN_PATH=$(swift build --package-path "$PACKAGE_ROOT" -c release --show-bin-path)

rm -rf "$APP_PATH"
mkdir -p "$CONTENTS_PATH/MacOS" "$CONTENTS_PATH/Resources" "$CONTENTS_PATH/Helpers"
install -m 755 "$BIN_PATH/NowcasterApp" "$CONTENTS_PATH/MacOS/Nowcaster"
install -m 644 "$PACKAGE_ROOT/Resources/Info.plist" "$CONTENTS_PATH/Info.plist"

for resource_bundle in "$BIN_PATH"/*.bundle(N); do
    cp -R "$resource_bundle" "$CONTENTS_PATH/Resources/"
done

"$PYTHON" "$PROJECT_ROOT/scripts/generate_sbom.py" --root "$PROJECT_ROOT" \
  --output "$CONTENTS_PATH/Resources/nowcaster-sbom.cdx.json"

if [[ "$IDENTITY" == "-" ]]; then
    SIGN_OPTIONS=(--timestamp=none)
else
    SIGN_OPTIONS=(--timestamp)
fi

if [[ "${NOWCASTER_SKIP_ENGINE_BUNDLE:-0}" != "1" ]]; then
    ENGINE_ROOT=$("$PROJECT_ROOT/scripts/build_engine_bundle.sh")
    install -m 755 "$ENGINE_ROOT/nowcaster-engine" "$CONTENTS_PATH/Helpers/nowcaster-engine"
    codesign --force --options runtime "${SIGN_OPTIONS[@]}" --sign "$IDENTITY" "$CONTENTS_PATH/Helpers/nowcaster-engine"
    "$PYTHON" "$PROJECT_ROOT/scripts/engine_manifest.py" --root "$PROJECT_ROOT" \
      --executable "$CONTENTS_PATH/Helpers/nowcaster-engine" \
      --output "$CONTENTS_PATH/Resources/engine-manifest.json"
fi

codesign --force --options runtime "${SIGN_OPTIONS[@]}" --sign "$IDENTITY" "$APP_PATH"
print "$APP_PATH"
