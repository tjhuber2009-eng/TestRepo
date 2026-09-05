import crypto from "node:crypto";
import { lookup } from "node:dns/promises";
import db from "../db.server";
import {
  adminPriceToCents,
  buildAjaxCartUrl,
  buildAjaxProductUrl,
  expectedAvailable,
  fingerprintIdentity,
  isPrivateAddress,
  isSameStorefrontHost,
  matchStorefrontVariant,
  shopifyNumericId,
  validateStorefrontUrl,
  type AdminVariantCore,
  type StorefrontVariantCore,
} from "./audit-core";

type AdminClient = {
  graphql: (query: string, options?: { variables?: Record<string, unknown> }) => Promise<Response>;
};

type AdminVariant = AdminVariantCore;

type AdminProduct = {
  id: string;
  title: string;
  handle: string;
  status: string;
  onlineStoreUrl: string | null;
};

type StorefrontVariant = StorefrontVariantCore;
type StorefrontProduct = {
  id: number | string;
  title: string;
  handle: string;
  variants: StorefrontVariant[];
};

type Finding = {
  productId?: string;
  productTitle?: string;
  handle?: string;
  variantId?: string;
  variantTitle?: string;
  sku?: string | null;
  kind: string;
  severity: "CRITICAL" | "WARNING" | "INFO";
  expectedValue?: string;
  observedValue?: string;
  detail?: string;
};

type ProductAuditResult = {
  product: AdminProduct;
  findings: Finding[];
  canReconcile: boolean;
  audited: boolean;
};

type PageInfo = { hasNextPage: boolean; endCursor: string | null };
type ProductConnectionData = { nodes: AdminProduct[]; pageInfo: PageInfo };
type VariantConnectionData = { nodes: AdminVariant[]; pageInfo: PageInfo };
type ProductsQueryData = { products: ProductConnectionData };
type ProductsByIdsData = { nodes: Array<AdminProduct | null> };
type VariantsQueryData = { product: { variants: VariantConnectionData } | null };
type ShopContextData = { shop: { currencyCode: string } };
type StorefrontCurrencyCache = Map<string, Promise<string>>;

type GraphqlError = { message?: string; extensions?: { code?: string } };
type GraphqlEnvelope<T> = {
  data?: T;
  errors?: GraphqlError[];
  extensions?: {
    cost?: {
      throttleStatus?: {
        currentlyAvailable?: number;
        restoreRate?: number;
      };
    };
  };
};

const SHOP_CONTEXT_QUERY = `#graphql
  query CatalogMirrorShopContext {
    shop { currencyCode }
  }
`;

const PRODUCTS_QUERY = `#graphql
  query CatalogMirrorProducts($first: Int!, $after: String) {
    products(first: $first, after: $after, sortKey: UPDATED_AT, reverse: true) {
      nodes { id title handle status onlineStoreUrl }
      pageInfo { hasNextPage endCursor }
    }
  }
`;

const PRODUCTS_BY_IDS_QUERY = `#graphql
  query CatalogMirrorProductsByIds($ids: [ID!]!) {
    nodes(ids: $ids) {
      ... on Product { id title handle status onlineStoreUrl }
    }
  }
`;

const VARIANTS_QUERY = `#graphql
  query CatalogMirrorVariants($id: ID!, $first: Int!, $after: String) {
    product(id: $id) {
      variants(first: $first, after: $after) {
        nodes {
          id
          title
          sku
          price
          availableForSale
          inventoryQuantity
          inventoryPolicy
          inventoryItem { tracked }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
`;

const RETRYABLE_HTTP = new Set([429, 500, 502, 503, 504]);
const STOREFRONT_MAX_BYTES = 5 * 1024 * 1024;

export class AuditInProgressError extends Error {
  constructor() {
    super("A catalog audit is already running for this shop. Wait for it to finish before starting another.");
    this.name = "AuditInProgressError";
  }
}

function clampInt(value: number, fallback: number, min: number, max: number) {
  return Number.isFinite(value) ? Math.max(min, Math.min(Math.trunc(value), max)) : fallback;
}

export function getAuditMaxProducts() {
  return clampInt(Number(process.env.AUDIT_PRODUCT_LIMIT || 250), 250, 25, 500);
}

