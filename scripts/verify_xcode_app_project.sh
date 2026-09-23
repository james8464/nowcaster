#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
XCODE_PROJECT="$PROJECT_ROOT/macos/Nowcaster/Nowcaster.xcodeproj"
INFO_PLIST="$PROJECT_ROOT/macos/Nowcaster/Resources/Info.plist"
APP_ENTRYPOINT="$PROJECT_ROOT/macos/Nowcaster/Sources/NowcasterApp/NowcasterApp.swift"

[[ -f "$XCODE_PROJECT/project.pbxproj" ]] || {
  print -u2 "Missing the native Nowcaster Xcode project."
  exit 1
}

xcodebuild -list -project "$XCODE_PROJECT" | grep -Fq "Nowcaster"
grep -Fq "com.apple.product-type.application" "$XCODE_PROJECT/project.pbxproj"
grep -Fq "LSUIElement" "$INFO_PLIST" && {
  print -u2 "The main app must remain a foreground application."
  exit 1
}
grep -Fq "CFBundleIconFile" "$INFO_PLIST"
grep -Fq "AppIcon" "$INFO_PLIST"
grep -Fq "ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;" "$XCODE_PROJECT/project.pbxproj"
grep -Fq "setActivationPolicy(.regular)" "$APP_ENTRYPOINT"
