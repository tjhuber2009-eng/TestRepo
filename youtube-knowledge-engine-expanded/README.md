# YouTube Knowledge Engine v3.1.0 — Secure Desktop Data & Providers

YouTube Knowledge Engine turns public YouTube channels into a **local-first, multi-channel knowledge base and channel-analysis workspace** with searchable transcripts, timestamped evidence, topic discovery, semantic retrieval, source-backed research, public-metric analysis, and portable archives.

The hardened transcript workspace remains available. v2.1 added persistent multi-channel queueing and optional semantic retrieval; v2.2 added worker-backed **Channel Analysis**; v2.3 added opt-in **durable server-side channel monitoring**; v2.4–v2.6 added cross-channel intelligence, audience analysis, longitudinal change detection, and timestamped **Visual Intelligence**. v3.0 introduced the desktop shell. **v3.1 promotes native SQLite to the authoritative durable desktop knowledge copy, retains IndexedDB as a compatibility/search cache, adds OS-secured provider credentials, and adds private bundled-tool discovery for yt-dlp/FFmpeg.**

## Desktop application (v3.1)

v3.0 introduced a desktop-first Tauri shell while preserving the proven browser/server workflow. The desktop application launches the existing Node backend as a bundled loopback-only sidecar, keeps monitoring alive when the main window is hidden to the system tray, and stores runtime data beneath the operating system application-data directory. End users do not need to install Node separately.
### Secure provider settings

Desktop API keys are configured from the tray menu via **AI provider settings**. That opens a separate bundled Tauri settings window; the main transcript/research webview is intentionally not granted privileged Tauri IPC. Secrets are stored in the operating system credential store (macOS Keychain Services, Windows Credential Manager, Linux Secret Service) and are injected only into the private loopback backend process. The non-secret URL/model/auth configuration is stored in the application-data directory. Saving settings restarts the private backend so changes take effect immediately.

### SQLite authority and browser cache

On desktop, `knowledge.sqlite` is now the authoritative durable copy for `kb_sources`, `kb_videos`, and `kb_meta`. The existing IndexedDB stores remain as a local execution/search compatibility cache so the proven Web Workers and semantic analysis paths do not need a risky one-shot rewrite. A generation marker reconciles the browser cache from SQLite on startup; completed browser mutations are committed back to SQLite. The manual controls are **Commit to SQLite now** and **Reload browser cache from SQLite**. Reload is staged and only replaces the browser cache after record counts and generation are stable.

### Optional bundled media tools

The desktop runtime prefers private `yt-dlp` and `ffmpeg` binaries placed in the application resources, then falls back to configured paths or system `PATH`. yt-dlp is explicitly pointed at the bundled Node runtime using `--js-runtimes node:<path>`. Third-party binaries are not silently downloaded into the source tree: installer builders must supply reviewed binaries with `npm run desktop:prepare-tools -- --yt-dlp <path> --ffmpeg <path>` and comply with the exact binary licenses documented in `THIRD_PARTY_NOTICES.md`.


Desktop security uses a per-launch random session token. The sidecar accepts the bootstrap token once, converts it to an HttpOnly SameSite cookie, and rejects direct unauthenticated access to the local server. The sidecar is forced to `127.0.0.1` even in production.

The main research webview is intentionally not granted application-command permissions. Tauri app commands are declared in the build manifest and the two provider-settings commands are capability-granted only to the bundled `settings` window. Provider HTTP endpoints must use HTTPS, except for exact local-loopback hosts (`localhost`, `127.0.0.1`, or `::1`); embedded URL credentials are rejected so secrets cannot be written to the plaintext provider-settings file. Desktop SQLite commit/reload participates in the shared knowledge-base mutation lock used by queue/import workflows, and startup restores from SQLite if the browser cache is unexpectedly empty even when a stale generation marker remains.

Developer commands:

```bash
npm run desktop:dev
npm run desktop:build
```

They require the normal Tauri/Rust platform prerequisites. `desktop:prepare-sidecar` copies the build machine's Node runtime into Tauri's target-specific sidecar location so packaged users do not need Node installed.


## Core workflow

