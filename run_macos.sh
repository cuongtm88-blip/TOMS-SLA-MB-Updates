#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

created_venv=0
if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
  created_venv=1
fi

if [[ "$created_venv" == "1" ]]; then
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m playwright install chromium
fi

exec .venv/bin/python app.py
