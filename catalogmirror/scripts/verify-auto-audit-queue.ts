import assert from "node:assert/strict";
import db from "../app/db.server.ts";
import { enqueueAutoAuditTask, syncPendingAuditCount } from "../app/lib/webhook.server.ts";

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

  assert.equal(await db.auditTask.count({ where: { shop } }), 2);
  const state = await db.shopAuditState.findUnique({ where: { shop } });
  assert.equal(state?.pendingChanges, 2);
  assert.equal(state?.lastWebhookTopic, "INVENTORY_LEVELS_UPDATE");

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