1. Paste a public YouTube channel URL or `@handle` into the channel workspace.
2. Discover Videos, Shorts, and streams/replays.
3. Capture available captions. Resume or retry as needed.
4. Optionally use the explicit paid **AI fill missing** action for captionless videos.
5. Choose a knowledge-base collection and click **Sync current channel**.
6. Paste another channel and repeat—or add many channel URLs to the **Ingestion queue** and let the browser process them sequentially.
7. Search, explore topics, or ask questions across all synced channels.
8. Optionally configure an embedding provider and build a semantic index for conceptual/hybrid retrieval.
9. Open **Channel Analysis** to generate scorecards, cadence/topic reports, age-normalized outliers, source-linked claim/rule evidence, longitudinal changes, and cross-channel intelligence.
10. Optionally click **Sample comments** to analyze a bounded public top-comment sample for repeated audience questions, requests, pain points, and topics. Commenter identities are discarded by the application.
11. Optionally click **Analyze visuals** to inspect up to three high-value public videos per run for charts, settings, displayed metrics, code, software UI, demonstrations, and visual/spoken inconsistencies.

A knowledge-base sync is staged first and then committed atomically. If staging fails, the previously synced copy of that channel remains intact.




## Visual Intelligence (v2.6)

v2.6 adds an optional visual evidence layer beside transcripts and audience intelligence. It is designed for information that is visible but absent or ambiguous in speech—for example indicator settings, chart annotations, table values, backtest metrics, software controls, slide text, code, diagrams, or physical demonstrations.

The initial server adapter uses Gemini's multimodal video understanding with a **public YouTube URL** as the media input. Video bytes therefore do not need to be mass-downloaded through the YouTube Knowledge Engine server for this path. The provider can be configured for agentic processing on long videos or static processing where appropriate.

Each accepted visual event contains:

- a start/end timestamp and direct YouTube timestamp link;
- a bounded evidence type and importance level;
- a concise visible observation;
- bounded on-screen/OCR text;
- visible label/value/unit triples when present;
- model confidence;
- nearby spoken context when identified;
- a conservative relationship label: **visual-only**, **supports narration**, **potential conflict**, or **uncertain**.

`potential_conflict` is deliberately not a verdict. It means the model observed a material apparent mismatch that deserves source review. Likewise, a displayed profit factor, return, backtest result, medical claim, product specification, or other metric is stored as **creator-presented visual evidence**, not as independently verified truth.

Visual analysis is explicit and quota-aware. A browser action analyzes at most three not-yet-cached high-value candidate videos per run and requires confirmation before external AI use. Results are sanitized by the server and cached locally in IndexedDB. Re-running the action moves on to additional candidates. Provider API keys stay server-side.

Server configuration:

```bash
VISUAL_PROVIDER=gemini
VISUAL_GEMINI_API_KEY=...
VISUAL_MODEL=gemini-3.8-flash
VISUAL_PROCESSING=agentic
```

Use `AI_ACCESS_TOKEN` on hosted deployments to protect this external-AI endpoint just like paid transcription, embeddings, and research synthesis. Additional `VISUAL_*` environment settings bound token output, timeout, response size, hourly requests, and cache lifetime.

## Intelligence prioritization (v2.5)

v2.5 turns the v2.4 intelligence layer into a more actionable research workflow without claiming to know whether a creator's assertions are true.

- **Evidence strength** scores recurring idea families from 0–100 using passage recurrence, distinct supporting videos, passage specificity, and structured rule features. It measures corpus support that an idea was repeatedly expressed—not truth, profitability, or factual correctness.
- **Creator evolution** summarizes up to 16 recent calendar quarters, showing upload volume, leading title topics, and transcript-derived rule/performance-claim counts so shifts in a creator's focus are visible historically.
- **Priority intelligence alerts** rank newly surfaced idea families, potentially changed/opposite rules, new age-normalized outliers, and material topic shifts after a newer sync. Alerts remain evidence-linked and are research triage rather than autonomous conclusions.
- **Audience → content/research opportunities** combine repeated comment topics with explicit requests, questions, pain points, existing channel coverage, and underused-winner signals. The score prioritizes what may deserve investigation; it is not a prediction of future views.

All of these outputs are derived locally from the existing knowledge corpus plus optional privacy-minimized comment samples. They add no new paid model dependency.

## Intelligence layer (v2.4)

v2.4 moves beyond generic “chat with a channel” behavior and treats YouTube as a longitudinal research corpus. The derived intelligence layer is local-first and evidence-linked.

### Strategy / claim families

Transcript evidence is clustered into repeated idea families using bounded lexical similarity plus structured rule features. Channel reports can now surface:

- repeated strategy/rule families within a channel;
- repeated claims or recommendations across two independent channels;
- ideas that appear unique to either compared channel;
- **potential disagreements** when similar evidence contains conservative opposite-stance markers such as buy vs short, use vs avoid, or increase vs reduce.

