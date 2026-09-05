import db from "../db.server";
import { isAutoAuditEnabled } from "../lib/auto-audit-core";

export async function loader() {
  const started = Date.now();
  try {
    await db.$queryRaw`SELECT 1`;
    const autoAuditEnabled = isAutoAuditEnabled();
    const autoAuditWorkerReady =
      !autoAuditEnabled || Boolean(globalThis.catalogMirrorAutoAuditWorkerStarted);

    if (!autoAuditWorkerReady) {
      return Response.json(
        {
          ok: false,
          database: "ready",
          autoAuditWorker: "not-started",
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
