import { lookup } from "node:dns/promises";
import db from "../db.server";
import { isPrivateAddress } from "./audit-core";
import {
  enqueueAutoAuditTask,
  enqueueReconciliationProducts,
} from "./auto-audit-queue.server";
import {
  isReconciliationEnabled,
  productUpdatedAtQuery,
  reconciliationIntervalMs,
  reconciliationPollMs,
  reconciliationSchedulerPollMs,
  reconciliationWindow,
} from "./reconciliation-core";

type AdminClient = {
  graphql: (query: string, options?: { variables?: Record<string, unknown> }) => Promise<Response>;
};

type StartEnvelope = {
  data?: {
    bulkOperationRunQuery?: {
      bulkOperation?: { id: string; status: string } | null;
      userErrors?: Array<{ field?: string[] | null; message?: string }>;
    };
  };
  errors?: Array<{ message?: string }>;
};

type StatusEnvelope = {
  data?: {
    bulkOperation?: {
      id: string;
      status: string;
      errorCode?: string | null;
      objectCount?: string | number | null;
      rootObjectCount?: string | number | null;
      url?: string | null;
      partialDataUrl?: string | null;
    } | null;
  };
  errors?: Array<{ message?: string }>;
};

export type ReconciliationTaskResult =
  | { kind: "defer"; delayMs: number }
  | { kind: "complete"; discovered: number };

declare global {
  var catalogMirrorReconciliationSchedulerStarted: boolean | undefined;
}

function truncate(value: string, max = 1800) {
  return value.length > max ? value.slice(0, max) + "…" : value;
}

function maxReconciliationBytes() {
  const mb = Number(process.env.RECONCILIATION_MAX_RESULT_MB || 256);
  const bounded = Number.isFinite(mb) ? Math.max(10, Math.min(Math.trunc(mb), 2048)) : 256;
  return bounded * 1024 * 1024;
}

function maxReconciliationProducts() {
  const count = Number(process.env.RECONCILIATION_MAX_PRODUCTS || 500000);
  return Number.isFinite(count)
    ? Math.max(1000, Math.min(Math.trunc(count), 2_000_000))
    : 500000;
}

async function adminGraphql<T>(
  admin: AdminClient,
  query: string,
  variables: Record<string, unknown>,
): Promise<T> {
  const response = await admin.graphql(query, { variables });
  const body = (await response.json()) as T & { errors?: Array<{ message?: string }> };
  if (!response.ok) throw new Error("Shopify Admin API HTTP " + response.status);
  if (body.errors?.length) {
    throw new Error(truncate(body.errors.map((error) => error.message || "GraphQL error").join("; ")));
  }
  return body;
}

function buildBulkProductQuery(since: Date | null, cutoff: Date) {
  const search = productUpdatedAtQuery(since, cutoff);
  return [
    "{",
    "  products(query: " + JSON.stringify(search) + ", sortKey: UPDATED_AT) {",
    "    edges {",
    "      node { id }",
    "    }",
    "  }",
    "}",
  ].join("\n");
}

async function startBulkReconciliation(
  admin: AdminClient,
  shop: string,
  since: Date | null,
  cutoff: Date,
) {
  const mutation = [
    "#graphql",
    "mutation CatalogMirrorStartReconciliation($query: String!) {",
    "  bulkOperationRunQuery(query: $query) {",
    "    bulkOperation { id status }",
    "    userErrors { field message }",
    "  }",
    "}",
  ].join("\n");

  const envelope = await adminGraphql<StartEnvelope>(admin, mutation, {
    query: buildBulkProductQuery(since, cutoff),
  });
  const payload = envelope.data?.bulkOperationRunQuery;
  const userErrors = payload?.userErrors ?? [];
  if (userErrors.length) {
    throw new Error(
      truncate(userErrors.map((error) => error.message || "Bulk operation error").join("; ")),
    );
  }
  const operation = payload?.bulkOperation;
  if (!operation?.id) throw new Error("Shopify did not return a bulk operation ID");

  await db.shopAuditState.upsert({
    where: { shop },
    create: {
      shop,
      reconciliationBulkOperationId: operation.id,
      reconciliationCutoffAt: cutoff,
      reconciliationDiscovered: 0,
      lastReconciliationError: null,
    },
    update: {
      reconciliationBulkOperationId: operation.id,
      reconciliationCutoffAt: cutoff,
      reconciliationDiscovered: 0,
      lastReconciliationError: null,
    },
  });

  return operation.id;
}

