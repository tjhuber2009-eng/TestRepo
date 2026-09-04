# Market Predictor — GitHub Stage 2C

This directory is the GitHub-native continuation of the market-prediction research project.

## Current state

- Aggregate Welch–Goyal Stage 1: closed as a valid negative benchmark.
- Cross-sectional OSAP route: blocked on a defensible free PERMNO bridge.
- Sharadar route: feasible but deferred because the user requested a free path.
- Current free path: QuantRocket learning bundle `usstock-learn-1d`.
- QuantRocket documents this bundle as daily US stock/ETF data for 2007–2011 for free-tier users.
- This branch is isolated from HR-MECH and from the other trading projects on TestRepo.

The local Claude Code checkpoint reported by the user was:

- local branch: `stage2c-free-quantrocket`
- local checkpoint: `1af3e89`
- blocker: Docker/WSL absent on the Windows machine

This GitHub branch removes that local infrastructure blocker by using a GitHub-hosted Ubuntu runner with Docker.

## Research rule

**No model may run until the data audit passes and a preregistration is committed.**

The first GitHub job is therefore data-only. It checks:

1. QuantRocket free learning-bundle availability.
2. security count and metadata;
3. survivorship / delisted-security presence;
4. 2008–2009 coverage;
5. point-in-time common-stock universe behavior;
6. Zipline adjustment timing;
7. terminal observations for delisted stocks;
8. underlying adjustment-database schema;
9. whether the free bundle can support the proposed next-session label without a terminal-return bias.

If a hard gate fails, Stage 2C stops.

## Required secret

The workflow requires one repository secret:

`QUANTROCKET_LICENSE_KEY`

Create a free QuantRocket account, obtain the license key from the QuantRocket account page, and add it in GitHub:

**Settings → Secrets and variables → Actions → New repository secret**

Do not commit the key and do not paste it into chat.

## Workflow

`.github/workflows/market-predictor-quantrocket-stage2c.yml`

The workflow only runs on branch `market-predictor-stage2c`.

Artifacts:

- `stage2c_quantrocket_audit.json`
- `stage2c_quantrocket_audit.md`

The free 2007–2011 sample is historical pseudo-OOS development evidence only. Even a future positive model result would require longer independent data before any deployment claim.
