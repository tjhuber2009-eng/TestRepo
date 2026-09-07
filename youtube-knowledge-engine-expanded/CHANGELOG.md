# Changelog

## 3.1.0 — 2026-09-06

- Promoted native desktop SQLite from a recovery mirror to the authoritative durable knowledge-base copy; IndexedDB remains a compatibility/search cache for the existing worker stack.
- Added generation-based startup reconciliation from SQLite plus staged, counted browser-cache reloads and explicit manual commit/reload controls.
- Increased native record safety bounds so legitimate long transcripts up to the existing import limits can be stored without the old 2 MB mirror ceiling.
- Added a separate bundled Tauri **AI provider settings** window; the main research webview remains outside privileged Tauri IPC.
- Added OS-native credential storage through `keyring` (macOS Keychain Services, Windows Credential Manager, Linux Secret Service) with no plaintext secret fallback.
- Provider URL/model/auth configuration is stored separately from secrets; saving settings securely restarts the private loopback backend.
- Added `YTDLP_BIN`, `FFMPEG_BIN`, and `YTDLP_NODE_BIN` tool overrides and explicit yt-dlp use of the bundled Node runtime.
- Added optional installer-time media-tool preparation and third-party license notices; binaries are not silently committed or downloaded by the source release.
- Kept the loopback-only random-port/session-token backend boundary, tray monitoring, web/self-hosted compatibility, and existing Visual Intelligence behavior.
- Restricted the two native provider-settings commands with a Tauri `AppManifest` and settings-window-only capability instead of relying solely on the main loopback webview being a remote origin.
- Hardened provider URL validation to parsed HTTPS/exact-loopback hosts and reject embedded URL credentials before writing non-secret configuration.
- Integrated desktop SQLite commit/restore with the shared knowledge-base mutation lock, added retry after concurrent mutations, restored unexpectedly empty browser caches from native data, and purged abandoned SQLite staging rows at each commit.
- Applied a restrictive Unix process umask in desktop mode so SQLite/WAL, monitor data, and AI checkpoint files default to private permissions.

## 3.0.0 — 2026-09-06

- Added the first desktop-first Tauri shell with a bundled Node sidecar, dynamic loopback port, system tray lifecycle, and OS application-data directories.
- Added a per-launch desktop session boundary: one-time bootstrap token → HttpOnly SameSite cookie → deny all unauthenticated loopback requests.
- Added a native SQLite recovery mirror for `kb_sources`, `kb_videos`, and `kb_meta`, with bounded mirror batches and atomic snapshot commits.
- Added paged native recovery, IndexedDB staging, snapshot generation/count validation, and atomic final restore so large recoveries do not require one giant JSON response or destroy the active KB on partial failure.
- Added desktop recovery controls and automatic mirroring after knowledge-base mutations while keeping IndexedDB canonical for this migration milestone.
- Added cross-platform desktop compile checks for Windows, macOS, and Linux, plus pinned direct Rust/Tauri dependencies and target-specific bundled Node sidecar preparation.
- Extended release/version invariants to cover Tauri configuration, desktop security capabilities, native recovery bounds, and knowledge-backup producer metadata.

## 2.6.0 — 2026-09-06

- Added opt-in **Visual Intelligence** for public YouTube videos using a server-side multimodal provider adapter; the first adapter targets Gemini's current Interactions API and sends the public YouTube URL directly instead of downloading video bytes through the application server.
- Added structured, timestamped visual evidence for charts, settings panels, displayed metrics, tables, slides, code, software UI, diagrams, products, demonstrations, and on-screen text.
- Visual events preserve bounded OCR text, visible label/value pairs, confidence, importance, nearby spoken context, and a conservative visual/spoken relationship (`visual_only`, `supports_spoken`, `potential_conflict`, or `uncertain`).
- Potential narration/visual mismatches are explicitly labeled research triage and never treated as automatic factual adjudication; displayed backtests/results remain creator-presented evidence rather than independently verified performance.
- Added a local visual-evidence cache in browser IndexedDB, source cleanup, JSON/Markdown analysis export integration, timestamp links, and high-value candidate prioritization based on titles, outliers, and transcript-derived rule/performance evidence.
- Visual analysis is never automatic: each run requires explicit confirmation and inspects at most three previously unanalyzed high-value videos, allowing quota/cost to remain bounded.
- Added a separately rate-limited and cached `/api/visual/analyze` endpoint protected by the existing paid-AI access gate; provider keys never reach the browser and provider responses are schema-validated, size-bounded, and sanitized before storage.
- Added `VISUAL_*` configuration for model/provider, agentic/static processing, timeout, output-token, response-size, rate-limit, and cache controls, plus health capability reporting that never exposes credentials.
- Added behavioral tests for direct YouTube URL requests, agentic structured output, output sanitization, cost/auth gating, caching, provider-secret non-disclosure, candidate triage, and visual-corpus summaries.

