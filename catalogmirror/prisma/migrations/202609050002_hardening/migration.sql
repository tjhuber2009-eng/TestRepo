ALTER TABLE "AuditRun"
  ADD COLUMN "trigger" TEXT NOT NULL DEFAULT 'MANUAL',
  ADD COLUMN "warnings" INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN "catalogTruncated" BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN "durationMs" INTEGER;

ALTER TABLE "Incident"
  ADD COLUMN "occurrenceCount" INTEGER NOT NULL DEFAULT 1;

WITH ranked AS (
  SELECT
    "id",
    ROW_NUMBER() OVER (
      PARTITION BY "shop", "fingerprint"
      ORDER BY "lastSeenAt" DESC, "id" DESC
    ) AS rn
  FROM "Incident"
)
DELETE FROM "Incident"
WHERE "id" IN (SELECT "id" FROM ranked WHERE rn > 1);

DROP INDEX IF EXISTS "Incident_shop_fingerprint_idx";
CREATE UNIQUE INDEX "Incident_shop_fingerprint_key" ON "Incident"("shop", "fingerprint");

CREATE TABLE "WebhookReceipt" (
  "id" TEXT NOT NULL,
  "shop" TEXT NOT NULL,
  "topic" TEXT NOT NULL,
  "attempts" INTEGER NOT NULL DEFAULT 1,
  "receivedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "processedAt" TIMESTAMP(3),
  CONSTRAINT "WebhookReceipt_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "WebhookReceipt_receivedAt_idx" ON "WebhookReceipt"("receivedAt");
CREATE INDEX "WebhookReceipt_shop_receivedAt_idx" ON "WebhookReceipt"("shop", "receivedAt");

CREATE TABLE "ShopAuditState" (
  "shop" TEXT NOT NULL,
  "shopGid" TEXT,
  "pendingChanges" INTEGER NOT NULL DEFAULT 0,
  "lastWebhookTopic" TEXT,
  "lastWebhookAt" TIMESTAMP(3),
  "lastAuditAt" TIMESTAMP(3),
  "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "ShopAuditState_pkey" PRIMARY KEY ("shop")
);

CREATE TABLE "AuditLease" (
  "shop" TEXT NOT NULL,
  "owner" TEXT NOT NULL,
  "expiresAt" TIMESTAMP(3) NOT NULL,
  "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "AuditLease_pkey" PRIMARY KEY ("shop")
);
