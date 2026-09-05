import assert from "node:assert/strict";
import db from "../app/db.server.ts";
import {
  enqueueAutoAuditTask,
  syncPendingAuditCount,
  wakeReconciliationTask,
} from "../app/lib/auto-audit-queue.server.ts";

const shop = "catalogmirror-ci-queue.myshopify.com";

async function main() {
  process.env.AUTO_AUDIT_DEBOUNCE_SECONDS = "5";

  await db.auditTask.deleteMany({ where: { shop } });
  await db.shopAuditState.deleteMany({ where: { shop } });

  await enqueueAutoAuditTask({
    shop,
    topic: "PRODUCTS_UPDATE",
    resourceType: "PRODUCT",
    resourceId: "gid://shopify/Product/123",
  });

  let tasks = await db.auditTask.findMany({ where: { shop } });
  assert.equal(tasks.length, 1);
  assert.equal(tasks[0].generation, 1);
  assert.equal(tasks[0].attempts, 0);
  assert.equal(tasks[0].priority, 100);

  await enqueueAutoAuditTask({
    shop,
    topic: "PRODUCTS_UPDATE",
    resourceType: "PRODUCT",
    resourceId: "gid://shopify/Product/123",
  });

  tasks = await db.auditTask.findMany({ where: { shop } });
  assert.equal(tasks.length, 1, "duplicate resource webhooks must coalesce");
  assert.equal(tasks[0].generation, 2, "coalesced webhooks must advance generation");

  await enqueueAutoAuditTask({
    shop,
    topic: "INVENTORY_LEVELS_UPDATE",
    resourceType: "INVENTORY_ITEM",
    resourceId: "gid://shopify/InventoryItem/456",
  });

  await enqueueAutoAuditTask({
    shop,
    topic: "PERIODIC_RECONCILIATION",
    resourceType: "RECONCILE",
    resourceId: shop,
    recordWebhook: false,
  });

  assert.equal(await db.auditTask.count({ where: { shop } }), 3);
  const reconcile = await db.auditTask.findUnique({
    where: {
      shop_resourceType_resourceId: {
        shop,
        resourceType: "RECONCILE",
        resourceId: shop,
      },
    },
  });
  assert.equal(reconcile?.priority, 5);
  let state = await db.shopAuditState.findUnique({ where: { shop } });
  assert.equal(state?.pendingChanges, 3);
  assert.equal(state?.lastWebhookTopic, "INVENTORY_LEVELS_UPDATE");

  const operationId = "gid://shopify/BulkOperation/999";
  await db.shopAuditState.update({
    where: { shop },
    data: { reconciliationBulkOperationId: operationId },
  });

  const reconcileBefore = await db.auditTask.findUnique({
    where: {
      shop_resourceType_resourceId: {
        shop,
        resourceType: "RECONCILE",
        resourceId: shop,
      },
    },
  });
  assert.ok(reconcileBefore);

  assert.equal(
    await wakeReconciliationTask(shop, "gid://shopify/BulkOperation/123"),
    false,
    "unrelated bulk operations must not wake CatalogMirror reconciliation",
  );

  assert.equal(
    await wakeReconciliationTask(shop, operationId),
    true,
    "matching bulk operation should wake reconciliation",
  );
  const reconcileAfter = await db.auditTask.findUnique({
    where: {
      shop_resourceType_resourceId: {
        shop,
        resourceType: "RECONCILE",
        resourceId: shop,
      },
    },
  });
  assert.ok(reconcileAfter);
  assert.ok(reconcileAfter.availableAt <= new Date(Date.now() + 1000));

  state = await db.shopAuditState.findUnique({ where: { shop } });
  assert.equal(
    state?.lastWebhookTopic,
    "INVENTORY_LEVELS_UPDATE",
    "bulk-finish accelerator must not overwrite last catalog webhook metadata",
  );

  await db.auditTask.deleteMany({ where: { shop } });
  const pending = await syncPendingAuditCount(shop);
  assert.equal(pending, 0);
  assert.equal(
    (await db.shopAuditState.findUnique({ where: { shop } }))?.pendingChanges,
    0,
  );
}

try {
  await main();
} finally {
  await db.auditTask.deleteMany({ where: { shop } });
  await db.shopAuditState.deleteMany({ where: { shop } });
  await db.$disconnect();
}