These labels are research triage, not factual adjudication. Every surfaced family links back to timestamped source evidence for manual review.

### Longitudinal change detection

When a channel is synced again and analyzed, v2.4 archives a compact form of the previous derived report before replacing the current cache. The **Since the previous sync** panel reports:

- change in analyzed video count;
- newly surfaced strategy/claim families;
- potentially changed rules or stances;
- newly surfaced outlier videos;
- topic-count shifts.

Up to four compact prior derived snapshots are retained in browser IndexedDB. They are derived analysis data rather than a second transcript corpus. Monitor imports intentionally leave the previous analysis cache available so the next analysis can compare the new generation against it.

### Audience intelligence

Audience sampling is explicit and never runs automatically. When `yt-dlp` is available, the server can fetch a bounded sample of public top-level comments from a small set of recent/high-signal videos. The browser then derives:

- recurring audience topics;
- questions;
- requests for future content;
- heuristic pain points/complaints;
- sample-level positive/negative/question/request rates.

The server endpoint returns no commenter names, and the browser stores only the derived audience report. Author identity is discarded during normalization. Samples are biased toward the selected videos and YouTube's comment ordering, so they must not be treated as representative surveys.

## Background monitoring (v2.3)

v2.3 adds an opt-in server-side monitor for channels that should be checked even when the browser is closed. Monitoring is **disabled by default**. Enable automatic scheduling with `MONITORING_ENABLED=true`, configure a private persistent `MONITOR_DATA_DIR`, and use a long random `MONITOR_ACCESS_TOKEN` on hosted/production deployments.

Each monitor stores:

- a lightweight durable channel index;
- one integrity-hashed transcript file per successfully captured video;
- last-run/last-success/error/generation metadata;
- retry state for recent videos that initially have no captions.

Refresh behavior is incremental. Existing transcript files are reused, only new/retryable videos are fetched, and a completed generation replaces the importable index atomically. The scheduler can run a bounded number of channels in parallel with `MONITOR_JOB_CONCURRENCY` (default `1`, maximum `4`), while each channel separately respects `MONITOR_CAPTION_CONCURRENCY`. If the server stops after writing a new transcript but before committing the new generation, the orphan checkpoint is hash-validated and reused on the next run. Five consecutive transient upstream failures open a circuit breaker and leave the previous completed snapshot unchanged.

The browser's **Background monitors** card can:

- add, pause/resume, run, and delete server monitors;
- show last success, next scheduled check, ready transcript count, and new-video count;
- import only a newer completed snapshot;
- choose the destination collection at import time.

Monitor snapshots stream as integrity-chained NDJSON. Import is staged in IndexedDB and atomically replaces only that channel's knowledge-base copy after record counts, generation, transcript hashes, and the rolling chain validate. The shared browser mutation lock prevents monitor import from racing manual sync/import or the browser ingestion queue.

The server intentionally keeps the monitoring corpus separate from the browser's canonical knowledge base: background work can continue without an open tab, while search/semantic/channel-analysis behavior remains local-first after the completed snapshot is imported.

### Docker / hosted persistence

The Docker image provisions `/data` and defaults `MONITOR_DATA_DIR` to `/data/monitoring`. Mount a private persistent volume at `/data`; otherwise monitored snapshots can disappear when the deployment/container filesystem is replaced. On Railway or another hosted platform, attach a persistent volume and set `MONITOR_ACCESS_TOKEN` before enabling automatic monitoring.

## Channel Analysis (v2.2)

Channel Analysis runs locally in a Web Worker against one synced channel at a time, with an optional second channel for comparison. Reports are cached against the source sync timestamp so unchanged channels do not need to be rescanned.

The report includes:

- public subscriber count when available;
- video count, transcript coverage, total transcript words, and content-type mix;
- public median/average views and lifetime views/day when available;
- **observed view velocity** after at least two imported monitor generations, using actual public view-count change between observation timestamps;
- observed subscriber growth/day when two public subscriber observations are available;
- age-bucket performance index, comparing a video's views with the median for similarly aged videos on that channel;
- overperforming and underperforming video outliers;
- an Observed momentum panel ranking videos by monitor-to-monitor public view growth and reporting measured coverage;
- upload cadence, recent posting rate, weekday distribution, duration mix, and publication range;
- recurring title topics and transcript themes;
- 180-day topic-momentum analysis showing rising and falling title themes;
- title-format correlations such as how-to, question, numbered/list, bracketed, short, long, or high-caps titles;
- underused topics whose limited set of videos outperform their age bucket;
- heuristic transcript extraction of performance claims, recommendations, predictions, and strategy/rule passages;
- strategy-completeness tagging for passages containing combinations of entry, stop, target, timeframe, risk, and indicator language;
- timestamped links back to the original YouTube evidence;
- two-channel comparisons for publishing metrics, shared topics, distinct strengths, and potential content gaps;
- JSON and Markdown report export.

