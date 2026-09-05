import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();
const tables = [
  "Session",
  "AuditRun",
  "Incident",
  "WebhookReceipt",
  "ShopAuditState",
  "AuditLease",
  "AuditTask",
];

try {
  const columns = await prisma.$queryRawUnsafe(
    `SELECT table_name, column_name, data_type, is_nullable, column_default
     FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = ANY(ARRAY[
         'Session','AuditRun','Incident','WebhookReceipt',
         'ShopAuditState','AuditLease','AuditTask','_prisma_migrations'
       ])
     ORDER BY table_name, ordinal_position`,
  );

  const indexes = await prisma.$queryRawUnsafe(
    `SELECT tablename, indexname, indexdef
     FROM pg_indexes
     WHERE schemaname = 'public'
       AND tablename = ANY(ARRAY[
         'Session','AuditRun','Incident','WebhookReceipt',
         'ShopAuditState','AuditLease','AuditTask'
       ])
     ORDER BY tablename, indexname`,
  );

  const counts = {};
  for (const table of tables) {
    const exists = await prisma.$queryRawUnsafe(
      `SELECT to_regclass('public."${table}"')::text AS r`,
    );
    counts[table] = exists[0]?.r
      ? Number((await prisma.$queryRawUnsafe(
          `SELECT COUNT(*)::bigint AS c FROM "${table}"`,
        ))[0].c)
      : null;
  }

  let migrations = [];
  const migrationTable = await prisma.$queryRawUnsafe(
    "SELECT to_regclass('public._prisma_migrations')::text AS r",
  );
  if (migrationTable[0]?.r) {
    migrations = await prisma.$queryRawUnsafe(
      `SELECT migration_name, started_at, finished_at, rolled_back_at,
              applied_steps_count, logs
       FROM _prisma_migrations
       ORDER BY started_at`,
    );
  }

  console.log(
    "CATALOGMIRROR_DB_INSPECT=" +
      JSON.stringify(
        { columns, indexes, counts, migrations },
        (_key, value) => (typeof value === "bigint" ? value.toString() : value),
      ),
  );
} finally {
  await prisma.$disconnect();
}
