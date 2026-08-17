#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"

touch TextToMp3Dock.app
qlmanage -r cache >/dev/null 2>&1 || true
killall Dock >/dev/null 2>&1 || true
killall Finder >/dev/null 2>&1 || true

echo "Da refresh cache icon cho TextToMp3Dock.app"