async function getBulkOperation(admin: AdminClient, operationId: string) {
  const query = [
    "#graphql",
    "query CatalogMirrorReconciliationStatus($id: ID!) {",
    "  bulkOperation(id: $id) {",
    "    id",
    "    status",
    "    errorCode",
    "    objectCount",
    "    rootObjectCount",
    "    url",
    "    partialDataUrl",
    "  }",
    "}",
  ].join("\n");

  const envelope = await adminGraphql<StatusEnvelope>(admin, query, { id: operationId });
  return envelope.data?.bulkOperation ?? null;
}

async function assertPublicHostname(hostname: string) {
  const records = await lookup(hostname, { all: true, verbatim: true });
  if (!records.length || records.some((record) => isPrivateAddress(record.address))) {
    throw new Error("Bulk result URL resolved to a private or disallowed network address");
  }
}

async function fetchBulkResult(urlValue: string) {
  let current = new URL(urlValue);
  if (current.protocol !== "https:") throw new Error("Bulk result URL must use HTTPS");

  for (let redirect = 0; redirect <= 3; redirect += 1) {
    await assertPublicHostname(current.hostname);
    const response = await fetch(current, {
      headers: {
        accept: "application/jsonl, application/x-ndjson, text/plain",
        "user-agent": "CatalogMirror/1.2 (+catalog-integrity-monitor)",
      },
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });

    if (response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location");
      if (!location || redirect === 3) throw new Error("Bulk result redirect could not be followed safely");
      current = new URL(location, current);
      if (current.protocol !== "https:") throw new Error("Bulk result redirect must use HTTPS");
      continue;
    }

    if (!response.ok) throw new Error("Bulk result download returned HTTP " + response.status);
    return response;
  }

  throw new Error("Bulk result redirect limit exceeded");
}

async function ingestBulkProductIds(shop: string, resultUrl: string) {
  const response = await fetchBulkResult(resultUrl);
  if (!response.body) throw new Error("Bulk result response had no body");

  const declared = Number(response.headers.get("content-length") || 0);
  const maxBytes = maxReconciliationBytes();
  if (declared > maxBytes) {
    throw new Error("Bulk result exceeds RECONCILIATION_MAX_RESULT_MB safety limit");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let bytes = 0;
  let discovered = 0;
  let batch: string[] = [];

  async function flushBatch() {
    if (!batch.length) return;
    await enqueueReconciliationProducts(shop, batch);
    batch = [];
  }

  async function handleLine(line: string) {
    const trimmed = line.trim();
    if (!trimmed) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      throw new Error("Bulk reconciliation returned invalid JSONL");
    }

    const id =
      parsed && typeof parsed === "object" && "id" in parsed
        ? (parsed as { id?: unknown }).id
        : undefined;
    if (typeof id !== "string" || !/^gid:\/\/shopify\/Product\/\d+$/.test(id)) return;

    discovered += 1;
    if (discovered > maxReconciliationProducts()) {
      throw new Error("Bulk reconciliation exceeds RECONCILIATION_MAX_PRODUCTS safety limit");
    }
    batch.push(id);
    if (batch.length >= 100) await flushBatch();
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    bytes += value.byteLength;
    if (bytes > maxBytes) {
      await reader.cancel();
      throw new Error("Bulk reconciliation exceeded RECONCILIATION_MAX_RESULT_MB safety limit");
    }

    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      await handleLine(line);
      newline = buffer.indexOf("\n");
    }
  }

  buffer += decoder.decode();
  if (buffer.trim()) await handleLine(buffer);
  await flushBatch();
  return discovered;
}

