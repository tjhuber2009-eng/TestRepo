import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData, useNavigation } from "react-router";
import { authenticate } from "../shopify.server";
import db from "../db.server";
import {
  AuditInProgressError,
  getAuditMaxProducts,
  runCatalogAudit,
} from "../lib/audit.server";

function durationLabel(durationMs: number | null) {
  if (!durationMs) return "—";
  if (durationMs < 1000) return `${durationMs} ms`;
  const seconds = Math.round(durationMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export async function loader({ request }: LoaderFunctionArgs) {
  const { session } = await authenticate.admin(request);
  const [lastRun, openCritical, openWarnings, recent, state, recentRuns, queuedTasks] = await Promise.all([
    db.auditRun.findFirst({ where: { shop: session.shop }, orderBy: { startedAt: "desc" } }),
    db.incident.count({ where: { shop: session.shop, status: "OPEN", severity: "CRITICAL" } }),
    db.incident.count({ where: { shop: session.shop, status: "OPEN", severity: "WARNING" } }),
    db.incident.findMany({
      where: { shop: session.shop, status: "OPEN", severity: { in: ["CRITICAL", "WARNING"] } },
      orderBy: { lastSeenAt: "desc" },
      take: 8,
    }),
    db.shopAuditState.findUnique({ where: { shop: session.shop } }),
    db.auditRun.findMany({
      where: { shop: session.shop },
      orderBy: { startedAt: "desc" },
      take: 5,
    }),
    db.auditTask.count({ where: { shop: session.shop } }),
  ]);

  return {
    lastRun,
    openCritical,
    openWarnings,
    recent,
    pendingChanges: queuedTasks,
    lastWebhookAt: state?.lastWebhookAt ?? null,
    maxProducts: getAuditMaxProducts(),
    recentRuns,
    lastAutoAuditAt: state?.lastAutoAuditAt ?? null,
    lastAutoAuditError: state?.lastAutoAuditError ?? null,
    lastReconciliationAt: state?.lastReconciliationAt ?? null,
    lastReconciliationError: state?.lastReconciliationError ?? null,
    reconciliationDiscovered: state?.reconciliationDiscovered ?? 0,
    reconciliationRunning: Boolean(state?.reconciliationBulkOperationId),
    autoAuditEnabled:
      process.env.AUTO_AUDIT_ENABLED === "true" ||
      (process.env.AUTO_AUDIT_ENABLED !== "false" && process.env.NODE_ENV === "production"),
    reconciliationEnabled:
      process.env.RECONCILIATION_ENABLED === "true" ||
      (process.env.RECONCILIATION_ENABLED !== "false" && process.env.NODE_ENV === "production"),
  };
}

export async function action({ request }: ActionFunctionArgs) {
  const { admin, session } = await authenticate.admin(request);
  const form = await request.formData();
  if (form.get("intent") !== "audit") return { ok: false, message: "Unknown action" };

  const requestedLimit = Number(form.get("limit") || getAuditMaxProducts());

  try {
    const run = await runCatalogAudit({
      admin,
      shop: session.shop,
      limit: requestedLimit,
      trigger: "MANUAL",
    });
    const coverage = run.catalogTruncated
      ? " Coverage was partial; incidents outside the scanned products were preserved."
      : " Full requested coverage completed.";
    return {
      ok: true,
      message: `Audit complete: ${run.productsAudited} products checked, ${run.critical} critical, ${run.warnings} warnings.${coverage}`,
    };
  } catch (error) {
    if (error instanceof AuditInProgressError) return { ok: false, message: error.message };
    return { ok: false, message: error instanceof Error ? error.message : "Audit failed" };
  }
}

export default function Dashboard() {
  const data = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>();
  const nav = useNavigation();
  const running = nav.state !== "idle";
  const options = [50, 100, 250, 500].filter((value) => value <= data.maxProducts);
  if (!options.includes(data.maxProducts)) options.push(data.maxProducts);

  return (
    <s-page heading="CatalogMirror">
      {data.pendingChanges > 0 ? (
        <s-banner tone="warning">
          {data.autoAuditEnabled
            ? `${data.pendingChanges} catalog verification task${data.pendingChanges === 1 ? "" : "s"} queued for automatic checking.`
            : `${data.pendingChanges} catalog change${data.pendingChanges === 1 ? "" : "s"} waiting for verification. Automatic audits are disabled.`}
        </s-banner>
      ) : null}
      {data.lastAutoAuditError ? (
        <s-banner tone="critical">
          Automatic verification encountered an error and will retry: {data.lastAutoAuditError}
        </s-banner>
      ) : null}
      {data.lastReconciliationError ? (
        <s-banner tone="warning">
          Periodic reconciliation encountered an error and will retry: {data.lastReconciliationError}
        </s-banner>
      ) : null}

      <s-section heading="Catalog integrity">
        <s-paragraph>
          Compare Shopify Admin product truth with the public Online Store representation. CatalogMirror is read-only and never changes catalog data.
        </s-paragraph>
        <Form method="post">
          <input type="hidden" name="intent" value="audit" />
          <label htmlFor="audit-limit">Products to scan </label>
          <select id="audit-limit" name="limit" defaultValue={String(Math.min(250, data.maxProducts))}>
            {options.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>{" "}
          <s-button type="submit" variant="primary" loading={running}>Run catalog audit</s-button>
        </Form>
        {actionData?.message ? (
          <s-banner tone={actionData.ok ? "success" : "critical"}>{actionData.message}</s-banner>
        ) : null}
      </s-section>

      <s-section heading="Status">
        <s-stack direction="inline" gap="base">
          <s-box padding="base" border="base" borderRadius="base">
            <s-heading>Critical</s-heading>
            <s-text>{String(data.openCritical)}</s-text>
          </s-box>
          <s-box padding="base" border="base" borderRadius="base">
            <s-heading>Warnings</s-heading>
            <s-text>{String(data.openWarnings)}</s-text>
          </s-box>
          <s-box padding="base" border="base" borderRadius="base">
            <s-heading>Pending changes</s-heading>
            <s-text>{String(data.pendingChanges)}</s-text>
          </s-box>
          <s-box padding="base" border="base" borderRadius="base">
            <s-heading>Auto monitor</s-heading>
            <s-text>{data.autoAuditEnabled ? "On" : "Off"}</s-text>
          </s-box>
          <s-box padding="base" border="base" borderRadius="base">
            <s-heading>Reconciliation</s-heading>
            <s-text>{data.reconciliationEnabled ? (data.reconciliationRunning ? "Running" : "On") : "Off"}</s-text>
          </s-box>
          <s-box padding="base" border="base" borderRadius="base">
            <s-heading>Last audit</s-heading>
            <s-text>{data.lastRun?.finishedAt ? new Date(data.lastRun.finishedAt).toLocaleString() : "Not run yet"}</s-text>
          </s-box>
          <s-box padding="base" border="base" borderRadius="base">
            <s-heading>Last automatic check</s-heading>
            <s-text>{data.lastAutoAuditAt ? new Date(data.lastAutoAuditAt).toLocaleString() : "Not run yet"}</s-text>
          </s-box>
          <s-box padding="base" border="base" borderRadius="base">
            <s-heading>Last reconciliation</s-heading>
            <s-text>{data.lastReconciliationAt ? new Date(data.lastReconciliationAt).toLocaleString() : "Not run yet"}</s-text>
          </s-box>
        </s-stack>
        {data.lastReconciliationAt ? (
          <s-paragraph>
            Last reconciliation discovered {String(data.reconciliationDiscovered)} product{data.reconciliationDiscovered === 1 ? "" : "s"} for targeted verification.
          </s-paragraph>
        ) : null}
        {data.lastRun ? (
          <s-paragraph>
            Last run: {data.lastRun.status}; {String(data.lastRun.productsSeen)} products loaded; {String(data.lastRun.productsAudited)} audited; {String(data.lastRun.critical)} critical; {String(data.lastRun.warnings)} warnings; {durationLabel(data.lastRun.durationMs)}.
            {data.lastRun.catalogTruncated ? " This was a partial catalog scan." : ""}
          </s-paragraph>
        ) : null}
      </s-section>

      <s-section heading="Recent open findings">
        {data.recent.length === 0 ? (
          <s-paragraph>No open critical or warning findings. Run an audit to verify the catalog.</s-paragraph>
        ) : (
          <s-stack direction="block" gap="small">
            {data.recent.map((incident) => (
              <s-box key={incident.id} padding="base" border="base" borderRadius="base">
                <s-heading>{incident.severity} · {incident.kind}</s-heading>
                <s-paragraph>
                  {incident.productTitle || incident.handle || "Catalog item"}
                  {incident.variantTitle ? ` — ${incident.variantTitle}` : ""}
                </s-paragraph>
                <s-text>
                  {incident.expectedValue ? `Expected: ${incident.expectedValue}. ` : ""}
                  {incident.observedValue ? `Observed: ${incident.observedValue}. ` : ""}
                  {incident.detail || ""}
                </s-text>
              </s-box>
            ))}
          </s-stack>
        )}
        <s-paragraph><Link to="/app/incidents">Review all incidents</Link></s-paragraph>
      </s-section>

      <s-section heading="Audit history">
        {data.recentRuns.length === 0 ? (
          <s-paragraph>No audits have run yet.</s-paragraph>
        ) : (
          <s-stack direction="block" gap="small">
            {data.recentRuns.map((run) => (
              <s-box key={run.id} padding="small" border="base" borderRadius="base">
                <s-text>
                  {new Date(run.startedAt).toLocaleString()} — {run.status} — {String(run.productsAudited)} audited — {String(run.critical)} critical — {String(run.warnings)} warnings — {durationLabel(run.durationMs)}
                  {run.catalogTruncated ? " — partial" : ""}
                </s-text>
              </s-box>
            ))}
          </s-stack>
        )}
      </s-section>
    </s-page>
  );
}
