# Zero-fee AI model tournament — matched repeated-trial ranking

> **Provisional recovery:** Nemotron Lightning timed out at trial 19/20 in the original run and is being retried separately.

Protocol: **nested_chronological_v3**
Frozen continuous-state SHA: `00d01b8c28bfc88763c94d6641fca45d52f289f0`
Matched cases: **10**
Available models: **4**

| Rank | Model | Keep rate | Keep 95% CI | Case wins | Guard | Paired median ΔK | ΔK 95% CI | Unique ideas |
|---:|---|---:|---|---:|---:|---:|---|---:|
| 1 | poolside/laguna-xs-2.1 | 30.0% | 14.5%–51.9% | 4.5 | 10/20 | 0.012880 | -0.017021–0.064202 | 12 |
| 2 | nvidia/nemotron-3-ultra-550b-a55b | 25.0% | 11.2%–46.9% | 2.0 | 6/20 | 0.037450 | -0.004003–0.083095 | 20 |
| 3 | nvidia/nemotron-3-super-120b-a12b | 20.0% | 8.1%–41.6% | 1.5 | 12/20 | 0.001694 | -0.005921–0.033968 | 20 |
| 4 | deepseek-ai/deepseek-v4-pro-0813 | 5.0% | 0.9%–23.6% | 2.0 | 11/20 | 0.000000 | -0.017548–0.000316 | 20 |

Unavailable or unconfigured:
- `gemini-2.5-pro` (gemini): GEMINI_API_KEY missing
- `z-ai/glm-5.2` (nvidia): APIStatusError: Error code: 410 - {'type': 'about:blank', 'title': 'Gone', 'status': 410, 'detail': "The model 'z-ai/glm-5.2' has reached its end of life on 2026-08-21T09:00:00Z and is no longer available."}
- `openai/gpt-oss-120b` (groq): GROQ_API_KEY missing

Ranking uses repeated matched trials rather than a single lucky proposal.
No model sees another model's output. Hidden validation and 2023+ OOS remain sealed.
