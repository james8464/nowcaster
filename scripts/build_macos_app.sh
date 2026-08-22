#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
PACKAGE_ROOT="$PROJECT_ROOT/macos/Nowcaster"
BUILD_ROOT="$PROJECT_ROOT/build"
APP_PATH="$BUILD_ROOT/Nowcaster.app"
CONTENTS_PATH="$APP_PATH/Contents"
IDENTITY=${NOWCASTER_CODESIGN_IDENTITY:--}

if [[ "$APP_PATH" != "$PROJECT_ROOT/build/Nowcaster.app" ]]; then
    print -u2 "Refusing to build into an unexpected app path: $APP_PATH"
    exit 1
fi

swift build --package-path "$PACKAGE_ROOT" -c release --product NowcasterApp
BIN_PATH=$(swift build --package-path "$PACKAGE_ROOT" -c release --show-bin-path)

rm -rf "$APP_PATH"
mkdir -p "$CONTENTS_PATH/MacOS" "$CONTENTS_PATH/Resources"
install -m 755 "$BIN_PATH/NowcasterApp" "$CONTENTS_PATH/MacOS/Nowcaster"
install -m 644 "$PACKAGE_ROOT/Resources/Info.plist" "$CONTENTS_PATH/Info.plist"

for resource_bundle in "$BIN_PATH"/*.bundle(N); do
    cp -R "$resource_bundle" "$CONTENTS_PATH/Resources/"
done

codesign --force --deep --sign "$IDENTITY" "$APP_PATH"
print "$APP_PATH"
