import crypto from "node:crypto";
import os from "node:os";
import { Prisma } from "@prisma/client";
import db from "../db.server";
import { unauthenticated } from "../shopify.server";
import {
  AuditInProgressError,
  getAuditMaxProducts,
  runCatalogAudit,
} from "./audit.server";
import {
  autoAuditBackoffMs,
  autoAuditPollMs,
  isAutoAuditEnabled,
  type AutoAuditResourceType,
} from "./auto-audit-core";
import { syncPendingAuditCount } from "./webhook.server";

type ClaimedTask = {
  id: string;
  shop: string;
  resourceType: string;
  resourceId: string;
  reason: string;
  generation: number;
  attempts: number;
  availableAt: Date;
  lockedBy: string | null;
  lockedUntil: Date | null;
  lastError: string | null;
  createdAt: Date;
  updatedAt: Date;
};

type InventoryItemQuery = {
  data?: {
    inventoryItem?: {
      variants?: {
        nodes?: Array<{ product?: { id?: string } | null }>;
      };
    } | null;
  };
  errors?: Array<{ message?: string }>;
};

type AdminContext = Awaited<ReturnType<typeof unauthenticated.admin>>;
type AdminClient = AdminContext["admin"];

declare global {
  var catalogMirrorAutoAuditWorkerStarted: boolean | undefined;
}

const WORKER_ID = os.hostname() + ":" + process.pid + ":" + crypto.randomUUID();
const TASK_LOCK_MS = 10 * 60_000;
const TASK_LOCK_HEARTBEAT_MS = 2 * 60_000;

function truncate(value: string, max = 1800) {
  return value.length > max ? value.slice(0, max) + "…" : value;
}

function sleep(ms: number) {
  return new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    timer.unref();
  });
}

function autoAuditProductLimit() {
  const configured = Number(process.env.AUTO_AUDIT_PRODUCT_LIMIT || getAuditMaxProducts());
  const value = Number.isFinite(configured) ? Math.trunc(configured) : getAuditMaxProducts();
  return Math.max(25, Math.min(value, getAuditMaxProducts(), 500));
}

async function claimTask(): Promise<ClaimedTask | null> {
  const now = new Date();
  const lockedUntil = new Date(now.getTime() + TASK_LOCK_MS);
  const rows = await db.$queryRaw<ClaimedTask[]>(Prisma.sql`
    WITH candidate AS (
      SELECT "id"
      FROM "AuditTask"
      WHERE "availableAt" <= ${now}
        AND ("lockedUntil" IS NULL OR "lockedUntil" < ${now})
      ORDER BY "availableAt" ASC, "createdAt" ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    UPDATE "AuditTask" AS task
    SET "lockedBy" = ${WORKER_ID},
        "lockedUntil" = ${lockedUntil},
        "attempts" = task."attempts" + 1,
        "updatedAt" = CURRENT_TIMESTAMP
    FROM candidate
    WHERE task."id" = candidate."id"
    RETURNING task.*
  `);

  return rows[0] ?? null;
}

async function productIdsForInventoryItem(
  admin: AdminClient,
  inventoryItemId: string,
) {
  const query = [
    "#graphql",
    "query CatalogMirrorInventoryItemProducts($id: ID!) {",
    "  inventoryItem(id: $id) {",
    "    variants(first: 100) {",
    "      nodes { product { id } }",
    "    }",
    "  }",
    "}",
  ].join("\n");

  const response = await admin.graphql(query, {
    variables: { id: inventoryItemId },
  });

  const body = (await response.json()) as InventoryItemQuery;
  if (!response.ok || body.errors?.length) {
    const details = body.errors?.map((error) => error.message || "GraphQL error").join("; ");
    throw new Error(
      "Unable to map inventory item to product: " + (details || "HTTP " + response.status),
    );
  }

  const ids = body.data?.inventoryItem?.variants?.nodes
    ?.map((variant) => variant.product?.id)
    .filter((id): id is string => Boolean(id && /^gid:\/\/shopify\/Product\/\d+$/.test(id))) ?? [];

  return Array.from(new Set(ids));
}

async function resolveTaskProducts(
  task: ClaimedTask,
  admin: AdminClient,
) {
  const type = task.resourceType as AutoAuditResourceType;
  if (type === "PRODUCT") return [task.resourceId];
  if (type === "INVENTORY_ITEM") return productIdsForInventoryItem(admin, task.resourceId);
  if (type === "SHOP") return null;
  throw new Error("Unsupported automatic audit resource type: " + task.resourceType);
}

