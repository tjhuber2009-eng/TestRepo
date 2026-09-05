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
- Product/create, product/update, product/delete, and inventory-level webhooks mark the catalog as changed.
- Shopify webhook delivery IDs are persisted and deduplicated. Failed processing releases the receipt for a Shopify retry; stale in-flight receipts can be reclaimed.
- Shop deletion removes CatalogMirror data.
- Shopify App Pricing uses the Partner API with bounded retry and cache behavior.
- Separate liveness (`/health`) and database readiness (`/ready`) endpoints.
- Security response headers without setting `X-Frame-Options`, which would break Shopify embedding.
- Multi-stage, non-root Node 24 production container.
- Docker build context excludes local environment files and development artifacts.
- Dependabot maintains npm and GitHub Actions dependencies weekly.
- CodeQL scans only the CatalogMirror JavaScript/TypeScript source with security-extended queries.

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
7. Prisma client generation
8. React Router type generation + TypeScript
9. Production build
10. Production server smoke test for `/health` and `/ready`
11. Production Docker image build

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
