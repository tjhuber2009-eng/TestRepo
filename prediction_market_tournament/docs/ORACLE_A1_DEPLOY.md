# Oracle Always Free A1 deployment — PMT-FROZEN-V1

This is the preferred zero-cloud-cost persistent-host path for
PMT-FROZEN-V1.

## Important Oracle limits

Oracle Always Free compute resources must be created in the tenancy's **home
region**. For Ampere A1, the Always Free allowance is sufficient for this
collector. Oracle may reclaim Always Free instances it classifies as idle over
a seven-day period, so the project also includes deterministic replacement-host
recovery. A reclaim/outage never authorizes retroactive signal reconstruction.

## Create the VM

In Oracle Cloud Console:

1. Open **Compute -> Instances -> Create instance**.
2. Name: `pmt-forward-v1`.
3. Image: **Canonical Ubuntu 24.04**, ARM64 / Always Free eligible.
4. Shape:
   - processor series: **Ampere**
   - shape: **VM.Standard.A1.Flex**
   - OCPUs: **1**
   - memory: **1 GB**
5. Boot volume:
   - use the normal Always Free-eligible boot volume;
   - 50 GB is sufficient for PMT.
6. Networking:
   - create/use a VCN with a **public subnet**;
   - assign a **public IPv4 address**;
   - internet gateway enabled;
   - no application inbound ports are required.
7. Security:
   - inbound SSH/TCP 22 only, preferably restricted to your own source IP;
   - PMT itself requires only outbound HTTPS/WSS/SSH access.
8. SSH key:
   - use Oracle's generated key pair or your own key;
   - retain the private key securely.
9. Create the instance and wait until its lifecycle state is **Running**.

## Connect

Oracle Ubuntu images normally use the `ubuntu` account.

Example:

```bash
ssh -i /path/to/oracle-private-key ubuntu@PUBLIC_IP
```

## Run the PMT bootstrap

On the Oracle VM:

```bash
curl -fsSLo /tmp/pmt-oracle-bootstrap.sh \
  https://raw.githubusercontent.com/tjhuber2009-eng/TestRepo/prediction-market-tournament/prediction_market_tournament/deploy/bootstrap_oracle_a1.sh

bash /tmp/pmt-oracle-bootstrap.sh
```

The bootstrap:

- clones the public `prediction-market-tournament` branch;
- generates a dedicated ED25519 key for **audit-data pushes only**;
- pins GitHub's published ED25519 host key;
- stops and prints the deploy public key if GitHub write access is not ready;
- never starts PMT-FROZEN-V1 by itself.

## One-time GitHub deploy-key step

If the bootstrap prints a public key:

1. Open **TestRepo -> Settings -> Deploy keys**.
2. Choose **Add deploy key**.
3. Title: `PMT Oracle A1 forward collector`.
4. Paste the exact printed **public** key.
5. Enable **Allow write access**.
6. Save.
7. Rerun:

```bash
bash /tmp/pmt-oracle-bootstrap.sh
```

Do not copy or upload the VM's private deploy-key file.

## What the second bootstrap run does

After GitHub write access succeeds it:

- installs Python 3.12 and a 2 GB swap file for install/recovery headroom;
- installs the pinned PMT dependency/test environment;
- runs the complete pytest suite;
- verifies a GitHub data-push dry run;
- verifies host clock synchronization;
- verifies fresh Chainlink raw + 60-second TWAP RTDS data;
- verifies all frozen weather ensemble inputs;
- verifies an executable current BTC 5-minute market and books;
- installs the persistent systemd user service;
- keeps the service disabled;
- **does not create the forward marker**.

A successful run ends with:

`PMT-FROZEN-V1 HAS NOT BEEN STARTED.`

## Deliberate launch

Only after setup/preflight has passed:

```bash
cd ~/TestRepo/prediction_market_tournament
.venv/bin/python scripts/start_forward.py
systemctl --user enable --now pmt-forward.service
```

`start_forward.py` reruns the mandatory live preflight. There is no supported
backdate or custom-start-time option.

## Confirm service

```bash
systemctl --user status pmt-forward.service --no-pager
journalctl --user -u pmt-forward.service -n 100 --no-pager
```

The marker should exist only after launch:

```bash
cat ~/TestRepo/prediction_market_tournament/data/forward_start_v1.json
```

## Replacement VM after an Oracle reclaim

Create another Ubuntu 24.04 A1 VM with the same **ARM64** architecture and run
the same bootstrap command.

The bootstrap detects the already-persisted V1 marker and automatically enters
`recover_oracle_a1.sh` after GitHub write access is configured.

On a replacement VM you will normally generate a new deploy key. Remove the old
VM's obsolete deploy key from TestRepo after the replacement is working.

Recovery:

- fetches the exact remote branch/data;
- reinstalls Python 3.12 + exact transport dependency;
- verifies the immutable spec/code/runtime semantics;
- starts the service only if the marker still matches;
- never changes `started_at`;
- never reconstructs observations missed during the outage.

If runtime verification fails, recovery fails closed instead of silently
creating a different V1.
