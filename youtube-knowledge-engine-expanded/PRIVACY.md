# Privacy

YouTube Knowledge Engine is local-first. The active channel workspace and the multi-channel knowledge base are stored in browser IndexedDB by default.

## Stored in the browser

Full transcript records, timestamps, catalog metadata, hashes, quality flags, attempts/errors, and project metadata are stored in the browser's IndexedDB for the current site origin.

## Server behavior

The bundled server does not require a persistent database for the normal browser knowledge base. It maintains bounded in-memory caches while running. Optional `AI_CHECKPOINT_DIR` persists completed paid-transcription chunks if configured; those files are private runtime data and are explicitly blocked by the release-audit gate from source packages. **If background monitoring is enabled, `MONITOR_DATA_DIR` stores complete monitored-channel transcript snapshots and lightweight channel indexes on the server filesystem/volume.**

## External services

- Public channel/caption extraction contacts YouTube and may invoke yt-dlp.
- Captionless-video AI transcription sends downloaded audio chunks to the configured speech-to-text provider.
- Optional AI Research synthesis sends only the selected evidence passages and question to the configured research provider; it does not send the whole archive by default.
- Optional Visual Intelligence sends the selected public YouTube video URL and a bounded analysis prompt to the configured multimodal provider. The provider—not this application—retrieves/processes the public video for that request.

Provider requests may be subject to that provider's logging, retention, billing, and privacy terms.

## Exports

Exports and `.cts.jsonl` backups can contain the full transcript archive. Treat them as potentially sensitive files and protect/delete them according to your needs.

## Cookies

If `YTDLP_COOKIES_FILE` is configured, it is consumed server-side only. Do not commit or share that file.


## Multi-channel knowledge base

Cross-channel search, topic exploration, collection filtering, and local evidence retrieval operate directly on IndexedDB in the browser. The full knowledge-base corpus is not sent to the Node server for these operations. Optional AI synthesis receives only the evidence passages selected for the current question.

## Semantic index and ingestion queue

The multi-channel ingestion queue stores pending jobs and per-video progress in a separate browser IndexedDB database. Queue processing contacts YouTube through the bundled server but does not invoke paid speech-to-text automatically.

The optional semantic index is also browser-local and derived from the transcript corpus. When you explicitly build or refresh it, bounded transcript passages are sent to the configured embedding provider. Semantic searches send the query text to that provider to obtain a query vector. Provider/model requests may be logged, retained, or billed according to that provider's terms.

Stored embedding vectors are quantized in browser IndexedDB and are excluded from `.ykb.jsonl` knowledge-base backups because they are derived and provider-specific. Clearing the semantic index does not delete transcripts.

## Channel Analysis

Channel Analysis is computed locally from the browser-stored knowledge base and observed public YouTube metadata. The deterministic report does not send transcripts, titles, or metrics to a new analytics service. Optional semantic indexing and AI synthesis retain their existing explicit provider behavior.

## Background monitoring

Background monitoring is disabled by default. When enabled/configured, the server contacts YouTube on the schedule you choose and stores successful public-caption transcripts under `MONITOR_DATA_DIR` so work can continue while no browser tab is open. These server-side transcript copies remain until the monitor is deleted or the data directory is removed.

The browser monitor token is session-only DOM state and is not intentionally written to localStorage, IndexedDB, exports, or server monitor state. Completed snapshots are streamed back only through the protected monitor endpoint and then imported into the browser knowledge base after integrity validation.

Treat the monitor data directory/volume as private transcript storage. Hosting-provider filesystem snapshots/backups may retain those files according to the provider's retention policy.


## Public-count observations

When background monitoring is enabled, completed snapshots may retain the current and immediately previous public video view count and public subscriber count together with their observation timestamps. These two-point observations are used locally after import to estimate observed public-count change per day. They are public observations, not private YouTube Studio analytics, and can inherit rounding/delay from YouTube's public presentation.


## Audience comment sampling

Audience intelligence is opt-in. When requested, the server uses yt-dlp to retrieve a bounded public top-level comment sample for selected public videos. The application deliberately discards commenter names and channel/profile identifiers before returning comments to the browser. Raw comment samples are held only in bounded in-memory server cache; the browser persists only the derived audience report in IndexedDB. Comment text itself may still contain personal information voluntarily written by commenters, so exported audience reports should be handled accordingly.

## Visual intelligence

Visual Intelligence is opt-in and never runs automatically. When requested, the server sends a canonical public YouTube video URL plus a bounded analysis prompt to the configured multimodal provider. The provider API key remains on the server. The browser receives only a sanitized structured report containing timestamped observations, bounded OCR/on-screen text, visible values, confidence/importance labels, and conservative visual/spoken relationship fields.

Visual reports are stored in browser IndexedDB as derived research data. They can contain text or values visible in the source video and therefore may reproduce information shown publicly by the creator. They are deleted when the associated knowledge-base source is removed. JSON/Markdown analysis exports may include these visual findings. Provider-side logging, retention, quota, and billing are governed by the provider's terms.


## Desktop local storage

The desktop build keeps the primary compatible knowledge base in the embedded webview's IndexedDB and maintains an additional local SQLite recovery mirror in the operating system application-data directory. The mirror contains channel/source metadata and transcript records already present in the user's local knowledge base. It is not uploaded by the desktop feature. Background-monitor files and optional AI transcription checkpoints are also redirected to the application-data directory rather than the installation directory.

## Desktop secure credentials and SQLite (v3.1)

In desktop builds, the durable knowledge-base copy is stored in a private application-data SQLite database. IndexedDB is retained as a compatibility/search cache for the local worker pipeline and is reconciled from the SQLite generation on startup. AI-provider API keys are stored in the operating system credential manager and are not included in knowledge-base backups, SQLite records, analysis exports, or browser storage.