## 2.5.0 — 2026-09-06

- Added bounded **evidence-strength** scoring for repeated idea/strategy families using distinct supporting videos, recurrence, passage specificity, and structured rule completeness. Scores explicitly measure corpus support, not truth or profitability.
- Added a **creator-evolution timeline** covering up to 16 recent calendar quarters with upload counts, top title topics, and transcript-derived strategy-rule/performance-claim counts.
- Added prioritized longitudinal **intelligence alerts** for potentially changed rules, newly surfaced idea families, new age-normalized outliers, and material topic shifts after a newer source sync.
- Cross-channel consensus/conflict rows now include combined evidence-strength and supporting-video counts for faster source triage.
- Added **audience → content/research opportunities** that combine repeated comment demand, explicit requests/questions/pain points, channel topic coverage, and underused-winner overlap into a bounded priority score.
- Analysis JSON/Markdown exports now preserve creator evolution, priority alerts, evidence-strength context, and audience opportunity rows.
- Bumped derived analysis schema/cache version so existing transcript and knowledge-base data remain intact while reports recompute with the new intelligence fields.
- Added regression coverage for evidence-strength calibration, historical evolution, alert prioritization, audience-opportunity ranking, and v2.5 UI wiring.

## 2.4.0 — 2026-09-06

- Added a local-first **Intelligence layer** that clusters transcript-derived strategies, claims, recommendations, and predictions into repeated idea families with timestamped source evidence.
- Two-channel comparison now surfaces repeated ideas across independent channels, ideas unique to either channel, and conservative **potential disagreement** signals for similar passages with opposite lexical stance markers.
- Added longitudinal **Since the previous sync** analysis: compact prior analysis snapshots are retained across newer channel/monitor syncs and compared for new idea families, potentially changed rules, topic shifts, new outliers, and video-count changes.
- Fixed stale in-memory analysis reuse after a knowledge-base mutation by validating the source sync timestamp and clearing report memory on knowledge-base change events.
- Added optional **Audience intelligence** using bounded public top-level YouTube comment samples through yt-dlp. The server strips commenter names and exposes only comment text, public likes/timing, video linkage, and uploader-reply status.
- Audience analysis runs only after an explicit click, samples at most five candidate videos per run, requests at most 40 top-level comments per video from the browser, and stores only the derived browser-local report.
- Added local audience heuristics for recurring topics, questions, future-content requests, pain points, and sample-level sentiment/question/request rates with explicit sampling-bias caveats.
- Added a bounded `/api/comments` endpoint with same-origin JSON protections, rate limits, in-memory TTL caching, validated video IDs, bounded yt-dlp output, and a test-injectable fetcher.
- Added regression coverage for cross-channel consensus/conflict triage, longitudinal analysis history, privacy-minimized comment normalization, audience insight extraction, and the public comment API.

## 2.3.2 — 2026-09-06

