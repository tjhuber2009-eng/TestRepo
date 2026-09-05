import db from "../db.server";
import {
  inventoryItemGidFromPayload,
  productGidFromPayload,
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

export {
  cancelAutoAuditTask,
  enqueueAutoAuditTask,
  markShopChanged,
  syncPendingAuditCount,
} from "./auto-audit-queue.server";


export { inventoryItemGidFromPayload, productGidFromPayload };
