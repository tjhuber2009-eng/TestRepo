import assert from "node:assert/strict";
import test from "node:test";
import {
  autoAuditBackoffMs,
  autoAuditDebounceMs,
  autoAuditPollMs,
  autoAuditTaskPriority,
  inventoryItemGidFromPayload,
  isAutoAuditEnabled,
  productGidFromPayload,
} from "./auto-audit-core.ts";

test("product webhook IDs normalize to Shopify GIDs", () => {
  assert.equal(productGidFromPayload({ id: 123 }), "gid://shopify/Product/123");
  assert.equal(
    productGidFromPayload({ admin_graphql_api_id: "gid://shopify/Product/456" }),
    "gid://shopify/Product/456",
  );
  assert.equal(productGidFromPayload({ id: "bad" }), null);
});

test("inventory level webhook GID strings avoid unsafe integer rounding", () => {
  assert.equal(
    inventoryItemGidFromPayload({
      inventory_item_id: 271878346596884015,
      admin_graphql_api_id: "gid://shopify/InventoryLevel/24826418?inventory_item_id=271878346596884015",
    }),
    "gid://shopify/InventoryItem/271878346596884015",
  );
  assert.equal(
    inventoryItemGidFromPayload({ inventory_item_id: 271878346596884015 }),
    null,
  );
  assert.equal(
    inventoryItemGidFromPayload({ inventory_item_id: "271878346596884015" }),
    "gid://shopify/InventoryItem/271878346596884015",
  );
});

test("automatic audit timing controls are bounded", () => {
  assert.equal(autoAuditDebounceMs("1"), 5000);
  assert.equal(autoAuditDebounceMs("999"), 300000);
  assert.equal(autoAuditPollMs("1"), 1000);
  assert.equal(autoAuditPollMs("999999"), 60000);
  assert.equal(autoAuditBackoffMs(0), 15000);
  assert.equal(autoAuditBackoffMs(20), 900000);
});

test("queue priority keeps live webhooks ahead of reconciliation", () => {
  assert.equal(autoAuditTaskPriority("PRODUCT"), 100);
  assert.equal(autoAuditTaskPriority("INVENTORY_ITEM"), 100);
  assert.equal(autoAuditTaskPriority("SHOP"), 80);
  assert.equal(autoAuditTaskPriority("RECONCILE_PRODUCT"), 10);
  assert.equal(autoAuditTaskPriority("RECONCILE"), 5);
});

test("automatic audits default on only in production", () => {
  assert.equal(isAutoAuditEnabled({ NODE_ENV: "production" } as NodeJS.ProcessEnv), true);
  assert.equal(isAutoAuditEnabled({ NODE_ENV: "development" } as NodeJS.ProcessEnv), false);
  assert.equal(isAutoAuditEnabled({ NODE_ENV: "development", AUTO_AUDIT_ENABLED: "true" } as NodeJS.ProcessEnv), true);
  assert.equal(isAutoAuditEnabled({ NODE_ENV: "production", AUTO_AUDIT_ENABLED: "false" } as NodeJS.ProcessEnv), false);
});
