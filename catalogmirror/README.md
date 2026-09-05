# CatalogMirror

CatalogMirror is a read-only Shopify catalog integrity monitor. It compares Shopify Admin product truth with the public Online Store representation and creates durable incidents for storefront drift.

## What it verifies

- Product identity and handle parity.
- Missing or unexpected storefront variants.
- Variant prices, while respecting Shopify presentment currency.
- Variant availability using Shopify's own `availableForSale` field.
- Online Store publication exclusions.
- Storefront transport/payload failures without falsely resolving prior incidents.

## Production hardening

- Patched Shopify React Router SDK 2.x with current App Bridge initialization.
- GraphQL Admin API only; no legacy REST Admin API usage.
- Read-only scopes only: `read_products,read_inventory`.
- PostgreSQL/Prisma persistence with reproducible migrations.
- Stable incident fingerprints: changing observed values update the same incident instead of creating churn.
- Unique incident identity per shop and fingerprint.
- Conservative reconciliation: only successfully re-verified products can auto-resolve old findings.
- Partial scans never resolve incidents for products outside the scanned slice.
- Per-shop audit lease prevents overlapping audits and race-driven false resolutions.
- Bounded audit concurrency and retry/backoff for Admin API throttling and transient storefront failures.
- Locale-aware Ajax Product API and cart URLs.
- Presentment-currency verification through the locale-aware cart endpoint before price comparison.
- Shopify's 250-variant Ajax limit is detected and surfaced as a coverage warning rather than false missing-variant errors.
- Variant matching treats Shopify variant IDs as authoritative; legacy/fallback matching is only used when an ID cannot be parsed.
- Shopify `availableForSale` drives availability parity instead of hand-derived inventory heuristics.
- Storefront fetches require HTTPS, standard HTTPS ports, public network targets, bounded redirects, timeouts, and response-size limits.
- Product/create and product/update webhooks queue exact-product verification after a short debounce.
- Inventory-level webhooks queue the inventory item and resolve its associated product through Shopify Admin before auditing.
- Unknown-resource webhook payloads safely fall back to a coalesced shop-level verification task.
- Automatic audit work is persisted in PostgreSQL, so deploys/restarts do not lose queued checks.
- Multiple Railway replicas claim tasks atomically with `FOR UPDATE SKIP LOCKED`; generation checks prevent a newer webhook from being consumed by an older in-flight audit.
- Queue and audit locks use renewable short leases, giving long healthy audits exclusivity while allowing crashed workers to recover automatically.
- Failed automatic audits retry with bounded exponential backoff and surface the latest error in the dashboard.
- Shopify webhook delivery IDs are persisted and deduplicated. Failed processing releases the receipt for a Shopify retry; stale in-flight receipts can be reclaimed.
- Shop deletion removes CatalogMirror data.
- Shopify App Pricing uses the Partner API with bounded retry and cache behavior.
- Separate liveness (`/health`) and database readiness (`/ready`) endpoints.
- Security response headers without setting `X-Frame-Options`, which would break Shopify embedding.
- Multi-stage, non-root Node 24 production container.
- Docker build context excludes local environment files and development artifacts.
- Dependabot maintains npm and GitHub Actions dependencies weekly.
- CodeQL scans only the CatalogMirror JavaScript/TypeScript source with security-extended queries.

## Automatic monitoring

Automatic monitoring is enabled by default in production. Shopify webhooks are acknowledged only after their durable queue mutation succeeds; the web process then consumes queued work independently.

Product changes normally audit one exact product rather than rescanning the catalog. Inventory changes resolve their inventory item back to the associated product first. Bursts for the same resource are debounced and coalesced into one task.

The queue is deliberately generation-aware. If another webhook updates the same resource while an audit is running, the worker cannot delete the newer generation when the older audit finishes. If a process dies, renewable queue/audit leases expire and another healthy process can reclaim the work.

A successful full manual catalog audit removes only queue work that existed before that audit started. Webhooks arriving during the manual scan remain queued.

## Periodic reconciliation

Webhook delivery is not guaranteed, so CatalogMirror also runs periodic reconciliation. The scheduler uses Shopify's `updated_at` product search filter and persists a watermark only after successful discovery ingestion.

For large discovery windows, reconciliation uses Shopify GraphQL Bulk Operations. The bulk result is streamed line-by-line from JSONL, and discovered product IDs are placed into low-priority targeted audit tasks. Live product and inventory webhook tasks have higher queue priority, so background reconciliation cannot starve recent merchant changes.

The first reconciliation discovers all products up to a one-minute safety cutoff. Later runs use an overlapping updated-at window to tolerate delayed updates and clock-boundary effects. Failed runs do not advance the watermark.

## Audit behavior

Audits are ordered by Shopify product update time so recently changed products are checked first. `AUDIT_PRODUCT_LIMIT` is a safety cap, currently limited by the application to at most 500 products per interactive run.

If the shop has more products than the selected scan size, the run is explicitly marked partial. Existing incidents outside that slice are preserved.

