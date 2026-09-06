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
- the current strategy.py

The model must return JSON with:

    {"proposal":"one-line description","strategy_py":"complete Python source"}

The host validates JSON, Python syntax, and the required MoonStrategy class
before writing strategy.py.

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
