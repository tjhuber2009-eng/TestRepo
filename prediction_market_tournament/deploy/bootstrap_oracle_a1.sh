#!/usr/bin/env bash
set -euo pipefail

# One-command bootstrap for a fresh Oracle Ubuntu 24.04 Ampere A1 VM.
# Safe to rerun. It never starts PMT-FROZEN-V1.

REPO_URL="https://github.com/tjhuber2009-eng/TestRepo.git"
SSH_REPO_URL="git@github.com:tjhuber2009-eng/TestRepo.git"
REPO="$HOME/TestRepo"
ROOT="$REPO/prediction_market_tournament"
BRANCH="prediction-market-tournament"
KEY="$HOME/.ssh/pmt_forward_deploy_ed25519"

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git openssh-client ca-certificates

if [[ ! -d "$REPO/.git" ]]; then
  git clone --single-branch --branch "$BRANCH" "$REPO_URL" "$REPO"
else
  git -C "$REPO" fetch origin "$BRANCH"
  git -C "$REPO" checkout "$BRANCH"
  git -C "$REPO" reset --hard "origin/$BRANCH"
fi

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

if [[ ! -f "$KEY" ]]; then
  ssh-keygen -q -t ed25519 -N "" -f "$KEY" -C "pmt-forward-oracle-a1"
fi
chmod 600 "$KEY"
chmod 644 "$KEY.pub"

# Pin GitHub's currently published ED25519 host key rather than trusting
# unauthenticated ssh-keyscan output.
GITHUB_ED25519='github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl'
if ! grep -Fqx "$GITHUB_ED25519" "$HOME/.ssh/known_hosts" 2>/dev/null; then
  printf '%s\n' "$GITHUB_ED25519" >> "$HOME/.ssh/known_hosts"
fi
chmod 600 "$HOME/.ssh/known_hosts"

CONFIG="$HOME/.ssh/config"
touch "$CONFIG"
chmod 600 "$CONFIG"
if ! grep -q "IdentityFile $KEY" "$CONFIG"; then
  cat >> "$CONFIG" <<EOF
Host github.com
  HostName github.com
  User git
  IdentityFile $KEY
  IdentitiesOnly yes
EOF
fi

git -C "$REPO" remote set-url origin "$SSH_REPO_URL"

# A write-enabled GitHub deploy key is required only for remote audit
# persistence. Public cloning already succeeded without credentials.
if ! git -C "$REPO" push --dry-run origin HEAD:"$BRANCH" >/tmp/pmt-push.out 2>/tmp/pmt-push.err; then
  cat <<EOF

PMT Oracle host is prepared, but GitHub audit-write access is not enabled yet.

Add the following PUBLIC key to:
  TestRepo -> Settings -> Deploy keys -> Add deploy key

Title:
  PMT Oracle A1 forward collector

Enable:
  Allow write access

PUBLIC KEY:
$(cat "$KEY.pub")

After adding it, rerun this same bootstrap command.
PMT-FROZEN-V1 has NOT been started.
EOF
  exit 20
fi

if [[ -f "$ROOT/data/forward_start_v1.json" ]]; then
  echo "Persisted V1 start marker detected; entering recovery path."
  exec bash "$ROOT/deploy/recover_oracle_a1.sh"
fi

exec bash "$ROOT/deploy/setup_oracle_a1.sh"
