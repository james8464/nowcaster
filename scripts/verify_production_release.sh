#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
PYTHON=${NOWCASTER_BUILD_PYTHON:-$PROJECT_ROOT/.venv/bin/python}
APP_PATH=${1:?usage: verify_production_release.sh APP_PATH}
CONTENTS=$APP_PATH/Contents
HELPER=$CONTENTS/Helpers/nowcaster-engine

test -d "$APP_PATH"
test -x "$HELPER"
test -f "$CONTENTS/Resources/engine-manifest.json"
test -f "$CONTENTS/Resources/nowcaster-sbom.cdx.json"
"$PYTHON" "$PROJECT_ROOT/scripts/engine_manifest.py" --root "$PROJECT_ROOT" \
    --executable "$HELPER" --verify "$CONTENTS/Resources/engine-manifest.json"
"$PYTHON" -c 'import json, pathlib, sys; value=json.loads(pathlib.Path(sys.argv[1]).read_text()); assert value["bomFormat"] == "CycloneDX" and value["components"]' \
    "$CONTENTS/Resources/nowcaster-sbom.cdx.json"
"$HELPER" monitor --help >/dev/null
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
codesign --verify --strict --verbose=2 "$HELPER"
DETAILS=$(codesign -dv --verbose=4 "$APP_PATH" 2>&1)
print "$DETAILS" | grep -q 'Authority=Developer ID Application:'
print "$DETAILS" | grep -q 'Runtime Version='
if codesign -d --entitlements :- "$APP_PATH" 2>/dev/null | grep -q 'get-task-allow'; then
    print -u2 'Development entitlement get-task-allow is forbidden'
    exit 1
fi
HELPER_ENTITLEMENTS=$(codesign -d --entitlements :- "$HELPER" 2>/dev/null)
print "$HELPER_ENTITLEMENTS" | grep -q 'com.apple.security.cs.disable-library-validation'
if print "$HELPER_ENTITLEMENTS" | grep -q 'get-task-allow'; then
    print -u2 'Development entitlement get-task-allow is forbidden on the helper'
    exit 1
fi
xcrun stapler validate "$APP_PATH"
spctl --assess --type execute --verbose=2 "$APP_PATH"
print 'Production release verification passed.'
