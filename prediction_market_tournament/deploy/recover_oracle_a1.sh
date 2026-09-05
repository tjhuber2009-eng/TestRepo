#!/usr/bin/env bash
set -euo pipefail

# Rebuild helper for a replacement Oracle A1 VM.
# This NEVER manufactures or backdates a V1 clock.
#
# There are two legitimate cases:
#   1) V1 was never started: clone current frozen branch and run setup_oracle_a1.sh.
#   2) V1 was already started: clone the branch including the persisted data/start
#      marker, install the exact environment, and let require_forward_started()
#      verify hashes/runtime before the service can run.
#
# IMPORTANT: a VM outage remains an outage. Missed checkpoints are never replayed.

ROOT="$HOME/TestRepo/prediction_market_tournament"
REPO="$HOME/TestRepo"
BRANCH="prediction-market-tournament"

if [[ ! -d "$REPO/.git" ]]; then
  echo "Clone TestRepo to $REPO first." >&2
  exit 2
fi

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3.12 \
  python3.12-venv \
  git \
  ca-certificates \
  chrony
sudo systemctl enable --now chrony

if ! swapon --show=NAME --noheadings | grep -q .; then
  if [[ ! -f /swapfile ]]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
  fi
  sudo swapon /swapfile
  if ! grep -q '^/swapfile ' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  fi
fi

git -C "$REPO" fetch origin "$BRANCH"
git -C "$REPO" checkout "$BRANCH"
git -C "$REPO" reset --hard "origin/$BRANCH"

python3.12 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install pip==26.2.1
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT" pytest==9.1.1

cd "$ROOT"
"$ROOT/.venv/bin/python" -m pytest -q

git -C "$REPO" config user.name "PMT Forward Bot"
git -C "$REPO" config user.email "pmt-forward-bot@users.noreply.github.com"

mkdir -p "$HOME/.config/systemd/user"
cp "$ROOT/deploy/pmt-forward.service" \
  "$HOME/.config/systemd/user/pmt-forward.service"
systemctl --user daemon-reload
sudo loginctl enable-linger "$USER"

if [[ -f "$ROOT/data/forward_start_v1.json" ]]; then
  # This is the only valid recovery path after V1 started. It does not rerun
  # pre-start preflight because that tool intentionally rejects an existing
  # marker. The service itself verifies spec/code/runtime against the marker.
  "$ROOT/.venv/bin/python" - <<'PY'
from pathlib import Path
from tournament.freeze import require_forward_started

root = Path.cwd()
marker = require_forward_started(root)
print("Frozen recovery verification passed:")
for key in (
    "started_at",
    "spec_sha256",
    "implementation_sha256",
    "runtime_sha256",
):
    print(f"  {key}: {marker[key]}")
PY
  systemctl --user enable --now pmt-forward.service
  echo "Recovered already-started V1. Outage/missed checkpoints remain missed."
else
  "$ROOT/.venv/bin/python" scripts/preflight_forward.py
  echo "Pre-start replacement host is ready. V1 remains NOT STARTED."
fi