- Added a behavioral release-security audit (`npm run release:audit`) and wired it into `npm run verify`; source packaging now fails closed on common secret files, cookie exports, private keys, persisted runtime transcript/checkpoint directories, databases, captured media, nested archives, symlinks, and unexpectedly large files/trees.
- Replaced the brittle hard-coded yt-dlp release assertion with a reproducibility invariant that requires an explicit stable pin while allowing future extractor upgrades without rewriting the test; the Docker pin remains `2026.08.19`, the current stable release verified on 2026-09-06.
- Hardened paid-transcription checkpoint recovery: malformed timestampless chunks retain their paid transcript text through bounded fallback segments instead of being silently dropped.
- Added per-chunk and completed-result hashes for newly written checkpoints, configuration fingerprints, bounded checkpoint file sizes, strict result-field sanitization, private checkpoint-directory/file permissions, and collision-resistant atomic temporary writes.
- Corrupt or inconsistent completed checkpoint results are no longer returned as trusted output; valid paid chunks remain reusable so recovery avoids unnecessary provider charges.
- Added regression tests for release-package secret/data leakage and checkpoint corruption/recovery behavior.

## 2.3.1 — 2026-09-05

- Added a first-class live YouTube acceptance command: `npm run acceptance:youtube`.
- The acceptance runner executes the same production `discoverChannel` and `fetchTranscript` paths used by the app against a real public channel.
- Outcomes are explicit and automation-friendly: `PASS` (0), `FAIL` (1), `BLOCKED` (2), or `INCONCLUSIVE` (3).
- The runner reports which discovery path was used, whether yt-dlp is installed, sampled video count, and a validated real caption result with word/segment counts.
- Added offline regression coverage for CLI parsing, error classification, and help behavior.


## 2.3.0 — 2026-09-05

- Added opt-in **durable server-side channel monitoring** so scheduled public-caption refreshes can continue while the browser is closed.
- Added persistent monitor state, per-video transcript checkpoint files, atomic generation indexes, and restart recovery that reuses hash-validated orphan checkpoints after interrupted runs.
- Added incremental monitor refreshes: unchanged transcript files are reused and only new/retryable videos are fetched.
- Added recent-no-caption retry windows plus a five-consecutive-transient-failure circuit breaker that preserves the previous completed snapshot.
- Added protected monitor operator APIs for list/upsert/pause/run/delete and integrity-chained NDJSON snapshot streaming.
- Added a dedicated `MONITOR_ACCESS_TOKEN`; production/reverse-proxy deployments never receive tokenless monitor administration merely because the proxy connects over loopback.
- Added the browser **Background monitors** UI with scheduling interval/language controls, monitor status, explicit server refresh, pause/resume, delete, and newer-generation snapshot import.
- Monitor snapshot import is staged, transcript-hash checked, generation/count/rolling-chain validated, and atomically replaces only the corresponding knowledge-base source.
- Monitor generation metadata is preserved through knowledge-base backups so restored browser archives can still detect whether the server has a newer snapshot.
- Added `/data` as the Docker persistent-data mount and defaulted `MONITOR_DATA_DIR=/data/monitoring`; monitoring remains disabled by default until `MONITORING_ENABLED=true`.
- Added bounded monitor caption concurrency, monitor rate limits, durable-data privacy/security guidance, and monitor module PWA packaging.
- Added bounded scheduler-level channel concurrency via `MONITOR_JOB_CONCURRENCY` plus a pending-tick mechanism so monitors added/resumed while a scheduler pass is busy are checked promptly instead of waiting for the next poll.
- Monitor generations now preserve two-point public view/subscriber observations; Channel Analysis converts those timestamps into clearly labeled observed views/day and subscriber-growth/day, with coverage, correction handling, source-linked momentum ranking, and explicit separation from private YouTube Studio metrics.
- Added server/manager regression tests covering persistence, restart recovery, incremental fetches, circuit breaking, snapshot integrity, operator authentication, reverse-proxy safety, and asynchronous run triggering.

## 2.2.0 — 2026-09-05

