import db from "../db.server";

type WebhookContext = {
  webhookId: string;
  shop: string;
  topic: string;
};

const PROCESSING_STALE_MS = 5 * 60_000;
const RECEIPT_TTL_DAYS = Math.max(7, Math.min(Number(process.env.WEBHOOK_RECEIPT_TTL_DAYS || 30), 90));

function isUniqueConstraint(error: unknown) {
  return Boolean(error && typeof error === "object" && "code" in error && (error as { code?: string }).code === "P2002");
}

async function beginWebhook({ webhookId, shop, topic }: WebhookContext) {
  try {
    await db.webhookReceipt.create({ data: { id: webhookId, shop, topic } });
    return true;
  } catch (error) {
    if (!isUniqueConstraint(error)) throw error;

    const staleBefore = new Date(Date.now() - PROCESSING_STALE_MS);
    const reclaimed = await db.webhookReceipt.updateMany({
      where: { id: webhookId, processedAt: null, receivedAt: { lt: staleBefore } },
      data: { receivedAt: new Date(), attempts: { increment: 1 } },
    });
    return reclaimed.count === 1;
  }
}

async function pruneReceipts() {
  const cutoff = new Date(Date.now() - RECEIPT_TTL_DAYS * 24 * 60 * 60_000);
  await db.webhookReceipt.deleteMany({ where: { receivedAt: { lt: cutoff } } });
}

export async function processWebhookDelivery(
  context: WebhookContext,
  work: () => Promise<void>,
) {
  const shouldProcess = await beginWebhook(context);
  if (!shouldProcess) return false;

  try {
    await work();
    await db.webhookReceipt.updateMany({
      where: { id: context.webhookId },
      data: { processedAt: new Date() },
    });
    try {
      await pruneReceipts();
    } catch (pruneError) {
      console.warn("CatalogMirror webhook receipt pruning failed", {
        webhookId: context.webhookId,
        error: pruneError instanceof Error ? pruneError.message : String(pruneError),
      });
    }
    return true;
  } catch (error) {
    try {
      await db.webhookReceipt.deleteMany({
        where: { id: context.webhookId, processedAt: null },
      });
    } catch (cleanupError) {
      console.error("CatalogMirror could not release failed webhook receipt", {
        webhookId: context.webhookId,
        error: cleanupError instanceof Error ? cleanupError.message : String(cleanupError),
      });
    }
    throw error;
  }
}

export async function markShopChanged(shop: string, topic: string) {
  const now = new Date();
  await db.shopAuditState.upsert({
    where: { shop },
    create: {
      shop,
      pendingChanges: 1,
      lastWebhookTopic: topic,
      lastWebhookAt: now,
    },
    update: {
      pendingChanges: { increment: 1 },
      lastWebhookTopic: topic,
      lastWebhookAt: now,
    },
  });
}

export function productGidFromPayload(payload: Record<string, unknown>) {
  const adminGid = payload.admin_graphql_api_id;
  if (typeof adminGid === "string" && adminGid.startsWith("gid://shopify/Product/")) return adminGid;

  const id = payload.id;
  if (typeof id === "number" || (typeof id === "string" && /^\d+$/.test(id))) {
    return `gid://shopify/Product/${id}`;
  }
  return null;
}
