# CatalogMirror

CatalogMirror is a read-only Shopify catalog integrity monitor. It compares Shopify Admin product truth with the public Online Store product representation and creates incidents for missing products/variants, price mismatches, and availability mismatches.

## Production design

- Shopify React Router template and latest App Bridge embedded pattern.
- GraphQL Admin API only; no REST Admin API calls.
- Read-only scopes: `read_products,read_inventory`.
- PostgreSQL via Prisma for sessions, audit history, and incidents.
- Mandatory Shopify privacy webhooks use framework HMAC authentication.
- Public-app distribution and Shopify App Pricing; no legacy Billing API implementation.
- Retry/backoff for storefront 429/502/503/504 responses.
- Products with no `onlineStoreUrl` are `EXPECTED_EXCLUSION`, not false failures.
- Railway Docker deployment and `/health` endpoint included.
- GitHub Actions validates type generation, TypeScript, and the production build for CatalogMirror changes.

## One-time setup against the existing CatalogMirror app

1. Provision PostgreSQL in Railway and set `DATABASE_URL`.
2. Set `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`, `SHOPIFY_APP_URL`, and `SCOPES=read_products,read_inventory`.
3. From this folder run `shopify app config link` and select the existing **CatalogMirror** app. This fills the real `client_id` and aligns the repository with that app without changing distribution.
4. Replace every `CHANGE-ME.example` in `shopify.app.toml` with the production HTTPS domain.
5. Run `shopify app deploy` to release app configuration/webhook subscriptions.
6. Deploy the web service to Railway. Startup runs `prisma migrate deploy` before serving.
7. In Partner Dashboard, configure Shopify App Pricing and create a Partner API client with **Manage apps** permission. Set `SHOPIFY_PARTNER_ORG_ID`, `SHOPIFY_PARTNER_API_ACCESS_TOKEN`, and `SHOPIFY_APP_GID`, then set `BILLING_ENFORCED=true`. CatalogMirror will redirect stores without an active subscription to Shopify's hosted pricing page.

## Development

```bash
npm install
npx prisma generate
npm run typecheck
npm run dev
```

## Audit semantics

For active Online Store products, CatalogMirror fetches `/products/{handle}.js` from the product's own Online Store origin. Variant matching prefers SKU and falls back to variant title. Expected availability is derived from Shopify inventory tracking, inventory quantity, and `CONTINUE` inventory policy.

`AVAILABILITY_MISMATCH`, `PRICE_MISMATCH`, `MISSING_VARIANT`, and `MISSING_STOREFRONT_PRODUCT` are critical. Storefront transport failures are warnings so transient outages don't become false catalog corruption incidents.

## App Store submission checklist

- [ ] Production domain and callback URLs replace placeholders in `shopify.app.toml`.
- [ ] `shopify app config link` points to the existing CatalogMirror app.
- [ ] `shopify app deploy` has released the 2026-07 webhook configuration.
- [ ] Automated compliance checks confirm bad-HMAC requests are rejected.
- [ ] Chrome incognito install/auth works without third-party cookies/local storage dependence.
- [ ] App opens directly to usable UI after install/auth.
- [ ] Test catalog has examples for correct data, price mismatch, availability mismatch, missing variant, and expected exclusion.
- [ ] Privacy policy, terms, support URL, and support email are completed in the App Store listing.
- [ ] Shopify App Pricing plans are configured in Partner Dashboard.
- [ ] App listing states that the Online Store sales channel is required for parity monitoring.
- [ ] Listing accurately states the app is read-only and never modifies merchant catalog data.

## Known operational boundary

The production domain and Shopify `client_id` are intentionally not guessed in source control. They must be linked to the already-created CatalogMirror app before release. This preserves the existing app identity and avoids accidentally creating or switching distribution.