When captions are collected through the normal transcript route, v2.2 also reuses the already-open public watch metadata to refresh title, publish date, duration, view count, and stream classification without a separate analytics crawl. yt-dlp channel discovery can also contribute public view and follower counts when exposed by YouTube. Missing metrics remain missing; the analyzer does not fabricate private analytics.

### Analysis methodology and limits

The **performance index** is descriptive: public views divided by the median public views of videos in the same age bucket (`0–30d`, `31–90d`, `91–365d`, `1–3y`, `3y+`). This is more useful than directly comparing a brand-new upload with a five-year-old video, but it is not causal attribution.

Public counts can be abbreviated, delayed, corrected, unavailable, or captured at slightly different times. Relative publication dates from page fallbacks are anchored to sync time. Lifetime views/day is not the same as current velocity. v2.3 therefore labels monitor-to-monitor count deltas separately as **observed velocity**; those measurements are only as precise as the public count representation and observation interval, and are not YouTube Studio realtime analytics. The app does **not** infer or invent private YouTube Studio metrics such as impressions, click-through rate, average view duration, retention, revenue, or subscriber conversions. Title/topic correlations should be treated as research hypotheses rather than prescriptions.

## Multi-channel ingestion queue

v2.1 can ingest many public channels without manually rebuilding the staging workspace for each one. Add one channel URL or `@handle` per line, choose a destination collection, and click **Run queue**.

The queue:

- persists jobs and per-video progress in a separate IndexedDB database;
- uses only the free/public caption endpoint—paid AI fill is never invoked by the queue;
- uses bounded parallel caption requests and the same retry/backoff protections as the channel workspace;
- opens an upstream circuit breaker after repeated network/block failures;
- stages completed transcripts separately and replaces an existing source only after the queued refresh completes;
- resumes paused/interrupted jobs from the last persisted video;
- preserves the previous synced source if a refresh captures no usable transcripts.

The browser tab must remain open while the queue is actively making network requests. Closing the tab does not erase progress; the next run resumes from persisted queue state.

## Optional semantic / vector retrieval

v2.1 adds a provider-neutral OpenAI-compatible embedding gateway plus a local derived vector index. This is optional: all existing lexical search, topic exploration, and local Research Mode continue to work without embeddings.

Semantic indexing is explicit and potentially billable. The app first estimates the number of transcript passages and requires confirmation. It then:

- chunks timestamped transcripts into bounded passages;
- hashes every embedding input;
- reuses unchanged vectors when transcript hash + provider/model fingerprint still match;
- builds replacement vectors in a staging store so a failed rebuild leaves the previous index usable;
- quantizes normalized vectors to signed 8-bit values (about 75% smaller than Float32 storage);
- stores only derived vectors + IDs/hashes/timestamps in a separate IndexedDB database—**no transcript passage plaintext is stored in the vector database**;
- uses exact scans for small or channel/collection-filtered indexes and locality-bucket candidate pruning for large unfiltered indexes;
- reconstructs each hit from the canonical transcript corpus and validates both passage and transcript SHA-256 before showing it;
- supports **Hybrid** retrieval (semantic similarity + lexical relevance) and **Semantic only** retrieval;
- can feed the top semantic passages into the existing citation-validated AI synthesis endpoint.

Index builds are capped at 100,000 passages per operation. For very large corpora, filter to a collection or channel and index incrementally. The semantic index is derived and rebuildable, so `.ykb.jsonl` backups intentionally exclude vectors.

## Multi-channel knowledge base

v2 adds persistent IndexedDB stores for:

- synced channel/source metadata;
- full transcript records;
- collections;
- atomic sync/import staging.

The knowledge base is independent of the active channel workspace. Clearing or replacing the staging workspace does not remove synced knowledge-base sources.

### Collections

Create research collections such as:

- Trading
- AI
- Programming
- Business
- Specific research projects

Each synced channel belongs to a collection and can be moved later. Search, topics, and research can be filtered by collection and/or individual channel.

