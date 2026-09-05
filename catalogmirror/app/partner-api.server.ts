type Subscription = { billingPeriod: string } | null;

const cache = new Map<string, { value: Exclude<Subscription, null>; expiresAt: number }>();
const RETRYABLE = new Set([429, 500, 502, 503, 504]);

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function cleanupCache() {
  const now = Date.now();
  for (const [key, entry] of cache) {
    if (entry.expiresAt <= now) cache.delete(key);
  }
  if (cache.size > 500) {
    const oldest = [...cache.entries()].sort((a, b) => a[1].expiresAt - b[1].expiresAt);
    for (const [key] of oldest.slice(0, cache.size - 500)) cache.delete(key);
  }
}

function retryDelay(response: Response, attempt: number) {
  const retryAfter = Number(response.headers.get("retry-after"));
  if (Number.isFinite(retryAfter) && retryAfter >= 0) return Math.min(retryAfter * 1000, 10_000);
  return Math.min(500 * 2 ** attempt, 8_000);
}

export async function fetchActiveSubscription(shopId: string): Promise<Subscription> {
  cleanupCache();
  const cached = cache.get(shopId);
  if (cached && cached.expiresAt > Date.now()) return cached.value;

  const orgId = process.env.SHOPIFY_PARTNER_ORG_ID;
  const token = process.env.SHOPIFY_PARTNER_API_ACCESS_TOKEN;
  const appId = process.env.SHOPIFY_APP_GID;
  if (!orgId || !token || !appId) throw new Error("Shopify App Pricing credentials are not configured");

  let lastError: unknown;

  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      const response = await fetch(`https://partners.shopify.com/${encodeURIComponent(orgId)}/api/2026-07/graphql.json`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Shopify-Access-Token": token,
        },
        body: JSON.stringify({
          query: `query ($appId: ID!, $shopId: ID!) {
            activeSubscription(appId: $appId, shopId: $shopId) { billingPeriod }
          }`,
          variables: { appId, shopId },
        }),
        signal: AbortSignal.timeout(10_000),
      });

      if (RETRYABLE.has(response.status)) {
        lastError = new Error(`Partner API HTTP ${response.status}`);
        await wait(retryDelay(response, attempt));
        continue;
      }

      const body = await response.json() as {
        data?: { activeSubscription?: Subscription };
        errors?: Array<{ message?: string }>;
      };

      if (!response.ok || body.errors?.length) {
        const message = body.errors?.map((error) => error.message || "GraphQL error").join("; ");
        throw new Error(`Partner API request failed: ${message || response.status}`);
      }

      const value = body.data?.activeSubscription ?? null;
      if (value) cache.set(shopId, { value, expiresAt: Date.now() + 5 * 60_000 });
      return value;
    } catch (error) {
      lastError = error;
      if (attempt === 3) break;
      await wait(Math.min(500 * 2 ** attempt, 8_000));
    }
  }

  throw lastError instanceof Error ? lastError : new Error("Partner API request failed");
}
