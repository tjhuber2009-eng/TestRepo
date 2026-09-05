export function isReconciliationEnabled(env = process.env) {
  if (env.RECONCILIATION_ENABLED === "false") return false;
  if (env.RECONCILIATION_ENABLED === "true") return true;
  return env.NODE_ENV === "production";
}

export function reconciliationIntervalMs(raw = process.env.RECONCILIATION_INTERVAL_MINUTES) {
  const minutes = Number(raw ?? 360);
  const clamped = Number.isFinite(minutes)
    ? Math.max(30, Math.min(Math.trunc(minutes), 7 * 24 * 60))
    : 360;
  return clamped * 60_000;
}

export function reconciliationSchedulerPollMs(raw = process.env.RECONCILIATION_SCHEDULER_POLL_MS) {
  const ms = Number(raw ?? 300_000);
  return Number.isFinite(ms)
    ? Math.max(60_000, Math.min(Math.trunc(ms), 30 * 60_000))
    : 300_000;
}

export function reconciliationOverlapMs(raw = process.env.RECONCILIATION_OVERLAP_MINUTES) {
  const minutes = Number(raw ?? 5);
  const clamped = Number.isFinite(minutes)
    ? Math.max(1, Math.min(Math.trunc(minutes), 60))
    : 5;
  return clamped * 60_000;
}

export function reconciliationPollMs(raw = process.env.RECONCILIATION_BULK_POLL_SECONDS) {
  const seconds = Number(raw ?? 60);
  const clamped = Number.isFinite(seconds)
    ? Math.max(15, Math.min(Math.trunc(seconds), 300))
    : 60;
  return clamped * 1000;
}

export function reconciliationWindow(lastSuccessfulAt: Date | null, now = new Date()) {
  const cutoff = new Date(now.getTime() - 60_000);
  const since = lastSuccessfulAt
    ? new Date(lastSuccessfulAt.getTime() - reconciliationOverlapMs())
    : null;
  return { since, cutoff };
}

export function productUpdatedAtQuery(since: Date | null, cutoff: Date) {
  const upper = "updated_at:<='" + cutoff.toISOString() + "'";
  if (!since) return upper;
  return "updated_at:>'" + since.toISOString() + "' " + upper;
}

export function bulkOperationGidFromPayload(payload: Record<string, unknown>) {
  const id = payload.admin_graphql_api_id;
  return typeof id === "string" && /^gid:\/\/shopify\/BulkOperation\/\d+$/.test(id)
    ? id
    : null;
}
