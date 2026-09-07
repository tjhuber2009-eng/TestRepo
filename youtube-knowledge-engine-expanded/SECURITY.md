# Security

## Safe defaults

Local development binds to `127.0.0.1`. Paid AI is not anonymously available on public/production deployments unless you explicitly configure it that way.

## Public deployment checklist

1. Set a long random `AI_ACCESS_TOKEN` if any paid transcription, embedding, research, or visual-analysis provider is configured.
2. Set a separate long random `MONITOR_ACCESS_TOKEN` before exposing monitor administration or enabling automatic monitoring on a hosted deployment.
3. Keep `AI_PUBLIC_FALLBACK=false` unless anonymous paid consumption is an intentional product choice.
4. Set `ALLOWED_HOSTS` to the production hostname(s).
5. Set `TRUST_PROXY=1` only behind a reverse proxy you control/trust that overwrites `X-Forwarded-For`; otherwise leave it disabled.
6. Store provider keys, access tokens, cookies, and checkpoint storage outside source control.
7. Keep `YTDLP_MAX_CONCURRENCY`, `AI_MAX_CONCURRENCY`, rate limits, response-size limits, audio limits, and `MAX_CONNECTIONS` bounded.
8. Terminate TLS at the trusted edge. HSTS is emitted when the server can determine the request is HTTPS.
9. Do not expose `YTDLP_COOKIES_FILE` or its contents to the browser. Use the least-privileged cookie set possible.
10. Persist `AI_CHECKPOINT_DIR` only on private storage; checkpoints can contain transcript text. Checkpoint files are written atomically with private file permissions, the directory is restricted where the platform permits it, and `AI_MAX_CHECKPOINT_MB` bounds corrupted/oversized checkpoint reads and writes.
11. Run `npm run release:audit` before packaging or publishing source. It rejects common secret files, cookie exports, private keys, runtime transcript/checkpoint directories, databases, captured media, archives, symlinks, and unexpectedly large release trees.
12. Review provider retention/privacy terms before enabling external AI.

## Trust boundaries

- Imported `.cts.jsonl` backups are untrusted and validated/staged before replacement.
- Transcript text is untrusted data. Research synthesis serializes it as data and instructs the model not to follow instructions contained in evidence.
- Browser archives are local to that browser profile. Anyone with access to the browser profile may be able to read them.
- The in-process server cache is ephemeral and not a durable security boundary.

## Reporting

Do not publish secrets or private media when reporting a problem. Include the app version, request ID from the failing response when available, and a sanitized reproduction.


## Knowledge-base imports

Knowledge-base backups are untrusted input. v2 stages imports separately, validates record counts and the rolling SHA-256 chain, rejects duplicate video IDs and transcript hash mismatches, sanitizes source URLs, bounds transcript/segment sizes, and only then atomically replaces the active knowledge-base stores.


## Visual-provider safety

`POST /api/visual/analyze` is an explicit external-AI endpoint and uses the same `AI_ACCESS_TOKEN` authorization boundary as other paid/provider-backed AI features. The browser never receives `VISUAL_GEMINI_API_KEY` or other provider credentials.

The visual gateway validates video IDs, clamps the research focus text, rate-limits both endpoint attempts and billable provider calls, deduplicates identical in-flight/cached requests, bounds provider response bytes and duration, requests structured output, and whitelists/sanitizes every persisted field. A malformed provider response fails closed instead of being stored as visual evidence.

The initial Gemini adapter passes only a canonical public YouTube watch URL plus a fixed research prompt. Treat provider processing, retention, billing, and regional availability as external-service concerns and review those terms before enabling the feature. `potential_conflict` output is research triage only and must not be presented as verified contradiction.

## Embedding-provider safety

Semantic indexing is treated as a paid/external-AI capability. `/api/embeddings` uses the same `AI_ACCESS_TOKEN` gate as paid transcription and AI research synthesis. Keep that token enabled on public deployments.

Configure embedding credentials only through server environment variables. Provider API keys are never sent to the browser. The gateway bounds batch size, text length, response size, vector dimensions, request duration, and hourly billable requests. It does not automatically retry an ambiguous provider timeout.

