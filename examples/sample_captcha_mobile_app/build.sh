#!/usr/bin/env bash
# Build the SampleCaptchaApp.app for the iOS Simulator.
#
# What this does, in order:
#   1. rsync ../sample_captcha_fixture/ → Resources/FixtureHTML/
#      (so the fixture HTML lives at a stable, project-local path)
#   2. xcodegen generate (creates SampleCaptchaApp.xcodeproj)
#   3. xcodebuild -sdk iphonesimulator (produces SampleCaptchaApp.app)
#
# Prereqs:
#   - Xcode + iOS Simulator runtime installed
#   - xcodegen (brew install xcodegen)
#
# Used by:
#   - Local dev: ./build.sh && ./install.sh
#   - .github/workflows/visual-challenge-maestro-ios.yml (Tier 1 CI)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

FIXTURE_SRC="../sample_captcha_fixture"
FIXTURE_DST="Resources/FixtureHTML"

if [[ ! -d "$FIXTURE_SRC" ]]; then
    echo "ERROR: fixture source $FIXTURE_SRC not found (run from examples/sample_captcha_mobile_app)" >&2
    exit 1
fi

if ! command -v xcodegen >/dev/null 2>&1; then
    echo "ERROR: xcodegen not on PATH. Install with: brew install xcodegen" >&2
    exit 2
fi

echo "==> Syncing fixture HTML → $FIXTURE_DST"
mkdir -p "$FIXTURE_DST"
rsync -a --delete --exclude='.DS_Store' "$FIXTURE_SRC/" "$FIXTURE_DST/"

echo "==> Generating SampleCaptchaApp.xcodeproj"
xcodegen generate

DERIVED_DATA="${DERIVED_DATA:-$SCRIPT_DIR/DerivedData}"
mkdir -p "$DERIVED_DATA"

echo "==> Building for iphonesimulator (arm64)"
xcodebuild \
    -project SampleCaptchaApp.xcodeproj \
    -scheme SampleCaptchaApp \
    -configuration Debug \
    -sdk iphonesimulator \
    -destination 'generic/platform=iOS Simulator' \
    -derivedDataPath "$DERIVED_DATA" \
    CODE_SIGNING_REQUIRED=NO \
    CODE_SIGNING_ALLOWED=NO \
    build | tail -25

APP_PATH=$(find "$DERIVED_DATA/Build/Products/Debug-iphonesimulator" -maxdepth 2 -name "SampleCaptchaApp.app" -type d | head -1)
if [[ -z "$APP_PATH" ]]; then
    echo "ERROR: build succeeded but SampleCaptchaApp.app not found under $DERIVED_DATA" >&2
    exit 3
fi

echo ""
echo "==> Built: $APP_PATH"
echo "Install with:  xcrun simctl install booted '$APP_PATH'"
echo "Launch with:   xcrun simctl launch booted dev.mkqa.SampleCaptchaApp"