export function getAuditConcurrency() {
  return clampInt(Number(process.env.AUDIT_CONCURRENCY || 4), 4, 1, 8);
}

function truncate(value: string, max = 1800) {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function retryAfterMs(response: Response, attempt: number) {
  const retryAfter = response.headers.get("retry-after");
  const seconds = retryAfter ? Number(retryAfter) : NaN;
  if (Number.isFinite(seconds) && seconds >= 0) return Math.min(seconds * 1000, 10_000);
  return Math.min(500 * 2 ** attempt, 8_000);
}

async function adminQuery<T>(
  admin: AdminClient,
  query: string,
  variables: Record<string, unknown>,
): Promise<T> {
  let lastError: unknown;

  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      const response = await admin.graphql(query, { variables });
      const body = (await response.json()) as GraphqlEnvelope<T>;

      if (RETRYABLE_HTTP.has(response.status)) {
        lastError = new Error(`Shopify Admin API HTTP ${response.status}`);
        await wait(retryAfterMs(response, attempt));
        continue;
      }

      if (!response.ok) {
        throw new Error(`Shopify Admin API HTTP ${response.status}`);
      }

      const errors = body.errors ?? [];
      if (errors.length) {
        const throttled = errors.every((error) => error.extensions?.code === "THROTTLED");
        if (throttled) {
          const throttle = body.extensions?.cost?.throttleStatus;
          const restoreRate = throttle?.restoreRate || 50;
          const available = throttle?.currentlyAvailable || 0;
          const adaptiveDelay = Math.max(250, Math.ceil(((100 - available) / restoreRate) * 1000));
          lastError = new Error("Shopify Admin API throttled");
          await wait(Math.min(Math.max(adaptiveDelay, 500 * 2 ** attempt), 8_000));
          continue;
        }
        throw new Error(truncate(errors.map((error) => error.message || "GraphQL error").join("; ")));
      }

      if (!body.data) throw new Error("Shopify Admin API returned no data");
      return body.data;
    } catch (error) {
      lastError = error;
      if (attempt === 4) break;
      await wait(Math.min(500 * 2 ** attempt, 8_000));
    }
  }

  throw lastError instanceof Error ? lastError : new Error("Shopify Admin API request failed");
}

async function loadProducts(admin: AdminClient, limit: number) {
  const products: AdminProduct[] = [];
  let after: string | null = null;
  let hasMore = false;

  while (products.length < limit) {
    const first = Math.min(100, limit - products.length);
    const data: ProductsQueryData = await adminQuery<ProductsQueryData>(
      admin,
      PRODUCTS_QUERY,
      { first, after },
    );

    products.push(...(data.products?.nodes ?? []));
    hasMore = Boolean(data.products?.pageInfo?.hasNextPage);
    if (!hasMore || !data.products.pageInfo.endCursor) break;
    after = data.products.pageInfo.endCursor;
  }

  return { products, hasMore };
}

async function loadProductsByIds(admin: AdminClient, ids: string[]) {
  const uniqueIds = Array.from(new Set(ids.filter((id) => /^gid:\/\/shopify\/Product\/\d+$/.test(id))));
  const products: AdminProduct[] = [];

  for (let offset = 0; offset < uniqueIds.length; offset += 100) {
    const chunk = uniqueIds.slice(offset, offset + 100);
    const data: ProductsByIdsData = await adminQuery<ProductsByIdsData>(
      admin,
      PRODUCTS_BY_IDS_QUERY,
      { ids: chunk },
    );
    for (const node of data.nodes ?? []) {
      if (node?.id) products.push(node);
    }
  }

  return products;
}

async function loadVariants(admin: AdminClient, productId: string) {
  const variants: AdminVariant[] = [];
  let after: string | null = null;
  let coverageLimited = false;

  while (variants.length < 250) {
    const first = Math.min(100, 250 - variants.length);
    const data: VariantsQueryData = await adminQuery<VariantsQueryData>(
      admin,
      VARIANTS_QUERY,
      { id: productId, first, after },
    );

    if (!data.product) return { variants: [], coverageLimited: false, productMissing: true };

    const connection: VariantConnectionData = data.product.variants;
    variants.push(...(connection.nodes ?? []));
    if (!connection.pageInfo.hasNextPage || !connection.pageInfo.endCursor) break;
    if (variants.length >= 250) {
      coverageLimited = true;
      break;
    }
    after = connection.pageInfo.endCursor;
  }

  return { variants, coverageLimited, productMissing: false };
}