- Added a worker-backed **Channel Analysis** workspace for every synced channel.
- Added public-metric scorecards, cadence analysis, duration/content-type mix, transcript coverage, and public subscriber counts when available.
- Added age-bucket normalized video performance, overperformer/underperformer detection, views/day, and underused-winner topic hypotheses.
- Added recurring title-topic and transcript-theme analysis plus 180-day rising/falling topic momentum.
- Added title-format correlation tables for how-to, question, numbered/list, divider, bracketed, high-caps, short, and long titles.
- Added transcript-derived claim, recommendation, prediction, and strategy/rule evidence with direct YouTube timestamp links.
- Added structured strategy-completeness tagging across entry, stop, target, timeframe, risk, and indicator signals.
- Added two-channel comparisons for publishing metrics, shared topics, distinct strengths, and potential content gaps.
- Added JSON and Markdown channel-analysis report exports and click-through from analysis topics into the knowledge-base search.
- Analysis runs off the main thread, caches reports by source-sync timestamp plus analysis schema version, and keeps bounded evidence memory for very large channels.
- Transcript capture now reuses already-fetched public watch metadata to refresh view count, publish date, duration, title, and stream classification without an extra analytics request.
- Channel discovery/source metadata now preserves public follower/subscriber counts when exposed by YouTube/yt-dlp.
- Added analysis regression coverage for public-count parsing, relative dates, age normalization, source evidence, topic momentum, strategy completeness, channel comparison, offline-shell packaging, and public watch-metadata enrichment.
- Removing a channel now also removes its cached analysis report so deleted sources leave no stale derived report data behind.

## 2.1.0 — 2026-09-05

- Semantic vectors are now signed-int8 quantized, reducing vector storage by roughly 75% versus Float32.
- The derived vector database stores no transcript plaintext; hits are reconstructed and hash-validated against the canonical corpus.
- Large unfiltered semantic indexes use 12-bit locality buckets for bounded candidate pruning; filtered searches retain exact indexed scans.

- Added a persistent multi-channel **ingestion queue** with per-video resume state, bounded caption concurrency, upstream circuit breaking, and staged source replacement.
- Queue ingestion is free-caption-only; it never invokes paid speech-to-text automatically.
- Added a provider-neutral OpenAI-compatible embeddings gateway protected by the same paid-AI access token.
- Added explicit-cost semantic indexing with passage-count confirmation, 100,000-passage per-run safety ceiling, provider/model fingerprints, transcript/content hashes, unchanged-vector reuse, and staged atomic index replacement.
- Added separate browser-local semantic IndexedDB storage so vectors remain derived/rebuildable and do not bloat canonical knowledge-base backups.
- Added **Hybrid** semantic + lexical retrieval and **Semantic only** retrieval with timestamped source passages.
- Semantic results are validated against current transcript SHA-256 hashes before display, preventing stale evidence after corpus changes.
- Added semantic-result AI synthesis using the existing evidence-only, citation-validated research endpoint.
- Added queue/manual knowledge-base mutation interlocking to prevent staging-store races.
- Added queue resume transaction fixes, source-added-at preservation, vector metadata refresh on reused embeddings, and embedding response/vector validation.
- Health now reports embedding configuration/provider fingerprint independently from transcription/research readiness.
- Service worker now includes queue + semantic modules/workers in the offline application shell.

## 2.0.0 — 2026-09-05

- Renamed the product to **YouTube Knowledge Engine**.
- Added a persistent multi-channel knowledge-base layer while preserving the hardened single-channel ingestion workspace.
- Added collections and per-channel collection assignment.
- Added atomic staging/sync of the current channel into the knowledge base.
- Added cross-channel token-aware search with source/collection filters, relevance scoring, distinct-channel coverage summaries, timestamp context, and direct source links.
- Added cross-channel local Research Mode with source diversity and provenance.
- Extended optional AI research synthesis to preserve channel/source identity in evidence.
- Added local topic exploration across the filtered corpus.
- Added full knowledge-base transcript viewer.
- Added RAG JSONL export for the multi-channel corpus.
- Added integrity-chained `.ykb.jsonl` knowledge-base backup/import with staged atomic restore.
- Added dedicated IndexedDB stores (`kb_sources`, `kb_videos`, `kb_meta`, `kb_staging`) and schema v10.
- Avoided automatic whole-corpus search/topic scans at startup to keep very large knowledge bases responsive.
- Added URL/import sanitization and transcript-field whitelisting for knowledge-base backups.
- Added v2 regression tests for multi-channel storage, atomic sync, knowledge retrieval, provenance, backups, and service-worker packaging.

## 1.1.0 — 2026-09-05