The semantic vector index is derived browser data. Results are checked against the current transcript SHA-256 before display so stale vectors cannot silently become evidence after a transcript changes.

## Queue mutation lock

The multi-channel ingestion queue and manual knowledge-base sync/import operations share an expiring browser mutation lock. This prevents two operations from using the knowledge-base staging store simultaneously. If a tab crashes, the lock expires automatically; persisted queue state remains resumable.

## Channel-analysis data

Channel-analysis reports are derived locally and cached in IndexedDB metadata. Treat exported reports as potentially sensitive research artifacts because they can contain transcript excerpts, claims, and direct source links. No new server-side analytics endpoint is required for deterministic channel analysis.

## Background-monitor security

Monitoring is a privileged server mutation capability: it writes durable transcript snapshots and triggers unattended YouTube requests. Automatic scheduling is disabled by default. On production or trusted-proxy deployments, monitor administration fails closed unless the request supplies the configured `MONITOR_ACCESS_TOKEN`. The monitor token is sent in `X-Monitor-Access-Token` and the browser does not persist it to localStorage or IndexedDB.

Keep `MONITOR_DATA_DIR` on private storage with restrictive filesystem/volume access because it can contain complete transcript text. Use one writable application instance per monitoring data directory; the implementation uses atomic per-monitor lock files to prevent duplicate work, but it is not a distributed database/consensus system. Do not expose the monitor data volume as static web content.


### Monitor resource controls

Keep `MONITOR_JOB_CONCURRENCY` conservative on small hosts (default `1`, maximum `4`) and use `MONITOR_CAPTION_CONCURRENCY` to bound per-channel upstream caption work. These independent limits prevent a large set of due channels from spawning unbounded channel or caption jobs.


## Public comment sampling

`POST /api/comments` is a credential-free public-data endpoint, not a paid-AI endpoint. It still requires same-origin JSON requests, validates YouTube video IDs, clamps each request to at most 200 top-level comments, uses the shared yt-dlp semaphore, applies `COMMENTS_RATE_LIMIT_PER_HOUR`, bounds process output, and caches results in memory to reduce repeated upstream work. The response omits commenter names and profile identifiers.


## Desktop sidecar boundary

The Tauri desktop build launches the Node backend on `127.0.0.1` only. Each launch generates a high-entropy session token. The initial Tauri URL presents that token once; the server converts it to an HttpOnly SameSite cookie and redirects to a clean URL. All desktop-mode requests are denied without the session cookie. Runtime SQLite, monitoring, and AI checkpoint data live under the OS application-data directory, outside packaged resources. The Tauri webview is not granted shell-spawn permissions; sidecar process creation occurs only in Rust.

## Desktop provider credentials (v3.1)

Desktop provider API keys are stored through the operating system credential store, not in IndexedDB, environment files, provider-settings JSON, logs, or exports. The privileged Tauri IPC surface is isolated to a bundled settings window. The main loopback research webview has no Tauri capability. If the platform secure credential store is unavailable, saving the secret fails closed; there is no plaintext fallback.

Desktop installers may bundle separate yt-dlp/FFmpeg executables. The runtime prefers private resource paths and explicitly supplies the bundled Node runtime to yt-dlp. Installer builders are responsible for verifying binary provenance and satisfying the exact third-party license/source obligations.

### v3.1 desktop command, credential, and data boundary

- The loopback research webview has no Tauri app-command capability. Provider status/save commands are declared in the Tauri application manifest and granted only to the bundled `settings` window.
- Provider secrets are stored through the OS credential store only; non-secret provider configuration is saved separately. The app rejects credentials embedded in provider URLs.
- Plain HTTP provider URLs are permitted only for exact local-loopback hosts (`localhost`, `127.0.0.1`, `::1`); other providers require HTTPS.
- Desktop SQLite commit/reload uses the same browser mutation lock as queue and monitor imports to avoid cross-store snapshots taken during a concurrent mutation.
- Desktop mode applies a restrictive Unix umask before runtime files are created.
- Optional yt-dlp/FFmpeg binaries are an installer/distributor responsibility and must be reviewed with the applicable third-party licenses before redistribution.