function fingerprint(finding: Finding) {
  return crypto.createHash("sha256").update(fingerprintIdentity(finding)).digest("hex");
}

async function assertPublicDns(hostname: string) {
  if (/\.myshopify\.com$/i.test(hostname)) return;
  const results = await lookup(hostname, { all: true, verbatim: true });
  if (!results.length || results.some((result) => isPrivateAddress(result.address))) {
    throw new Error("Storefront hostname resolves to a private or disallowed network address");
  }
}

async function fetchStorefront(url: URL, attempts = 3) {
  const originalHost = url.hostname;
  let current = url;
  let lastError: unknown;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      await assertPublicDns(current.hostname);
      let response = await fetch(current, {
        headers: {
          accept: "application/json",
          "cache-control": "no-cache",
          "user-agent": "CatalogMirror/1.1 (+catalog-integrity-monitor)",
        },
        cache: "no-store",
        redirect: "manual",
        signal: AbortSignal.timeout(clampInt(Number(process.env.STOREFRONT_TIMEOUT_MS || 10_000), 10_000, 3_000, 20_000)),
      });

      for (let redirect = 0; redirect < 3 && response.status >= 300 && response.status < 400; redirect += 1) {
        const location = response.headers.get("location");
        if (!location) break;
        const next = validateStorefrontUrl(new URL(location, current).toString());
        if (!isSameStorefrontHost(originalHost, next.hostname)) {
          throw new Error("Storefront redirected to an unexpected hostname");
        }
        current = next;
        await assertPublicDns(current.hostname);
        response = await fetch(current, {
          headers: {
            accept: "application/json",
            "cache-control": "no-cache",
            "user-agent": "CatalogMirror/1.1 (+catalog-integrity-monitor)",
          },
          cache: "no-store",
          redirect: "manual",
          signal: AbortSignal.timeout(clampInt(Number(process.env.STOREFRONT_TIMEOUT_MS || 10_000), 10_000, 3_000, 20_000)),
        });
      }

      if (!RETRYABLE_HTTP.has(response.status)) return response;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }

    if (attempt < attempts - 1) await wait(400 * 2 ** attempt);
  }

  throw lastError instanceof Error ? lastError : new Error("Storefront request failed");
}

async function readStorefrontProduct(response: Response) {
  const declaredLength = Number(response.headers.get("content-length") || 0);
  if (declaredLength > STOREFRONT_MAX_BYTES) throw new Error("Storefront product payload is unexpectedly large");

  const text = await response.text();
  if (Buffer.byteLength(text, "utf8") > STOREFRONT_MAX_BYTES) {
    throw new Error("Storefront product payload exceeded the safety limit");
  }

  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("json") && !text.trimStart().startsWith("{")) {
    throw new Error("Storefront returned a non-JSON response");
  }

  const parsed = JSON.parse(text) as Partial<StorefrontProduct>;
  if (!parsed || !Array.isArray(parsed.variants) || typeof parsed.handle !== "string") {
    throw new Error("Storefront product JSON is missing required fields");
  }

  return parsed as StorefrontProduct;
}

async function getPresentmentCurrency(
  onlineStoreUrl: string,
  cache: StorefrontCurrencyCache,
) {
  const cartUrl = buildAjaxCartUrl(onlineStoreUrl);
  const key = cartUrl.toString();
  const cached = cache.get(key);
  if (cached) return cached;

  const pending = (async () => {
    const response = await fetchStorefront(cartUrl);
    if (!response.ok) throw new Error(`Storefront cart currency request returned HTTP ${response.status}`);

    const declaredLength = Number(response.headers.get("content-length") || 0);
    if (declaredLength > 1024 * 1024) throw new Error("Storefront cart payload is unexpectedly large");

    const text = await response.text();
    if (Buffer.byteLength(text, "utf8") > 1024 * 1024) {
      throw new Error("Storefront cart payload exceeded the safety limit");
    }

    const body = JSON.parse(text) as { currency?: unknown };
    if (typeof body.currency !== "string" || !/^[A-Z]{3,4}$/i.test(body.currency)) {
      throw new Error("Storefront cart response did not include a valid currency");
    }
    return body.currency.toUpperCase();
  })();

  cache.set(key, pending);
  return pending;
}