### Security / cost safety
- Fixed reverse-proxy paid-AI authorization so a loopback proxy connection cannot make a remote visitor appear tokenless-local.
- Paid AI access token is sent in a header instead of normal browser JSON bodies.
- Added hostname/port-correct same-origin validation, localhost DNS-rebinding protection, IPv6-loopback handling, bounded rate-limit state, request/body/header/connection limits, and `Retry-After` responses.
- Removed the user-key YouTube Data API path from arbitrary-channel archive discovery; legacy API-key requests now fail closed.
- Isolated yt-dlp from host config with `--ignore-config` and bounded public YouTube/provider response sizes.
- Rejects non-public/live/upcoming media and oversized estimated/downloaded audio before paid transcription when detectable.

### Reliability / durability
- AI checkpoints upgraded to validated schema 2 and are bound to provider/model/chunk/format/API configuration before reuse.
- Added bounded yt-dlp and paid-AI semaphores, in-flight request de-duplication, cache-aware billable throttling, and cost-safe one-attempt defaults.
- Added client mutation locks, cancellable stale searches, offline-aware stopping, clean fatal IndexedDB/quota shutdown, and a five-consecutive-failure upstream circuit breaker.
- Channel refresh is two-phase and crash-safe: discovery stages first; archive/unarchive state commits atomically afterward.
- Project backup upgraded to schema 3 with a rolling SHA-256 chain over header + every video row, duplicate/truncation/hash validation, staging, and atomic active-library replacement.
- IndexedDB upgraded to schema v9 with deterministic order indexing and automatic repair of legacy rows missing an order value.

### Scale / search / research
- Full transcript bodies remain IndexedDB-only; UI keeps lightweight summaries and renders long transcripts in bounded chunks.
- Deterministic ordered exports without full-library in-memory sorting.
- Streaming text/ZIP output in modern browsers; bounded fallbacks refuse unsafe giant in-memory exports.
- Search relevance scoring, exact timestamp matches, token-aware matching, phrase/exclusion syntax, and cancellable scans.
- Research ranking now reduces stopword noise, uses overlapping passages, suppresses near-duplicate same-video evidence, and validates AI citations.

### Acquisition / quality
- Caption acquisition is genuinely layered: direct public tracks then language-aware yt-dlp fallback even when the direct watch/caption request itself is blocked.
- Added bounded YouTube page/caption response readers and public-availability checks.
- Added transcript quality diagnostics and archive verification for hashes, word counts, and exact duplicate transcript bodies.

### Operations / release quality
- Added unique request IDs, `Server-Timing`, bounded keep-alive/header/request behavior, max connections, and deadline-bounded graceful shutdown.
- Added runtime capability reporting for yt-dlp/ffmpeg/AI readiness.
- PWA shell now has an icon, offline navigation fallback, stronger keyboard focus and ARIA semantics.
- CI now tests Node 22 + 24 and builds the production Docker image.
- Docker retains non-root execution, pinned yt-dlp, health check, and now declares SIGTERM shutdown explicitly.
- Expanded environment template plus `SECURITY.md` and `PRIVACY.md` operator guidance.
- Added deterministic `SOURCE-SHA256SUMS.txt` generation/verification for extracted-release integrity.

## 1.0.0 — 2026-09-05

- Rebuilt and preserved the full source tree after an earlier intermediate source was not retained.
- Videos + Shorts + Streams/replays reconciliation with 50,000-item safety ceiling and archive-on-refresh behavior.
- Manual/auto caption selection, preferred language, YouTube translation, JSON3/XML/VTT parsing, and rolling-caption de-duplication.
- Separate free-caption and paid-transcription endpoints.
- Optional persistent paid-transcription chunk checkpoints.
- Summary-only browser RAM model; transcript bodies remain in IndexedDB.
- Worker-based search and local Research Mode.
- Offline pause/resume, coordinated rate-limit backoff, Wake Lock, before-unload protection, quota display, crash recovery.
- SHA-256 transcript integrity, attempt history, quality diagnostics, duplicate detection, and repair.
- Streaming exports and staged project backup/import.
- Initial production HTTP hardening, non-root Docker, ffmpeg, yt-dlp, health check, Railway config, and CI.
