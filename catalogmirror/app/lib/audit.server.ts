import crypto from "node:crypto";
import db from "../db.server";

type AdminClient = { graphql: (query: string, options?: { variables?: Record<string, unknown> }) => Promise<Response> };

type AdminVariant = {
  id: string;
  title: string;
  sku: string | null;
  price: string;
  inventoryQuantity: number | null;
  inventoryPolicy: "DENY" | "CONTINUE";
  inventoryItem: { tracked: boolean } | null;
};

type AdminProduct = {
  id: string;
  title: string;
  handle: string;
  status: string;
  onlineStoreUrl: string | null;
  variants: { nodes: AdminVariant[] };
};

type StorefrontVariant = { id: number; title: string; sku?: string | null; available: boolean; price: number };
type StorefrontProduct = { id: number; title: string; handle: string; variants: StorefrontVariant[] };

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

const PRODUCTS_QUERY = `#graphql
  query CatalogMirrorProducts($first: Int!, $after: String) {
    products(first: $first, after: $after, sortKey: UPDATED_AT, reverse: true) {
      nodes {
        id title handle status onlineStoreUrl
        variants(first: 100) {
          nodes { id title sku price inventoryQuantity inventoryPolicy inventoryItem { tracked } }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
`;

function fingerprint(f: Finding) {
  return crypto.createHash("sha256").update([
    f.productId || "", f.variantId || "", f.kind, f.expectedValue || "", f.observedValue || ""
  ].join("|")).digest("hex");
}

function expectedAvailable(v: AdminVariant) {
  if (!v.inventoryItem?.tracked) return true;
  if (v.inventoryPolicy === "CONTINUE") return true;
  return (v.inventoryQuantity ?? 0) > 0;
}

async function fetchWithRetry(url: string, attempts = 4): Promise<Response> {
  let last: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(url, {
        headers: { "user-agent": "CatalogMirror/1.0 (+catalog-integrity-monitor)" },
        signal: AbortSignal.timeout(12000),
      });
      if (![429, 502, 503, 504].includes(res.status)) return res;
      last = new Error(`HTTP ${res.status}`);
    } catch (error) {
      last = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 400 * 2 ** i));
  }
  throw last instanceof Error ? last : new Error("Storefront request failed");
}

function storefrontJsonUrl(product: AdminProduct) {
  if (!product.onlineStoreUrl) return null;
  const url = new URL(product.onlineStoreUrl);
  return `${url.origin}/products/${encodeURIComponent(product.handle)}.js`;
}

async function auditProduct(product: AdminProduct): Promise<Finding[]> {
  if (!product.onlineStoreUrl) {
    return [{
      productId: product.id, productTitle: product.title, handle: product.handle,
      kind: "EXPECTED_EXCLUSION", severity: "INFO",
      detail: "Product has no Online Store URL, so public storefront parity is not applicable.",
    }];
  }

  const url = storefrontJsonUrl(product)!;
  let response: Response;
  try {
    response = await fetchWithRetry(url);
  } catch (error) {
    return [{
      productId: product.id, productTitle: product.title, handle: product.handle,
      kind: "STOREFRONT_FETCH_FAILED", severity: "WARNING",
      expectedValue: "reachable", observedValue: "request failed",
      detail: error instanceof Error ? error.message : String(error),
    }];
  }

  if (response.status === 404) {
    return [{
      productId: product.id, productTitle: product.title, handle: product.handle,
      kind: "MISSING_STOREFRONT_PRODUCT", severity: "CRITICAL",
      expectedValue: "published product", observedValue: "404",
    }];
  }
  if (!response.ok) {
    return [{
      productId: product.id, productTitle: product.title, handle: product.handle,
      kind: "STOREFRONT_FETCH_FAILED", severity: "WARNING",
      expectedValue: "HTTP 200", observedValue: `HTTP ${response.status}`,
    }];
  }

  let storefront: StorefrontProduct;
  try {
    storefront = await response.json() as StorefrontProduct;
  } catch {
    return [{
      productId: product.id, productTitle: product.title, handle: product.handle,
      kind: "INVALID_STOREFRONT_PAYLOAD", severity: "CRITICAL",
      expectedValue: "valid product JSON", observedValue: "invalid JSON",
    }];
  }

  const findings: Finding[] = [];
  const bySku = new Map(storefront.variants.filter(v => v.sku).map(v => [v.sku!, v]));
  const byTitle = new Map(storefront.variants.map(v => [v.title, v]));

  for (const av of product.variants.nodes) {
    const sv = (av.sku && bySku.get(av.sku)) || byTitle.get(av.title);
    if (!sv) {
      findings.push({
        productId: product.id, productTitle: product.title, handle: product.handle,
        variantId: av.id, variantTitle: av.title, sku: av.sku,
        kind: "MISSING_VARIANT", severity: "CRITICAL",
        expectedValue: "variant visible", observedValue: "variant absent",
      });
      continue;
    }

    const adminCents = Math.round(Number(av.price) * 100);
    if (Number.isFinite(adminCents) && adminCents !== Number(sv.price)) {
      findings.push({
        productId: product.id, productTitle: product.title, handle: product.handle,
        variantId: av.id, variantTitle: av.title, sku: av.sku,
        kind: "PRICE_MISMATCH", severity: "CRITICAL",
        expectedValue: `$${(adminCents / 100).toFixed(2)}`,
        observedValue: `$${(Number(sv.price) / 100).toFixed(2)}`,
      });
    }

    const expected = expectedAvailable(av);
    if (expected !== Boolean(sv.available)) {
      findings.push({
        productId: product.id, productTitle: product.title, handle: product.handle,
        variantId: av.id, variantTitle: av.title, sku: av.sku,
        kind: "AVAILABILITY_MISMATCH", severity: "CRITICAL",
        expectedValue: expected ? "available" : "sold out",
        observedValue: sv.available ? "available" : "sold out",
      });
    }
  }
  return findings;
}

