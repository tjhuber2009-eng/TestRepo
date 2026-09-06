# Reconstruction notes

High-confidence items recovered from the supplied screenshots include the file
layout, four-phase loop, strategy keep/revert flow, baseline and results files,
K score, signed Sharpe definition, ETH 6-hour window, frozen cash/commission/
margin settings, 50-trade floor, and +/-10% annual-volatility guard.

The exact original seed strategy, lower data-loader implementation, and
morning.py source were not visible and therefore remain reconstructed rather
than transcribed.

## NVIDIA migration

This branch changes the research-agent transport only. loop.py no longer invokes
Claude Code. It calls NVIDIA NIM through the OpenAI-compatible endpoint, reads
the key from NVIDIA_API_KEY, and requires strict JSON containing one proposal
and a complete replacement strategy.py.

The host validates Python syntax and the AtlasStrategy class before any strategy
replacement is accepted for backtesting.

The frozen harness, scoring equation, dates, commission, margin, minimum-trade
guard, volatility guard, keep/revert logic, and sealed OOS design remain
separate from the model call.