Shopify's Ajax Product API returns at most 250 variants per product. CatalogMirror can verify the returned variants but cannot prove parity beyond that public endpoint limit. Large products receive `VARIANT_COVERAGE_LIMIT`, and older findings for that product are not auto-resolved from an incomplete view.

Ajax product prices are returned in the customer's presentment currency. CatalogMirror checks the locale-aware cart currency first. If that currency differs from the shop Admin currency, price parity is skipped and the condition is recorded instead of creating a false price incident.

## CI quality gate

Every CatalogMirror code change runs against Node 24 and a clean PostgreSQL 16 service:

1. Refresh the lockfile only while completing an intentional dependency upgrade.
2. `npm ci`
3. `npm audit --audit-level=low`
4. Native Node unit tests
5. `prisma validate`
6. `prisma migrate deploy` against a fresh database
7. PostgreSQL-backed automatic-audit queue regression test
8. Prisma client generation
9. React Router type generation + TypeScript
10. Production build
11. Production server smoke test for `/health` and `/ready`
12. Production Docker image build

GitHub Actions used by the security/build workflows are pinned to commit SHAs rather than floating action tags.

## One-time production setup

1. Provision PostgreSQL in Railway and set `DATABASE_URL`.
2. Set `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`, `SHOPIFY_APP_URL`, and `SCOPES=read_products,read_inventory`.
3. From this folder run `shopify app config link` and select the existing **CatalogMirror** app.
4. Replace every `CHANGE-ME.example` in `shopify.app.toml` with the production HTTPS domain.
5. Run `shopify app deploy` to release app configuration and webhook subscriptions.
6. Deploy the web service to Railway. Startup applies pending Prisma migrations before serving.
7. Configure Shopify App Pricing in Partner Dashboard. Set `SHOPIFY_PARTNER_ORG_ID`, `SHOPIFY_PARTNER_API_ACCESS_TOKEN`, `SHOPIFY_APP_GID`, `SHOPIFY_APP_HANDLE`, then set `BILLING_ENFORCED=true`.

## Environment controls

- `AUDIT_PRODUCT_LIMIT`: maximum products allowed in an interactive audit; app hard-cap is 500.
- `AUDIT_CONCURRENCY`: concurrent product checks, 1–8.
- `STOREFRONT_TIMEOUT_MS`: per-storefront request timeout, clamped to 3–20 seconds.
- `WEBHOOK_RECEIPT_TTL_DAYS`: deduplication receipt retention, clamped to 7–90 days.
- `AUTO_AUDIT_ENABLED`: automatic webhook-driven verification; defaults on in production.
- `AUTO_AUDIT_DEBOUNCE_SECONDS`: resource debounce window, clamped to 5–300 seconds.
- `AUTO_AUDIT_POLL_MS`: idle queue polling interval, clamped to 1–60 seconds.
- `AUTO_AUDIT_PRODUCT_LIMIT`: safety cap for fallback shop-level automatic audits; never exceeds the manual audit cap.
- `RECONCILIATION_ENABLED`: periodic missed-webhook reconciliation; defaults on in production.
- `RECONCILIATION_INTERVAL_MINUTES`: interval between successful reconciliation windows, clamped to 30 minutes–7 days.
- `RECONCILIATION_SCHEDULER_POLL_MS`: scheduler scan interval, clamped to 1–30 minutes.
- `RECONCILIATION_OVERLAP_MINUTES`: overlap before the previous watermark, clamped to 1–60 minutes.
- `RECONCILIATION_BULK_POLL_SECONDS`: Shopify bulk-operation polling interval, clamped to 15–300 seconds.
- `RECONCILIATION_MAX_RESULT_MB`: streamed JSONL safety cap, clamped to 10 MB–2 GB.
- `RECONCILIATION_MAX_PRODUCTS`: discovered-product safety cap, clamped to 1,000–2,000,000.

## App Store / Built for Shopify readiness checklist

- [ ] Production domain and callback URLs replace placeholders in `shopify.app.toml`.
- [ ] `shopify app config link` points to the existing CatalogMirror app.
- [ ] `shopify app deploy` has released the 2026-07 webhook configuration.
- [ ] Bad-HMAC webhook requests are rejected in a production integration test.
- [ ] Duplicate webhook delivery IDs are verified to produce one state change.
- [ ] Chrome incognito install/auth works without third-party cookie or local-storage dependence.
- [ ] App opens directly to usable UI after install/auth.
- [ ] Test catalog covers correct data, price mismatch, presentment-currency difference, availability mismatch, missing variant, extra variant, expected exclusion, large-product coverage warning, and transient storefront failure.
- [ ] Privacy policy, terms, support URL, and support email are complete in the App Store listing.
- [ ] Shopify App Pricing plans are configured and subscription redirects are tested.
- [ ] Listing states that the Online Store sales channel is required for parity monitoring.
- [ ] Listing accurately states that the app is read-only.
- [ ] Admin Web Vitals are measured after real merchant traffic is available.

## Deliberate operational boundary

Interactive audits are intentionally capped at 500 products. Scaling to very large catalogs should use a durable background job/queue plus Shopify bulk operations instead of keeping a merchant HTTP request open. CatalogMirror fails conservatively at that boundary rather than reporting false certainty.