async function markReconciliationFailure(shop: string, message: string) {
  await db.shopAuditState.upsert({
    where: { shop },
    create: {
      shop,
      lastReconciliationError: truncate(message),
      reconciliationBulkOperationId: null,
      reconciliationCutoffAt: null,
    },
    update: {
      lastReconciliationError: truncate(message),
      reconciliationBulkOperationId: null,
      reconciliationCutoffAt: null,
    },
  });
}

export async function processReconciliationTask(
  admin: AdminClient,
  shop: string,
): Promise<ReconciliationTaskResult> {
  const state = await db.shopAuditState.findUnique({ where: { shop } });

  if (!state?.reconciliationBulkOperationId) {
    const { since, cutoff } = reconciliationWindow(state?.lastReconciliationAt ?? null);
    await startBulkReconciliation(admin, shop, since, cutoff);
    return { kind: "defer", delayMs: reconciliationPollMs() };
  }

  const operation = await getBulkOperation(admin, state.reconciliationBulkOperationId);
  if (!operation) {
    await markReconciliationFailure(shop, "Shopify bulk reconciliation operation was not found");
    throw new Error("Shopify bulk reconciliation operation was not found");
  }

  if (["CREATED", "RUNNING", "CANCELING"].includes(operation.status)) {
    return { kind: "defer", delayMs: reconciliationPollMs() };
  }

  if (operation.status !== "COMPLETED") {
    const message =
      "Shopify bulk reconciliation ended with status " +
      operation.status +
      (operation.errorCode ? " (" + operation.errorCode + ")" : "");
    await markReconciliationFailure(shop, message);
    throw new Error(message);
  }

  const cutoff = state.reconciliationCutoffAt;
  if (!cutoff) {
    await markReconciliationFailure(shop, "Reconciliation cutoff timestamp was missing");
    throw new Error("Reconciliation cutoff timestamp was missing");
  }

  const discovered = operation.url ? await ingestBulkProductIds(shop, operation.url) : 0;
  await db.shopAuditState.update({
    where: { shop },
    data: {
      lastReconciliationAt: cutoff,
      lastReconciliationError: null,
      reconciliationBulkOperationId: null,
      reconciliationCutoffAt: null,
      reconciliationDiscovered: discovered,
    },
  });

  return { kind: "complete", discovered };
}

async function schedulerTick() {
  const shops = await db.session.findMany({
    where: { isOnline: false },
    distinct: ["shop"],
    select: { shop: true },
  });
  if (!shops.length) return;

  const now = Date.now();
  for (const { shop } of shops) {
    const [state, existing] = await Promise.all([
      db.shopAuditState.findUnique({ where: { shop } }),
      db.auditTask.findUnique({
        where: {
          shop_resourceType_resourceId: {
            shop,
            resourceType: "RECONCILE",
            resourceId: shop,
          },
        },
        select: { id: true },
      }),
    ]);

    if (existing) continue;
    const due =
      Boolean(state?.reconciliationBulkOperationId) ||
      !state?.lastReconciliationAt ||
      now - state.lastReconciliationAt.getTime() >= reconciliationIntervalMs();

    if (!due) continue;
    await enqueueAutoAuditTask({
      shop,
      topic: "PERIODIC_RECONCILIATION",
      resourceType: "RECONCILE",
      resourceId: shop,
    });
  }
}

function scheduleNextTick() {
  const timer = setTimeout(() => {
    void schedulerTick()
      .catch((error) => {
        console.error(
          "CatalogMirror reconciliation scheduler failed",
          error instanceof Error ? error.message : String(error),
        );
      })
      .finally(scheduleNextTick);
  }, reconciliationSchedulerPollMs());
  timer.unref();
}

export function startReconciliationScheduler() {
  if (!isReconciliationEnabled()) return;
  if (globalThis.catalogMirrorReconciliationSchedulerStarted) return;
  globalThis.catalogMirrorReconciliationSchedulerStarted = true;

  void schedulerTick()
    .catch((error) => {
      console.error(
        "CatalogMirror reconciliation scheduler startup failed",
        error instanceof Error ? error.message : String(error),
      );
    })
    .finally(scheduleNextTick);
}