function startTaskHeartbeat(task: ClaimedTask) {
  const timer = setInterval(() => {
    void db.auditTask.updateMany({
      where: { id: task.id, lockedBy: WORKER_ID },
      data: { lockedUntil: new Date(Date.now() + TASK_LOCK_MS) },
    }).catch((error) => {
      console.error("CatalogMirror could not renew automatic audit task lock", {
        taskId: task.id,
        shop: task.shop,
        error: error instanceof Error ? error.message : String(error),
      });
    });
  }, TASK_LOCK_HEARTBEAT_MS);
  timer.unref();
  return timer;
}

async function completeTask(task: ClaimedTask) {
  const deleted = await db.auditTask.deleteMany({
    where: {
      id: task.id,
      generation: task.generation,
      lockedBy: WORKER_ID,
    },
  });

  if (deleted.count === 0) {
    await db.auditTask.updateMany({
      where: { id: task.id, lockedBy: WORKER_ID },
      data: { lockedBy: null, lockedUntil: null },
    });
  }

  const now = new Date();
  await db.shopAuditState.upsert({
    where: { shop: task.shop },
    create: {
      shop: task.shop,
      lastAutoAuditAt: now,
      lastAutoAuditError: null,
    },
    update: {
      lastAutoAuditAt: now,
      lastAutoAuditError: null,
    },
  });
  await syncPendingAuditCount(task.shop);
}

async function failTask(task: ClaimedTask, error: unknown) {
  const message = truncate(error instanceof Error ? error.message : String(error));
  const auditBusy = error instanceof AuditInProgressError;
  const retryMs = auditBusy ? 15_000 : autoAuditBackoffMs(Math.max(0, task.attempts - 1));

  const updated = await db.auditTask.updateMany({
    where: {
      id: task.id,
      generation: task.generation,
      lockedBy: WORKER_ID,
    },
    data: {
      lockedBy: null,
      lockedUntil: null,
      availableAt: new Date(Date.now() + retryMs),
      lastError: auditBusy ? null : message,
    },
  });

  if (updated.count === 0) {
    await db.auditTask.updateMany({
      where: { id: task.id, lockedBy: WORKER_ID },
      data: { lockedBy: null, lockedUntil: null },
    });
  } else if (!auditBusy) {
    await db.shopAuditState.upsert({
      where: { shop: task.shop },
      create: { shop: task.shop, lastAutoAuditError: message },
      update: { lastAutoAuditError: message },
    });
  }

  await syncPendingAuditCount(task.shop);
}

async function processTask(task: ClaimedTask) {
  const heartbeat = startTaskHeartbeat(task);
  try {
    const { admin } = await unauthenticated.admin(task.shop);
    const productIds = await resolveTaskProducts(task, admin);

    if (productIds && productIds.length === 0) {
      await completeTask(task);
      return;
    }

    const type = task.resourceType as AutoAuditResourceType;
    await runCatalogAudit({
      admin,
      shop: task.shop,
      trigger: "AUTO_" + type,
      ...(productIds ? { productIds } : { limit: autoAuditProductLimit() }),
    });

    await completeTask(task);
  } catch (error) {
    await failTask(task, error);
    if (!(error instanceof AuditInProgressError)) {
      console.error("CatalogMirror automatic audit failed", {
        shop: task.shop,
        resourceType: task.resourceType,
        resourceId: task.resourceId,
        attempts: task.attempts,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  } finally {
    clearInterval(heartbeat);
  }
}

async function workerLoop() {
  while (true) {
    try {
      const task = await claimTask();
      if (!task) {
        await sleep(autoAuditPollMs());
        continue;
      }
      await processTask(task);
    } catch (error) {
      console.error(
        "CatalogMirror automatic audit worker loop error",
        error instanceof Error ? error.message : String(error),
      );
      await sleep(autoAuditPollMs());
    }
  }
}

export function startAutoAuditWorker() {
  if (!isAutoAuditEnabled()) return;
  if (globalThis.catalogMirrorAutoAuditWorkerStarted) return;
  globalThis.catalogMirrorAutoAuditWorkerStarted = true;

  void workerLoop().catch((error) => {
    globalThis.catalogMirrorAutoAuditWorkerStarted = false;
    console.error(
      "CatalogMirror automatic audit worker stopped unexpectedly",
      error instanceof Error ? error.message : String(error),
    );
  });
}
