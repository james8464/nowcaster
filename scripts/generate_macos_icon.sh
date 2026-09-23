#!/bin/zsh
set -euo pipefail

SOURCE_ICON=$1
OUTPUT_ICON=$2
[[ -f "$SOURCE_ICON" ]] || { print -u2 "Missing source icon: $SOURCE_ICON"; exit 1; }

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
ICONSET="$WORK_DIR/AppIcon.iconset"
mkdir -p "$ICONSET" "${OUTPUT_ICON:h}"

for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$SOURCE_ICON" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    sips -z "$((size * 2))" "$((size * 2))" "$SOURCE_ICON" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$OUTPUT_ICON"