async function auditProduct(
  admin: AdminClient,
  product: AdminProduct,
  shopCurrency: string,
  currencyCache: StorefrontCurrencyCache,
): Promise<ProductAuditResult> {
  if (product.status !== "ACTIVE") {
    return { product, findings: [], canReconcile: true, audited: false };
  }

  if (!product.onlineStoreUrl) {
    return {
      product,
      audited: true,
      canReconcile: true,
      findings: [{
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        kind: "EXPECTED_EXCLUSION",
        severity: "INFO",
        detail: "Product is not published to the Online Store, so public storefront parity is not applicable.",
      }],
    };
  }

  let variantsResult: Awaited<ReturnType<typeof loadVariants>>;
  try {
    variantsResult = await loadVariants(admin, product.id);
  } catch (error) {
    return {
      product,
      audited: true,
      canReconcile: false,
      findings: [{
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        kind: "ADMIN_VARIANT_FETCH_FAILED",
        severity: "WARNING",
        expectedValue: "variant data available",
        observedValue: "Admin API request failed",
        detail: truncate(error instanceof Error ? error.message : String(error)),
      }],
    };
  }

  if (variantsResult.productMissing) {
    return { product, findings: [], canReconcile: true, audited: false };
  }

  let storefrontUrl: URL;
  try {
    storefrontUrl = buildAjaxProductUrl(product.onlineStoreUrl);
  } catch (error) {
    return {
      product,
      audited: true,
      canReconcile: false,
      findings: [{
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        kind: "UNSAFE_STOREFRONT_URL",
        severity: "WARNING",
        expectedValue: "public HTTPS storefront URL",
        observedValue: "URL rejected",
        detail: truncate(error instanceof Error ? error.message : String(error)),
      }],
    };
  }

  let response: Response;
  try {
    response = await fetchStorefront(storefrontUrl);
  } catch (error) {
    return {
      product,
      audited: true,
      canReconcile: false,
      findings: [{
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        kind: "STOREFRONT_FETCH_FAILED",
        severity: "WARNING",
        expectedValue: "reachable",
        observedValue: "request failed",
        detail: truncate(error instanceof Error ? error.message : String(error)),
      }],
    };
  }

  if (response.status === 404) {
    return {
      product,
      audited: true,
      canReconcile: true,
      findings: [{
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        kind: "MISSING_STOREFRONT_PRODUCT",
        severity: "CRITICAL",
        expectedValue: "published product",
        observedValue: "404",
      }],
    };
  }

  if (!response.ok) {
    return {
      product,
      audited: true,
      canReconcile: false,
      findings: [{
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        kind: "STOREFRONT_FETCH_FAILED",
        severity: "WARNING",
        expectedValue: "HTTP 200",
        observedValue: `HTTP ${response.status}`,
      }],
    };
  }

  let storefront: StorefrontProduct;
  try {
    storefront = await readStorefrontProduct(response);
  } catch (error) {
    return {
      product,
      audited: true,
      canReconcile: false,
      findings: [{
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        kind: "INVALID_STOREFRONT_PAYLOAD",
        severity: "WARNING",
        expectedValue: "valid product JSON",
        observedValue: "invalid or unexpected response",
        detail: truncate(error instanceof Error ? error.message : String(error)),
      }],
    };
  }

  const findings: Finding[] = [];
  let canReconcile = !variantsResult.coverageLimited;
  let priceComparable = true;

  try {
    const presentmentCurrency = await getPresentmentCurrency(product.onlineStoreUrl, currencyCache);
    if (presentmentCurrency !== shopCurrency) {
      priceComparable = false;
      canReconcile = false;
      findings.push({
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        kind: "PRICE_PRESENTMENT_CURRENCY",
        severity: "INFO",
        expectedValue: shopCurrency,
        observedValue: presentmentCurrency,
        detail: "Price parity is skipped because the public storefront is serving a different presentment currency than Shopify Admin.",
      });
    }
  } catch (error) {
    priceComparable = false;
    canReconcile = false;
    findings.push({
      productId: product.id,
      productTitle: product.title,
      handle: product.handle,
      kind: "PRICE_CURRENCY_UNVERIFIED",
      severity: "WARNING",
      expectedValue: shopCurrency,
      observedValue: "unknown",
      detail: truncate(error instanceof Error ? error.message : String(error)),
    });
  }

  if (variantsResult.coverageLimited) {
    findings.push({
      productId: product.id,
      productTitle: product.title,
      handle: product.handle,
      kind: "VARIANT_COVERAGE_LIMIT",
      severity: "WARNING",
      expectedValue: "complete variant parity",
      observedValue: "first 250 variants verifiable",
      detail: "Shopify's Ajax Product API returns at most 250 variants. CatalogMirror will not falsely resolve prior findings for this product.",
    });
  }

  const adminProductId = shopifyNumericId(product.id);
  if (adminProductId && String(storefront.id) !== adminProductId) {
    canReconcile = false;
    findings.push({
      productId: product.id,
      productTitle: product.title,
      handle: product.handle,
      kind: "STOREFRONT_PRODUCT_ID_MISMATCH",
      severity: "CRITICAL",
      expectedValue: adminProductId,
      observedValue: String(storefront.id),
    });
  }

  if (storefront.handle !== product.handle) {
    canReconcile = false;
    findings.push({
      productId: product.id,
      productTitle: product.title,
      handle: product.handle,
      kind: "STOREFRONT_HANDLE_MISMATCH",
      severity: "CRITICAL",
      expectedValue: product.handle,
      observedValue: storefront.handle,
    });
  }

  const matchedStorefrontIds = new Set<string>();

  for (const adminVariant of variantsResult.variants) {
    const match = matchStorefrontVariant(adminVariant, storefront.variants);
    if (match.ambiguous) {
      canReconcile = false;
      findings.push({
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        variantId: adminVariant.id,
        variantTitle: adminVariant.title,
        sku: adminVariant.sku,
        kind: "AMBIGUOUS_VARIANT_MATCH",
        severity: "WARNING",
        expectedValue: "one storefront variant",
        observedValue: "multiple candidates",
        detail: `Fallback match by ${match.strategy || "unknown"} was ambiguous.`,
      });
      continue;
    }

    const storefrontVariant = match.variant;
    if (!storefrontVariant) {
      findings.push({
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        variantId: adminVariant.id,
        variantTitle: adminVariant.title,
        sku: adminVariant.sku,
        kind: "MISSING_VARIANT",
        severity: "CRITICAL",
        expectedValue: "variant visible",
        observedValue: "variant absent",
      });
      continue;
    }

    matchedStorefrontIds.add(String(storefrontVariant.id));

    if (priceComparable) {
      const adminCents = adminPriceToCents(adminVariant.price);
      const storefrontCents = Number(storefrontVariant.price);
      if (adminCents === null || !Number.isFinite(storefrontCents)) {
        canReconcile = false;
        findings.push({
          productId: product.id,
          productTitle: product.title,
          handle: product.handle,
          variantId: adminVariant.id,
          variantTitle: adminVariant.title,
          sku: adminVariant.sku,
          kind: "INVALID_PRICE_DATA",
          severity: "WARNING",
          expectedValue: adminVariant.price,
          observedValue: String(storefrontVariant.price),
        });
      } else if (adminCents !== storefrontCents) {
        findings.push({
          productId: product.id,
          productTitle: product.title,
          handle: product.handle,
          variantId: adminVariant.id,
          variantTitle: adminVariant.title,
          sku: adminVariant.sku,
          kind: "PRICE_MISMATCH",
          severity: "CRITICAL",
          expectedValue: `${(adminCents / 100).toFixed(2)}`,
          observedValue: `${(storefrontCents / 100).toFixed(2)}`,
        });
      }
    }

    const expected = expectedAvailable(adminVariant);
    if (expected !== Boolean(storefrontVariant.available)) {
      findings.push({
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        variantId: adminVariant.id,
        variantTitle: adminVariant.title,
        sku: adminVariant.sku,
        kind: "AVAILABILITY_MISMATCH",
        severity: "CRITICAL",
        expectedValue: expected ? "available" : "sold out",
        observedValue: storefrontVariant.available ? "available" : "sold out",
      });
    }
  }

  if (!variantsResult.coverageLimited) {
    for (const storefrontVariant of storefront.variants) {
      if (matchedStorefrontIds.has(String(storefrontVariant.id))) continue;
      findings.push({
        productId: product.id,
        productTitle: product.title,
        handle: product.handle,
        variantId: `storefront:${storefrontVariant.id}`,
        variantTitle: storefrontVariant.title,
        sku: storefrontVariant.sku,
        kind: "UNEXPECTED_STOREFRONT_VARIANT",
        severity: "CRITICAL",
        expectedValue: "variant absent",
        observedValue: "variant present on storefront",
      });
    }
  }

  return { product, findings, canReconcile, audited: true };
}

