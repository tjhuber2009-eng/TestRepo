import db from "../db.server.ts";
import {
  autoAuditDebounceMs,
  autoAuditTaskPriority,
  type AutoAuditResourceType,
} from "./auto-audit-core.ts";

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
  recordWebhook?: boolean;
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
      priority: autoAuditTaskPriority(args.resourceType),
      availableAt,
    },
    update: {
      reason: args.topic,
      priority: autoAuditTaskPriority(args.resourceType),
      generation: { increment: 1 },
      attempts: 0,
      availableAt,
      lastError: null,
    },
  });

  if (args.recordWebhook === false) {
    await syncPendingAuditCount(args.shop);
  } else {
    await syncPendingAuditCount(args.shop, { topic: args.topic, webhookAt: now });
  }
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

export async function enqueueReconciliationProducts(shop: string, productIds: string[]) {
  const uniqueIds = Array.from(new Set(
    productIds.filter((id) => /^gid:\/\/shopify\/Product\/\d+$/.test(id)),
  ));
  if (!uniqueIds.length) return 0;

  const availableAt = new Date();
  for (let offset = 0; offset < uniqueIds.length; offset += 100) {
    const chunk = uniqueIds.slice(offset, offset + 100);
    await db.$transaction(
      chunk.map((resourceId) =>
        db.auditTask.upsert({
          where: {
            shop_resourceType_resourceId: {
              shop,
              resourceType: "RECONCILE_PRODUCT",
              resourceId,
            },
          },
          create: {
            shop,
            resourceType: "RECONCILE_PRODUCT",
            resourceId,
            reason: "PERIODIC_RECONCILIATION",
            priority: autoAuditTaskPriority("RECONCILE_PRODUCT"),
            availableAt,
          },
          update: {
            reason: "PERIODIC_RECONCILIATION",
            generation: { increment: 1 },
            attempts: 0,
            availableAt,
            lastError: null,
          },
        }),
      ),
    );
  }

  await syncPendingAuditCount(shop);
  return uniqueIds.length;
}
