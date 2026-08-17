#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  if ! command -v python3.11 >/dev/null 2>&1; then
    echo "Khong tim thay python3.11. Hay cai Python 3.11 truoc khi chay app." >&2
    exit 1
  fi
  python3.11 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python app.pyw
