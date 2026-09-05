import db from "../db.server";
import {
  autoAuditDebounceMs,
  inventoryItemGidFromPayload,
  productGidFromPayload,
  type AutoAuditResourceType,
} from "./auto-audit-core";

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

export async function syncPendingAuditCount(
  shop: string,
  metadata?: { topic?: string; webhookAt?: Date },
) {
  const pending = await db.auditTask.count({ where: { shop } });
  const webhookAt = metadata?.webhookAt ?? new Date();
  await db.shopAuditState.upsert({
    where: { shop },
    create: {
      shop,
      pendingChanges: pending,
      lastWebhookTopic: metadata?.topic,
      lastWebhookAt: metadata?.topic ? webhookAt : undefined,
    },
    update: {
      pendingChanges: pending,
      ...(metadata?.topic ? { lastWebhookTopic: metadata.topic, lastWebhookAt: webhookAt } : {}),
    },
  });
  return pending;
}

export async function enqueueAutoAuditTask(args: {
  shop: string;
  topic: string;
  resourceType: AutoAuditResourceType;
  resourceId: string;
}) {
  const now = new Date();
  const availableAt = new Date(now.getTime() + autoAuditDebounceMs());

  await db.auditTask.upsert({
    where: {
      shop_resourceType_resourceId: {
        shop: args.shop,
        resourceType: args.resourceType,
        resourceId: args.resourceId,
      },
    },
    create: {
      shop: args.shop,
      resourceType: args.resourceType,
      resourceId: args.resourceId,
      reason: args.topic,
      availableAt,
    },
    update: {
      reason: args.topic,
      generation: { increment: 1 },
      attempts: 0,
      availableAt,
      lastError: null,
    },
  });

  await syncPendingAuditCount(args.shop, { topic: args.topic, webhookAt: now });
}

export async function cancelAutoAuditTask(
  shop: string,
  resourceType: AutoAuditResourceType,
  resourceId: string,
) {
  await db.auditTask.deleteMany({ where: { shop, resourceType, resourceId } });
  await syncPendingAuditCount(shop);
}

export async function markShopChanged(shop: string, topic: string) {
  await enqueueAutoAuditTask({
    shop,
    topic,
    resourceType: "SHOP",
    resourceId: shop,
  });
}

export { inventoryItemGidFromPayload, productGidFromPayload };
