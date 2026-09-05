import assert from "node:assert/strict";
import test from "node:test";
import {
  bulkOperationGidFromPayload,
  isReconciliationEnabled,
  productUpdatedAtQuery,
  reconciliationIntervalMs,
  reconciliationOverlapMs,
  reconciliationPollMs,
  reconciliationSchedulerPollMs,
  reconciliationWindow,
} from "./reconciliation-core.ts";

test("bulk finish payload accepts only Shopify BulkOperation GIDs", () => {
  assert.equal(
    bulkOperationGidFromPayload({
      admin_graphql_api_id: "gid://shopify/BulkOperation/720918",
    }),
    "gid://shopify/BulkOperation/720918",
  );
  assert.equal(
    bulkOperationGidFromPayload({ admin_graphql_api_id: "gid://shopify/Product/720918" }),
    null,
  );
  assert.equal(bulkOperationGidFromPayload({ admin_graphql_api_id: "bad" }), null);
});

test("reconciliation defaults on only in production", () => {
  assert.equal(isReconciliationEnabled({ NODE_ENV: "production" } as NodeJS.ProcessEnv), true);
  assert.equal(isReconciliationEnabled({ NODE_ENV: "development" } as NodeJS.ProcessEnv), false);
  assert.equal(isReconciliationEnabled({ RECONCILIATION_ENABLED: "true" } as NodeJS.ProcessEnv), true);
  assert.equal(isReconciliationEnabled({ NODE_ENV: "production", RECONCILIATION_ENABLED: "false" } as NodeJS.ProcessEnv), false);
});

test("reconciliation timing is bounded", () => {
  assert.equal(reconciliationIntervalMs("1"), 30 * 60_000);
  assert.equal(reconciliationIntervalMs("999999"), 7 * 24 * 60 * 60_000);
  assert.equal(reconciliationSchedulerPollMs("1"), 60_000);
  assert.equal(reconciliationOverlapMs("0"), 60_000);
  assert.equal(reconciliationPollMs("1"), 15_000);
});

test("reconciliation windows overlap and stop before now", () => {
  const last = new Date("2026-09-05T10:00:00.000Z");
  const now = new Date("2026-09-05T12:00:00.000Z");
  const { since, cutoff } = reconciliationWindow(last, now);
  assert.equal(since?.toISOString(), "2026-09-05T09:55:00.000Z");
  assert.equal(cutoff.toISOString(), "2026-09-05T11:59:00.000Z");
});

test("Shopify updated_at search query includes a bounded window", () => {
  const since = new Date("2026-09-05T09:55:00.000Z");
  const cutoff = new Date("2026-09-05T11:59:00.000Z");
  assert.equal(
    productUpdatedAtQuery(since, cutoff),
    "updated_at:>'2026-09-05T09:55:00.000Z' updated_at:<='2026-09-05T11:59:00.000Z'",
  );
  assert.equal(
    productUpdatedAtQuery(null, cutoff),
    "updated_at:<='2026-09-05T11:59:00.000Z'",
  );
});
