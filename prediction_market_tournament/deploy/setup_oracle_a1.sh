#!/usr/bin/env bash
set -euo pipefail

# PMT-FROZEN-V1 Oracle OCI Ampere A1 setup.
#
# Expected host:
#   - Ubuntu 24.04 ARM64
#   - VM.Standard.A1.Flex
#   - 1 OCPU / 1 GB RAM (right-sized within Always Free allowance)
#   - public subnet with outbound internet access
#   - repository already cloned to ~/TestRepo on prediction-market-tournament
#
# This script runs tests + live preflight and installs the persistent service.
# It deliberately DOES NOT create the forward-start marker or start V1.

ROOT="$HOME/TestRepo/prediction_market_tournament"
REPO="$HOME/TestRepo"
BRANCH="prediction-market-tournament"

if [[ ! -f "$ROOT/config/frozen_v1.json" ]]; then
  echo "Expected repository at $ROOT" >&2
  exit 2
fi

if [[ -e "$ROOT/data/forward_start_v1.json" ]]; then
  echo "Refusing deployment changes after PMT-FROZEN-V1 has started." >&2
  exit 3
fi

ARCH="$(uname -m)"
case "$ARCH" in
  aarch64|arm64) ;;
  *)
    echo "Expected Oracle Ampere ARM64 host; got architecture: $ARCH" >&2
    exit 4
    ;;
esac

if ! grep -qi '^ID=ubuntu' /etc/os-release; then
  echo "Expected Ubuntu host." >&2
  exit 5
fi

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3.12 \
  python3.12-venv \
  git \
  ca-certificates \
  chrony

sudo systemctl enable --now chrony

# 1 GB RAM is sufficient for the steady-state collector. Add swap only as
# installation/recovery headroom; this does not manufacture CPU/network load.
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

if ! git -C "$REPO" push --dry-run origin HEAD:"$BRANCH"; then
  echo "GitHub write access is required for hourly audit-ledger persistence." >&2
  exit 6
fi

"$ROOT/.venv/bin/python" scripts/preflight_forward.py

mkdir -p "$HOME/.config/systemd/user"
cp "$ROOT/deploy/pmt-forward.service" \
  "$HOME/.config/systemd/user/pmt-forward.service"
systemctl --user daemon-reload

# Keep the user service alive after SSH logout.
sudo loginctl enable-linger "$USER"

cat <<'EOF'
Oracle PMT host setup completed successfully.

PMT-FROZEN-V1 HAS NOT BEEN STARTED.

The host passed:
  - exact-remote clean-tree verification
  - unit tests
  - GitHub persistence dry-run
  - live clock/RTDS/weather/BTC execution preflight

Deliberate launch remains:
  .venv/bin/python scripts/start_forward.py
  systemctl --user enable --now pmt-forward.service

Do not run the first command until the final host review is complete.
EOF
