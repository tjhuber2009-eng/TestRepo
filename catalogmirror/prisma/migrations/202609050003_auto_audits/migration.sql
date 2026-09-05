ALTER TABLE "ShopAuditState"
  ADD COLUMN "lastAutoAuditAt" TIMESTAMP(3),
  ADD COLUMN "lastAutoAuditError" TEXT;

CREATE TABLE "AuditTask" (
  "id" TEXT NOT NULL,
  "shop" TEXT NOT NULL,
  "resourceType" TEXT NOT NULL,
  "resourceId" TEXT NOT NULL,
  "reason" TEXT NOT NULL,
  "generation" INTEGER NOT NULL DEFAULT 1,
  "attempts" INTEGER NOT NULL DEFAULT 0,
  "availableAt" TIMESTAMP(3) NOT NULL,
  "lockedBy" TEXT,
  "lockedUntil" TIMESTAMP(3),
  "lastError" TEXT,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "AuditTask_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "AuditTask_shop_resourceType_resourceId_key"
  ON "AuditTask"("shop", "resourceType", "resourceId");
CREATE INDEX "AuditTask_availableAt_lockedUntil_idx"
  ON "AuditTask"("availableAt", "lockedUntil");
CREATE INDEX "AuditTask_shop_availableAt_idx"
  ON "AuditTask"("shop", "availableAt");
