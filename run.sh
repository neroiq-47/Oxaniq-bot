#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -d venv ]]; then
  python3 -m venv venv
  # shellcheck disable=SC1091
  source venv/bin/activate
  pip install -U pip
  pip install -r requirements.txt
else
  # shellcheck disable=SC1091
  source venv/bin/activate
fi
export PYTHONPATH=.
exec python3 -m src.main --paper "$@"
