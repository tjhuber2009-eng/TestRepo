# V4 Build Manifest

Protocol: `alpha_generation_v4`

Base: `atlas-forge-autoresearch@412452f846a5858e23b8ac0869c8ae754dd1f6a2`

This branch implements the complete architecture requested after the v3 model
allocator research:

| Requested improvement | Implementation |
|---|---|
| CAGR under fixed risk budget | `alpha_objective.py` |
| True multi-asset engine | `multi_asset_engine.py` |
| Rich data/features | `feature_store.py`, `context_adapters.py` |
| Earnings/event-driven strategies | `strategy_examples.py::pead_event_weights` |
| Controlled parameter optimization | `parameter_optimizer.py` |
| Regime-dependent strategies | `regime_engine.py` |
| Meta-filter | `meta_filter.py` |
| Reusable mutation knowledge | `motif_library.py` |
| Thompson allocation across research areas | `research_allocator.py` |
| Strategy portfolio optimization | `portfolio_optimizer.py` |
| Separate intraday research | `intraday_protocol.py` |
| External strategy harvesting | `external_harvester.py`, `strategy_intake.py` |
| Real development-only bootstrap | `live_bootstrap.py` |
| End-to-end orchestration | `campaign.py` |
| Regression/integrity coverage | `test_v4_alpha_generation.py` |

V4 does not reclassify v3 results and does not authorize hidden validation or
2023+ final OOS access.
