type Subscription = { billingPeriod: string } | null;

const cache = new Map<string, { value: Subscription; expiresAt: number }>();

export async function fetchActiveSubscription(shopId: string): Promise<Subscription> {
  const cached = cache.get(shopId);
  if (cached && cached.expiresAt > Date.now()) return cached.value;

  const orgId = process.env.SHOPIFY_PARTNER_ORG_ID;
  const token = process.env.SHOPIFY_PARTNER_API_ACCESS_TOKEN;
  const appId = process.env.SHOPIFY_APP_GID;
  if (!orgId || !token || !appId) throw new Error("Shopify App Pricing credentials are not configured");

  const res = await fetch(`https://partners.shopify.com/${orgId}/api/2026-07/graphql.json`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Shopify-Access-Token": token },
    body: JSON.stringify({
      query: `query ($appId: ID!, $shopId: ID!) { activeSubscription(appId: $appId, shopId: $shopId) { billingPeriod } }`,
      variables: { appId, shopId },
    }),
    signal: AbortSignal.timeout(10000),
  });
  const body = await res.json() as { data?: { activeSubscription?: Subscription }; errors?: unknown };
  if (!res.ok || body.errors) throw new Error(`Partner API request failed: ${JSON.stringify(body.errors ?? res.status)}`);

  const value = body.data?.activeSubscription ?? null;
  if (value) cache.set(shopId, { value, expiresAt: Date.now() + 5 * 60_000 });
  return value;
}
