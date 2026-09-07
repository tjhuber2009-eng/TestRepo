# Atlas Forge AUTORESEARCH — NVIDIA NIM reconstruction

This branch contains a runnable reconstruction of the Atlas Forge/Karpathy trading
autoresearch loop, with the repeated research-agent calls moved from Claude Code
to NVIDIA NIM.

## What stays frozen

- ETH 6-hour in-sample window: 2017-08-17 through 2022-12-31
- OOS starts 2023-01-01 and remains sealed
- cash = 10,000,000
- commission = 0.002
- margin = 0.25
- minimum trades = 50
- annual-volatility guard = +/-10% of the frozen baseline
- hard maximum historical drawdown = 10.0%
- K = ln(1 + total return) * signed Sharpe
- keep/revert behavior
- only strategy.py is the research artifact

## NVIDIA agent

Endpoint:

    https://integrate.api.nvidia.com/v1

Default model:

    nvidia/nemotron-3-super-120b-a12b

The API key is read only from the NVIDIA_API_KEY environment variable.

The model does not receive filesystem or shell tools. Each iteration sends:

- program.md
- the current baseline
- the last 30 scored attempts
- EvoMind v0.10 development-only research guidance
- the current strategy.py

The model returns one proposal plus the complete replacement strategy source
inside the host's explicit delimiter protocol. The host validates the response,
Python syntax, source safety, risk-control fingerprint, localized-change limits,
and the required AtlasStrategy class before any backtest.

## EvoMind v0.10 research intelligence

EvoMind is integrated as the research-intelligence layer above the frozen Atlas
Forge evaluator. It does not replace the evaluator.

For every adaptive research iteration EvoMind:

1. selects one proposal mode from **evolution**, **synthesis**,
   **skill_transfer**, **immigrant**, or **external_proposal**;
2. retrieves strong concepts learned from prior development-only experiments;
3. can transfer abstract concepts between tracks in the same research lane;
4. tells the proposal model which mechanisms have repeatedly failed;
5. receives the resulting Atlas Forge development verdict and metrics; and
6. updates its persistent proposal-source and concept memory.

Persistent memory is stored inside the active research state directory as
`evomind.db`, so core AUTORESEARCH and Stock+FX keep independent empirical
state while each learns across its own tracks.

The integration is intentionally asymmetric: **Atlas Forge grades EvoMind, never
the reverse.** EvoMind cannot change position sizing, keep/revert a candidate,
open hidden validation, open the 2023+ final OOS, bypass PBO/bootstrap evidence,
or relax any private/prop risk limit.

Atlas Forge uses EvoMind v0.10.0 safe-production behavior as the reference:
adaptive portfolio mode off by default, compute-cost mutation credit off, and
one island. The v0.10 release/source/wheel hashes and MIT license are frozen
under `vendor/evomind/`.

## YouTube Intelligence external hypothesis layer

YouTube Intelligence v3.1.0 is integrated underneath EvoMind as an external
hypothesis source. Its frozen source is branch
`yke-v3.1-windows-build`, commit
`1f7673b00994fb321fda0b7077c5405529441691`.

The current YouTube Intelligence product can discover whole public channels,
ingest/search transcripts, analyze channel performance/content patterns, extract
transcript-derived claims/recommendations/predictions/strategy rules, and fall
back to audio transcription for captionless videos. Visual frame/video
understanding is not part of this integration yet.

The Atlas bridge consumes a JSON or JSONL strategy-idea export. Useful fields
include `published_at`, `video_id`, `channel_title`, `summary`,
`strategy_rules`, `markets`, `timeframes`, `tags`,
`specification_quality`, and `claimed_metrics`.

Authority is deliberately one-way:

1. EvoMind decides whether the current proposal slot is
   `external_proposal`.
2. YouTube Intelligence supplies at most one compatible, previously untested
   hypothesis for that track.
3. Creator performance claims are stored for source auditing but are withheld
   from the proposal prompt and never count as empirical evidence.
4. Atlas Forge translates/tests the hypothesis under the normal frozen risk,
   chronology, cost, PBO/bootstrap, and lookahead gates.
5. Atlas Forge's verdict is written back to the YouTube Intelligence bridge so
   source/idea usefulness can be measured from actual research outcomes.

The chronology guard is strict. A video's `published_at` must be on or before
the active track's adaptive-development cutoff. Newer videos are retained as
quarantined discoveries but cannot enter that legacy track. This prevents a
2026 video from leaking knowledge about the supposedly untouched 2021–2022
hidden-validation or 2023+ final-OOS periods.

Lane state is stored separately:

- core: `continuous_state/youtube_intelligence.db`
- Stock+FX: `stock_fx_state/youtube_intelligence.db`
- feed: `youtube_intelligence_feed.jsonl` in the corresponding state directory

As with EvoMind, **Atlas Forge grades YouTube Intelligence, never the reverse.**

## Setup on Windows PowerShell

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    python prepare_data.py --asset ETH

Set the NVIDIA key without writing it into the repository:

    $env:NVIDIA_API_KEY="your-key"

Smoke-test the NVIDIA connection:

    python nvidia_smoke.py

Freeze the initial in-sample baseline:

    python harness.py --is --set-baseline

Run a three-iteration shakedown:

    python loop.py --iters 3 --model nvidia/nemotron-3-super-120b-a12b

Run continuously until a STOP file appears:

    python loop.py --model nvidia/nemotron-3-super-120b-a12b

## OOS

Do not open OOS during optimization. After the research campaign is finished,
the explicit one-look command is:

    python morning.py --unlock ONE_LOOK

Repeatedly inspecting OOS and then resuming optimization would turn the holdout
into another training set.

## Reconstruction limitation

The original Atlas Forge seed strategy and exact data-loader implementation were
not visible in the supplied screenshots. The included seed is a simple breakout
strategy so the loop is runnable; it is not claimed to be Atlas Forge's original.