async function loadProducts(admin: AdminClient, limit: number) {
  const products: AdminProduct[] = [];
  let after: string | null = null;
  while (products.length < limit) {
    const first = Math.min(50, limit - products.length);
    const res = await admin.graphql(PRODUCTS_QUERY, { variables: { first, after } });
    const body = await res.json() as any;
    if (body.errors?.length) throw new Error(body.errors.map((e: any) => e.message).join("; "));
    const connection = body.data?.products;
    products.push(...(connection?.nodes || []));
    if (!connection?.pageInfo?.hasNextPage || !connection.pageInfo.endCursor) break;
    after = connection.pageInfo.endCursor;
  }
  return products;
}

export async function runCatalogAudit(args: { admin: AdminClient; shop: string; limit?: number }) {
  const limit = Math.max(1, Math.min(args.limit || Number(process.env.AUDIT_PRODUCT_LIMIT || 100), 500));
  const run = await db.auditRun.create({ data: { shop: args.shop, status: "RUNNING" } });

  try {
    const products = await loadProducts(args.admin, limit);
    const currentFingerprints = new Set<string>();
    const priorOpen = await db.incident.findMany({
      where: { shop: args.shop, status: "OPEN" },
      select: { id: true, fingerprint: true },
    });
    const priorByFingerprint = new Map(priorOpen.map((i) => [i.fingerprint, i.id]));
    let findingsCount = 0, critical = 0, expected = 0, audited = 0;

    for (const product of products) {
      if (product.status !== "ACTIVE") continue;
      audited += 1;
      const findings = await auditProduct(product);
      for (const finding of findings) {
        const fp = fingerprint(finding);
        currentFingerprints.add(fp);
        findingsCount += 1;
        if (finding.severity === "CRITICAL") critical += 1;
        if (finding.kind === "EXPECTED_EXCLUSION") expected += 1;
        const existingId = priorByFingerprint.get(fp);
        const data = {
          auditRunId: run.id,
          productId: finding.productId,
          productTitle: finding.productTitle,
          handle: finding.handle,
          variantId: finding.variantId,
          variantTitle: finding.variantTitle,
          sku: finding.sku || null,
          kind: finding.kind,
          severity: finding.severity,
          expectedValue: finding.expectedValue,
          observedValue: finding.observedValue,
          detail: finding.detail,
          fingerprint: fp,
          status: "OPEN",
          lastSeenAt: new Date(),
          resolvedAt: null,
        };
        if (existingId) {
          await db.incident.update({ where: { id: existingId }, data });
        } else {
          await db.incident.create({ data: { shop: args.shop, ...data } });
        }
      }
    }

    const resolvedIds = priorOpen.filter(i => !currentFingerprints.has(i.fingerprint)).map(i => i.id);
    if (resolvedIds.length) {
      await db.incident.updateMany({ where: { id: { in: resolvedIds } }, data: { status: "RESOLVED", resolvedAt: new Date() } });
    }

    return await db.auditRun.update({
      where: { id: run.id },
      data: {
        status: "COMPLETED", productsSeen: products.length, productsAudited: audited,
        findings: findingsCount, critical, expected, finishedAt: new Date(),
      },
    });
  } catch (error) {
    await db.auditRun.update({
      where: { id: run.id },
      data: { status: "FAILED", errorMessage: error instanceof Error ? error.message : String(error), finishedAt: new Date() },
    });
    throw error;
  }
}