### Cross-channel search

Knowledge-base search scans transcript bodies in a Web Worker and supports:

- normal token-aware terms;
- `"exact phrases"`;
- `-excluded` terms;
- relevance scoring;
- distinct-channel coverage counts for each query, with one-click source filtering;
- channel filters;
- collection filters;
- timestamped matching context;
- direct links to the supporting YouTube timestamp;
- relevance, newest, and title sorting.

The page does **not** automatically scan the whole knowledge base at startup. Large-corpus searches and topic scans happen only when requested.

### Ask the knowledge base

Local Research Mode retrieves a bounded set of relevant transcript passages first. Evidence selection includes channel provenance and timestamp links and suppresses near-duplicate passages.

Optional AI synthesis sends only the displayed evidence passages—not the whole corpus—to the configured OpenAI-compatible research endpoint. Semantic indexing, when configured, sends only bounded transcript passages (and semantic search sends the query text) to the configured embedding provider. The server:

- treats transcript text as untrusted quoted data;
- preserves source/channel distinctions;
- requires `[S1]`, `[S2]`, etc. citations;
- validates returned source labels;
- rejects fabricated citation numbers.

This supports questions such as:

> Where do these creators agree and disagree about risk management?

> Find every strategy that specifies an entry, exit, stop loss, and timeframe.

> Which claims are repeated across multiple independent channels?

## Topic explorer

The local Web Worker can scan the filtered corpus and surface frequent meaningful topic terms by document frequency and mentions. Topic scans are explicit because large knowledge bases can contain tens of thousands of transcripts.

Clicking a topic launches a full knowledge-base search for that term.

## Knowledge-base portability

### RAG JSONL

**Export RAG JSONL** produces model-ready overlapping transcript chunks with:

- collection;
- source/channel ID and name;
- video ID and title;
- publication metadata;
- canonical video URL;
- timestamp URL;
- transcript SHA-256;
- chunk boundaries;
- chunk text.

### Knowledge-base backup

`.ykb.jsonl` backups preserve:

- collection settings;
- channel/source metadata;
- full transcript records;
- provenance and integrity hashes.

Backups use a rolling SHA-256 integrity chain plus source/video counts. Import is staged and validated before the active knowledge base is atomically replaced. Duplicate video IDs, malformed records, transcript hash mismatches, truncation, and chain corruption are rejected.

## Channel ingestion layer

The v1 capture system remains available and includes:

### Public-channel discovery

Discovery uses credential-free public extraction paths:

1. `yt-dlp` when installed (included in Docker), enumerating Videos, Shorts, and Streams/replays.
2. YouTube web / InnerTube fallback with continuation pagination.

A user-supplied YouTube Data API key is intentionally not used for arbitrary-channel archives.

Discovery deduplicates IDs, reconciles specialized Shorts/Stream metadata, preserves existing transcripts on refresh, and uses staged crash-safe refresh commits. The safety ceiling is 50,000 catalog entries per refresh with a visible warning if reached.

### Caption acquisition

- manual/creator captions preferred when appropriate;
- requested language selection;
- YouTube caption translation when supported;
- JSON3, XML/SRV, and WebVTT parsing;
- rolling-caption de-duplication;
- `yt-dlp` subtitle fallback;
- direct rejection of non-public metadata when detectable;
- timestamps retained throughout;
- bounded upstream response sizes.

### Optional paid transcription

Free captions and paid speech-to-text remain separate endpoints and UI actions.

A normal Build/Resume/Retry run never invokes paid transcription. **AI fill missing** requires a distinct action plus confirmation.

The paid path includes:

- public/live/duration validation before expensive work;
- audio-size and chunk-size limits;
- bounded AI concurrency;
- in-flight request deduplication;
- cost-safe provider retry defaults;
- ffmpeg chunking;
- optional durable chunk checkpoints bound to provider/configuration, hash-checked on reuse, bounded by `AI_MAX_CHECKPOINT_MB`, and atomically written with private permissions.

Release packaging is also guarded by `npm run release:audit`, which refuses common secret files, cookie exports, runtime transcript/checkpoint data, private keys, databases, captured media, archives, symlinks, and unexpectedly large source trees.

## Large-library behavior

The active channel workspace and the multi-channel knowledge base both keep full transcript bodies in IndexedDB instead of page RAM.

Other large-run safeguards include:

