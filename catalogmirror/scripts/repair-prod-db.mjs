import { execFileSync } from "node:child_process";
import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();
const FAILED = "202609050001_init";
const LEGACY_SESSION = "LegacySession_20260905";
const LEGACY_INCIDENT = "LegacyIncident_20260905";

function prismaCli(args) {
  execFileSync("npx", ["prisma", ...args], {
    stdio: "inherit",
    env: process.env,
  });
}

async function exists(name) {
  const rows = await prisma.$queryRawUnsafe(
    `SELECT to_regclass('public."${name}"')::text AS r`,
  );
  return Boolean(rows[0]?.r);
}

async function count(name) {
  return Number(
    (await prisma.$queryRawUnsafe(
      `SELECT COUNT(*)::bigint AS c FROM "${name}"`,
    ))[0].c,
  );
}

async function hasColumn(table, column) {
  const rows = await prisma.$queryRawUnsafe(
    `SELECT 1
       FROM information_schema.columns
      WHERE table_schema='public'
        AND table_name=$1
        AND column_name=$2
      LIMIT 1`,
    table,
    column,
  );
  return rows.length === 1;
}

try {
  if (!(await exists("Session")) || !(await exists("Incident"))) {
    throw new Error("Expected legacy Session and Incident tables are missing");
  }
  if (await exists("AuditRun")) {
    throw new Error("AuditRun unexpectedly exists; refusing legacy repair");
  }
  if (await exists(LEGACY_SESSION) || await exists(LEGACY_INCIDENT)) {
    throw new Error("Legacy archive tables already exist; refusing ambiguous repair");
  }

  const sessionRows = await count("Session");
  const incidentRows = await count("Incident");
  if (sessionRows !== 0 || incidentRows !== 0) {
    throw new Error(
      `Legacy tables are not empty (Session=${sessionRows}, Incident=${incidentRows}); refusing repair`,
    );
  }
  if (!(await hasColumn("Incident", "key")) || (await hasColumn("Incident", "id"))) {
    throw new Error("Incident table is not the expected legacy schema");
  }

  const failed = await prisma.$queryRawUnsafe(
    `SELECT migration_name, finished_at, rolled_back_at
       FROM _prisma_migrations
      WHERE migration_name=$1
      ORDER BY started_at DESC
      LIMIT 1`,
    FAILED,
  );
  if (!failed.length || failed[0].finished_at || failed[0].rolled_back_at) {
    throw new Error("Expected unresolved failed migration record was not found");
  }

  console.log("CATALOGMIRROR_REPAIR: guards passed; resolving failed migration");
  await prisma.$disconnect();
  prismaCli(["migrate", "resolve", "--rolled-back", FAILED]);

  const db = new PrismaClient();
  try {
    await db.$transaction(async (tx) => {
      await tx.$executeRawUnsafe(
        `ALTER TABLE "Session" RENAME TO "${LEGACY_SESSION}"`,
      );
      await tx.$executeRawUnsafe(
        `ALTER INDEX "Session_pkey" RENAME TO "LegacySession_20260905_pkey"`,
      );
      await tx.$executeRawUnsafe(
        `ALTER TABLE "Incident" RENAME TO "${LEGACY_INCIDENT}"`,
      );
      await tx.$executeRawUnsafe(
        `ALTER INDEX "Incident_pkey" RENAME TO "LegacyIncident_20260905_pkey"`,
      );
    });
  } finally {
    await db.$disconnect();
  }

  console.log("CATALOGMIRROR_REPAIR: empty legacy tables archived; deploying current migrations");
  prismaCli(["migrate", "deploy"]);

  const verify = new PrismaClient();
  try {
    const requiredTables = [
      "Session",
      "AuditRun",
      "Incident",
      "WebhookReceipt",
      "ShopAuditState",
      "AuditLease",
      "AuditTask",
    ];
    for (const table of requiredTables) {
      const rows = await verify.$queryRawUnsafe(
        `SELECT to_regclass('public."${table}"')::text AS r`,
      );
      if (!rows[0]?.r) throw new Error(`Missing migrated table: ${table}`);
    }

    const expectedColumns = [
      ["AuditRun", "trigger"],
      ["AuditRun", "warnings"],
      ["Incident", "occurrenceCount"],
      ["ShopAuditState", "lastAutoAuditAt"],
      ["ShopAuditState", "lastReconciliationAt"],
      ["AuditTask", "priority"],
    ];
    for (const [table, column] of expectedColumns) {
      const rows = await verify.$queryRawUnsafe(
        `SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name=$1 AND column_name=$2 LIMIT 1`,
        table,
        column,
      );
      if (!rows.length) throw new Error(`Missing migrated column: ${table}.${column}`);
    }

    console.log("CATALOGMIRROR_REPAIR_SUCCESS");
  } finally {
    await verify.$disconnect();
  }
} catch (error) {
  console.error("CATALOGMIRROR_REPAIR_FAILED", error);
  process.exit(1);
}
