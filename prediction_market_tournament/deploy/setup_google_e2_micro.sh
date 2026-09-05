#!/usr/bin/env bash
set -euo pipefail

# Run this on an Ubuntu 24.04 Google Compute Engine e2-micro VM after the
# private TestRepo repository has been cloned to ~/TestRepo and checked out
# on prediction-market-tournament. This script DOES NOT start PMT-FROZEN-V1.

ROOT="$HOME/TestRepo/prediction_market_tournament"
if [[ ! -f "$ROOT/config/frozen_v1.json" ]]; then
  echo "Expected repository at $ROOT" >&2
  exit 2
fi
if [[ -e "$ROOT/data/forward_start_v1.json" ]]; then
  echo "Refusing setup changes after PMT-FROZEN-V1 has started." >&2
  exit 3
fi

sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv git

python3.12 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install pip==26.2.1
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT"
"$ROOT/.venv/bin/python" -m pip install pytest==9.1.1

cd "$ROOT"
"$ROOT/.venv/bin/python" -m pytest -q
"$ROOT/.venv/bin/python" scripts/preflight_forward.py

git -C "$HOME/TestRepo" config user.name "PMT Forward Bot"
git -C "$HOME/TestRepo" config user.email "pmt-forward-bot@users.noreply.github.com"
if ! git -C "$HOME/TestRepo" push --dry-run origin HEAD:prediction-market-tournament; then
  echo "GitHub write access is required for hourly audit-ledger persistence." >&2
  exit 4
fi

mkdir -p "$HOME/.config/systemd/user"
cp "$ROOT/deploy/pmt-forward.service" "$HOME/.config/systemd/user/pmt-forward.service"
systemctl --user daemon-reload

# Allow the user service to continue after the SSH session closes.
sudo loginctl enable-linger "$USER"

cat <<'EOF'
PMT host setup and preflight completed.
V1 HAS NOT BEEN STARTED.

Deliberate launch remains two separate actions:
  1. .venv/bin/python scripts/start_forward.py
  2. systemctl --user enable --now pmt-forward.service

Do not run step 1 until the repository review is final; after the marker is
created, code/spec/runtime changes intentionally invalidate PMT-FROZEN-V1.
EOF