- Web Worker search/research;
- bounded search result sets;
- bounded-memory research candidates;
- incremental active-workspace metrics;
- throttled rendering;
- offline pause/resume;
- shared `Retry-After` backoff;
- upstream circuit breaker after repeated network/block failures;
- Wake Lock where supported;
- crash recovery;
- browser storage/quota reporting;
- streaming exports where browser File System Access is available.

## Security model

Local development binds to `127.0.0.1` by default. Production binds according to deployment configuration.

Important controls include:

- strict Host validation / DNS-rebinding protection;
- same-origin POST validation including port;
- JSON-only mutation endpoints;
- CSP and anti-framing headers;
- bounded request/header/connection limits;
- AI access-token protection;
- separate monitor operator-token protection for durable server state;
- reverse-proxy-safe paid-AI authorization;
- request IDs and timing headers;
- non-root Docker execution;
- graceful SIGTERM shutdown.

See [SECURITY.md](SECURITY.md).

## Privacy

Transcript libraries and the multi-channel knowledge base are stored in browser IndexedDB by default. They are not uploaded to the application server for search, topic exploration, or local evidence retrieval.

Optional paid transcription necessarily sends audio chunks to the configured speech-to-text provider. Optional AI synthesis sends only the selected evidence passages to the configured research provider. Optional semantic indexing sends bounded transcript passages to the configured embedding provider, and semantic queries send the query text. The derived vectors remain in browser IndexedDB; that vector database contains no transcript passage plaintext and is disposable/rebuildable.

See [PRIVACY.md](PRIVACY.md).

## Run locally

Requirements for normal public-caption use:

- Node.js 22+

```bash
npm start
```

Then open:

```text
http://127.0.0.1:3000
```

Windows users can also run `run.bat`. Unix-like systems can run `./run.sh`.

For the strongest YouTube extraction and paid transcription fallbacks, use the included Docker image because it installs the pinned `yt-dlp` release and ffmpeg.

## Docker

```bash
docker build -t youtube-knowledge-engine .
docker run --rm -p 3000:3000 youtube-knowledge-engine
```

Add environment variables only if you want optional AI features. See `.env.example`.

## Testing

```bash
npm run check
npm test
npm run manifest
npm run verify-manifest
```

`npm run verify` performs syntax checks, tests, and source-manifest verification.

## Architecture

```text
YouTube channel
      │
      ▼
Discovery: yt-dlp → YouTube web fallback
      │
      ▼
Single-channel staging workspace
      │
      ├── public captions
      └── optional paid speech-to-text
      │
      ▼
Atomic sync
      │
      ▼
Multi-channel IndexedDB knowledge base
      │
      ├── collections + resumable ingestion queue
      ├── cross-channel lexical search
      ├── optional semantic / hybrid vector retrieval
      ├── topic explorer + local evidence retrieval
      ├── channel analysis + comparisons
      ├── optional cited AI synthesis
      └── RAG / integrity-chained backups

Optional durable monitor service (server)
      │
      ├── scheduled credential-free discovery/caption refresh
      ├── per-video restart-safe transcript checkpoints
      └── integrity-chained completed snapshots → atomic browser import
```

## Current architectural boundary

v3.1.0 is desktop-first while retaining the web/self-hosted build. On desktop, SQLite is the authoritative durable knowledge copy while IndexedDB remains a compatibility/search cache for the existing worker-based retrieval and analysis paths. Background monitoring runs in the loopback sidecar and can continue while the main window is hidden to the system tray. The semantic vector index is still browser-local/derived and rebuildable. This is not yet multi-device cloud sync, a shared team database, or a hosted SaaS control plane; those remain separate future tiers.

## Live YouTube acceptance test

The automated suite validates parsers, storage, security, monitoring, knowledge-base behavior, and integration contracts without depending on YouTube being reachable from CI. To prove the final network/extractor boundary on the host where you intend to run the app, use the built-in live acceptance command:

```bash
npm run acceptance:youtube
```

By default this samples up to five recent public videos from `@OpenAI`, runs the same production channel-discovery code as the application, then runs the same production transcript function until it obtains a real public caption track.

You can choose another public channel:

```bash
npm run acceptance:youtube -- --channel @SomeChannel --max 10 --language en
```

For automation, add `--json`. Exit code `0` means **PASS**. Exit code `2` means **BLOCKED**: the host cannot currently reach or extract from YouTube, which is an environment/network result rather than a parser assertion failure. Exit code `3` means **INCONCLUSIVE** because the sampled videos had no usable public captions. Exit code `1` means **FAIL** and should be investigated as an application/protocol error.