async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T) => Promise<R>,
) {
  const results = new Array<R>(items.length);
  let nextIndex = 0;

  async function runWorker() {
    while (true) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= items.length) return;
      results[index] = await worker(items[index]);
    }
  }

  await Promise.all(Array.from({ length: Math.min(concurrency, Math.max(items.length, 1)) }, runWorker));
  return results;
}

async function acquireAuditLease(shop: string, owner: string) {
  const now = new Date();
  const expiresAt = new Date(now.getTime() + 60 * 60_000);
  const rows = await db.$queryRaw<Array<{ owner: string }>>`
    INSERT INTO "AuditLease" ("shop", "owner", "expiresAt", "updatedAt")
    VALUES (${shop}, ${owner}, ${expiresAt}, CURRENT_TIMESTAMP)
    ON CONFLICT ("shop") DO UPDATE
      SET "owner" = EXCLUDED."owner",
          "expiresAt" = EXCLUDED."expiresAt",
          "updatedAt" = CURRENT_TIMESTAMP
      WHERE "AuditLease"."expiresAt" < ${now}
    RETURNING "owner"
  `;

  if (rows.length !== 1 || rows[0].owner !== owner) throw new AuditInProgressError();
}

async function releaseAuditLease(shop: string, owner: string) {
  await db.auditLease.deleteMany({ where: { shop, owner } });
}

