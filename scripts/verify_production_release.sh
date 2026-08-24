#!/bin/zsh
set -euo pipefail

APP_PATH=${1:?usage: verify_production_release.sh APP_PATH}
CONTENTS=$APP_PATH/Contents
HELPER=$CONTENTS/Helpers/nowcaster-engine

test -d "$APP_PATH"
test -x "$HELPER"
test -f "$CONTENTS/Resources/engine-manifest.json"
test -f "$CONTENTS/Resources/nowcaster-sbom.cdx.json"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
codesign --verify --strict --verbose=2 "$HELPER"
DETAILS=$(codesign -dv --verbose=4 "$APP_PATH" 2>&1)
print "$DETAILS" | grep -q 'Authority=Developer ID Application:'
print "$DETAILS" | grep -q 'Runtime Version='
if codesign -d --entitlements :- "$APP_PATH" 2>/dev/null | grep -q 'get-task-allow'; then
    print -u2 'Development entitlement get-task-allow is forbidden'
    exit 1
fi
xcrun stapler validate "$APP_PATH"
spctl --assess --type execute --verbose=2 "$APP_PATH"
print 'Production release verification passed.'
