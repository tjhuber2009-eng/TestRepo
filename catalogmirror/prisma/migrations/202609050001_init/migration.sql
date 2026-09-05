CREATE TABLE "Session" (
    "id" TEXT NOT NULL,
    "shop" TEXT NOT NULL,
    "state" TEXT NOT NULL,
    "isOnline" BOOLEAN NOT NULL DEFAULT false,
    "scope" TEXT,
    "expires" TIMESTAMP(3),
    "accessToken" TEXT NOT NULL,
    "userId" BIGINT,
    "firstName" TEXT,
    "lastName" TEXT,
    "email" TEXT,
    "accountOwner" BOOLEAN NOT NULL DEFAULT false,
    "locale" TEXT,
    "collaborator" BOOLEAN DEFAULT false,
    "emailVerified" BOOLEAN DEFAULT false,
    "refreshToken" TEXT,
    "refreshTokenExpires" TIMESTAMP(3),
    CONSTRAINT "Session_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "Session_shop_idx" ON "Session"("shop");

CREATE TABLE "AuditRun" (
    "id" TEXT NOT NULL,
    "shop" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "productsSeen" INTEGER NOT NULL DEFAULT 0,
    "productsAudited" INTEGER NOT NULL DEFAULT 0,
    "findings" INTEGER NOT NULL DEFAULT 0,
    "critical" INTEGER NOT NULL DEFAULT 0,
    "expected" INTEGER NOT NULL DEFAULT 0,
    "errorMessage" TEXT,
    "startedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "finishedAt" TIMESTAMP(3),
    CONSTRAINT "AuditRun_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "AuditRun_shop_startedAt_idx" ON "AuditRun"("shop", "startedAt");

CREATE TABLE "Incident" (
    "id" TEXT NOT NULL,
    "shop" TEXT NOT NULL,
    "auditRunId" TEXT NOT NULL,
    "productId" TEXT,
    "productTitle" TEXT,
    "handle" TEXT,
    "variantId" TEXT,
    "variantTitle" TEXT,
    "sku" TEXT,
    "kind" TEXT NOT NULL,
    "severity" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'OPEN',
    "expectedValue" TEXT,
    "observedValue" TEXT,
    "detail" TEXT,
    "fingerprint" TEXT NOT NULL,
    "firstSeenAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "lastSeenAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "resolvedAt" TIMESTAMP(3),
    CONSTRAINT "Incident_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "Incident_shop_status_severity_idx" ON "Incident"("shop", "status", "severity");
CREATE INDEX "Incident_shop_fingerprint_idx" ON "Incident"("shop", "fingerprint");
ALTER TABLE "Incident" ADD CONSTRAINT "Incident_auditRunId_fkey" FOREIGN KEY ("auditRunId") REFERENCES "AuditRun"("id") ON DELETE CASCADE ON UPDATE CASCADE;