export async function runCatalogAudit(args: {
  admin: AdminClient;
  shop: string;
  limit?: number;
  trigger?: string;
  productIds?: string[];
}) {
  const maxProducts = getAuditMaxProducts();
  const requested = args.limit ?? maxProducts;
  const limit = clampInt(Number(requested), maxProducts, 1, maxProducts);
  const owner = crypto.randomUUID();
  const started = Date.now();

  await acquireAuditLease(args.shop, owner);

  let run: { id: string } | null = null;

  try {
    run = await db.auditRun.create({
      data: { shop: args.shop, status: "RUNNING", trigger: args.trigger || "MANUAL" },
      select: { id: true },
    });
    const shopContext: ShopContextData = await adminQuery<ShopContextData>(
      args.admin,
      SHOP_CONTEXT_QUERY,
      {},
    );
    const shopCurrency = shopContext.shop.currencyCode.toUpperCase();
    const currencyCache: StorefrontCurrencyCache = new Map();

    const targetedProductIds = args.productIds?.length ? args.productIds : null;
    const loaded = targetedProductIds
      ? { products: await loadProductsByIds(args.admin, targetedProductIds), hasMore: false }
      : await loadProducts(args.admin, limit);
    const { products, hasMore } = loaded;

    const results = await mapWithConcurrency(
      products,
      getAuditConcurrency(),
      (product) => auditProduct(args.admin, product, shopCurrency, currencyCache),
    );

    const currentFingerprints = new Set<string>();
    const reconcilableProductIds = new Set<string>();
    let findingsCount = 0;
    let critical = 0;
    let warnings = 0;
    let expected = 0;
    let audited = 0;

    for (const result of results) {
      if (result.audited) audited += 1;
      if (result.canReconcile) reconcilableProductIds.add(result.product.id);

      for (const finding of result.findings) {
        const fp = fingerprint(finding);
        currentFingerprints.add(fp);
        findingsCount += 1;
        if (finding.severity === "CRITICAL") critical += 1;
        if (finding.severity === "WARNING") warnings += 1;
        if (finding.kind === "EXPECTED_EXCLUSION") expected += 1;

        const now = new Date();
        await db.incident.upsert({
          where: { shop_fingerprint: { shop: args.shop, fingerprint: fp } },
          create: {
            shop: args.shop,
            auditRunId: run.id,
            productId: finding.productId,
            productTitle: finding.productTitle,
            handle: finding.handle,
            variantId: finding.variantId,
            variantTitle: finding.variantTitle,
            sku: finding.sku || null,
            kind: finding.kind,
            severity: finding.severity,
            status: "OPEN",
            expectedValue: finding.expectedValue,
            observedValue: finding.observedValue,
            detail: finding.detail,
            fingerprint: fp,
            lastSeenAt: now,
          },
          update: {
            auditRunId: run.id,
            productTitle: finding.productTitle,
            handle: finding.handle,
            variantTitle: finding.variantTitle,
            sku: finding.sku || null,
            severity: finding.severity,
            status: "OPEN",
            expectedValue: finding.expectedValue,
            observedValue: finding.observedValue,
            detail: finding.detail,
            occurrenceCount: { increment: 1 },
            lastSeenAt: now,
            resolvedAt: null,
          },
        });
      }
    }

    const reconcilable = Array.from(reconcilableProductIds);
    if (reconcilable.length) {
      const fingerprintFilter = currentFingerprints.size
        ? { fingerprint: { notIn: Array.from(currentFingerprints) } }
        : {};

      await db.incident.updateMany({
        where: {
          shop: args.shop,
          status: "OPEN",
          productId: { in: reconcilable },
          ...fingerprintFilter,
        },
        data: { status: "RESOLVED", resolvedAt: new Date() },
      });
    }

    const finishedAt = new Date();
    let pendingChanges: number | undefined;

    if (!targetedProductIds && !hasMore) {
      await db.auditTask.deleteMany({
        where: {
          shop: args.shop,
          updatedAt: { lte: new Date(started) },
          OR: [
            { lockedUntil: null },
            { lockedUntil: { lt: finishedAt } },
          ],
        },
      });
      pendingChanges = await db.auditTask.count({ where: { shop: args.shop } });
    }

    await db.shopAuditState.upsert({
      where: { shop: args.shop },
      create: {
        shop: args.shop,
        lastAuditAt: finishedAt,
        ...(pendingChanges !== undefined ? { pendingChanges } : {}),
      },
      update: {
        lastAuditAt: finishedAt,
        ...(pendingChanges !== undefined ? { pendingChanges } : {}),
      },
    });

    return await db.auditRun.update({
      where: { id: run.id },
      data: {
        status: "COMPLETED",
        productsSeen: products.length,
        productsAudited: audited,
        findings: findingsCount,
        critical,
        warnings,
        expected,
        catalogTruncated: hasMore,
        durationMs: Date.now() - started,
        finishedAt,
      },
    });
  } catch (error) {
    if (run) {
      try {
        await db.auditRun.update({
          where: { id: run.id },
          data: {
            status: "FAILED",
            errorMessage: truncate(error instanceof Error ? error.message : String(error)),
            durationMs: Date.now() - started,
            finishedAt: new Date(),
          },
        });
      } catch (recordError) {
        console.error("CatalogMirror could not persist failed audit state", {
          shop: args.shop,
          auditRunId: run.id,
          error: recordError instanceof Error ? recordError.message : String(recordError),
        });
      }
    }
    throw error;
  } finally {
    try {
      await releaseAuditLease(args.shop, owner);
    } catch (releaseError) {
      console.error("CatalogMirror could not release audit lease", {
        shop: args.shop,
        error: releaseError instanceof Error ? releaseError.message : String(releaseError),
      });
    }
  }
}
