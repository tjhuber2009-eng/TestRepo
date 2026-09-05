ALTER TABLE "ShopAuditState"
  ADD COLUMN "lastReconciliationAt" TIMESTAMP(3),
  ADD COLUMN "lastReconciliationError" TEXT,
  ADD COLUMN "reconciliationBulkOperationId" TEXT,
  ADD COLUMN "reconciliationCutoffAt" TIMESTAMP(3),
  ADD COLUMN "reconciliationDiscovered" INTEGER NOT NULL DEFAULT 0;

ALTER TABLE "AuditTask"
  ADD COLUMN "priority" INTEGER NOT NULL DEFAULT 50;

DROP INDEX IF EXISTS "AuditTask_availableAt_lockedUntil_idx";
DROP INDEX IF EXISTS "AuditTask_shop_availableAt_idx";

CREATE INDEX "AuditTask_priority_availableAt_lockedUntil_idx"
  ON "AuditTask"("priority", "availableAt", "lockedUntil");
CREATE INDEX "AuditTask_shop_priority_availableAt_idx"
  ON "AuditTask"("shop", "priority", "availableAt");
