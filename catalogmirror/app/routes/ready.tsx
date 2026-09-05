import db from "../db.server";
import { isAutoAuditEnabled } from "../lib/auto-audit-core";
import { isReconciliationEnabled } from "../lib/reconciliation-core";

export async function loader() {
  const started = Date.now();
  try {
    await db.$queryRaw`SELECT 1`;
    const autoAuditEnabled = isAutoAuditEnabled();
    const autoAuditWorkerReady =
      !autoAuditEnabled || Boolean(globalThis.catalogMirrorAutoAuditWorkerStarted);
    const reconciliationEnabled = isReconciliationEnabled();
    const reconciliationSchedulerReady =
      !reconciliationEnabled || Boolean(globalThis.catalogMirrorReconciliationSchedulerStarted);

    if (!autoAuditWorkerReady || !reconciliationSchedulerReady) {
      return Response.json(
        {
          ok: false,
          database: "ready",
          autoAuditWorker: autoAuditWorkerReady ? "ready" : "not-started",
          reconciliationScheduler: reconciliationSchedulerReady ? "ready" : "not-started",
          latencyMs: Date.now() - started,
        },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }

    return Response.json(
      {
        ok: true,
        database: "ready",
        autoAuditWorker: autoAuditEnabled ? "ready" : "disabled",
        reconciliationScheduler: reconciliationEnabled ? "ready" : "disabled",
        latencyMs: Date.now() - started,
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return Response.json(
      { ok: false, database: "unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
