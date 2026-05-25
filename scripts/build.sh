#!/usr/bin/env bash
set -euo pipefail

echo "=== Building OpenPrint standalone binary ==="

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "Python 3 is required"
    exit 1
fi

# Install build dependencies
pip install . pyinstaller

# Detect platform
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
    x86_64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
esac
TARGET="${OS}-${ARCH}"

echo "Building for: ${TARGET}"

# Build
pyinstaller --onefile --name "opp-${TARGET}" --strip --noconfirm \
    --add-data "src/openprint/static:openprint/static" \
    --hidden-import openprint.cli.main \
    --hidden-import openprint.server \
    --hidden-import openprint.bridge \
    --hidden-import openprint.testkit \
    --hidden-import openprint.backends.cups \
    --hidden-import openprint.backends.ipp \
    --hidden-import openprint.backends.dummy \
    --collect-submodules openprint \
    src/openprint/cli/__main__.py

echo
echo "Binary built: dist/opp-${TARGET}"
echo "Size: $(du -h "dist/opp-${TARGET}" | cut -f1)"
echo
echo "Test it:  ./dist/opp-${TARGET} --help"
