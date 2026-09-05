import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData, useNavigation } from "react-router";
import { authenticate } from "../shopify.server";
import db from "../db.server";
import { runCatalogAudit } from "../lib/audit.server";

export async function loader({ request }: LoaderFunctionArgs) {
  const { session } = await authenticate.admin(request);
  const [lastRun, openCritical, openWarnings, recent] = await Promise.all([
    db.auditRun.findFirst({ where: { shop: session.shop }, orderBy: { startedAt: "desc" } }),
    db.incident.count({ where: { shop: session.shop, status: "OPEN", severity: "CRITICAL" } }),
    db.incident.count({ where: { shop: session.shop, status: "OPEN", severity: "WARNING" } }),
    db.incident.findMany({ where: { shop: session.shop, status: "OPEN" }, orderBy: { lastSeenAt: "desc" }, take: 8 }),
  ]);
  return { shop: session.shop, lastRun, openCritical, openWarnings, recent };
}

export async function action({ request }: ActionFunctionArgs) {
  const { admin, session } = await authenticate.admin(request);
  const form = await request.formData();
  if (form.get("intent") !== "audit") return { ok: false, message: "Unknown action" };
  try {
    const run = await runCatalogAudit({ admin, shop: session.shop });
    return { ok: true, message: `Audit complete: ${run.productsAudited} products checked, ${run.critical} critical findings.` };
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : "Audit failed" };
  }
}

export default function Dashboard() {
  const data = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>();
  const nav = useNavigation();
  const running = nav.state !== "idle";
  return (
    <s-page heading="CatalogMirror">
      <s-section heading="Catalog integrity">
        <s-paragraph>Compare Shopify Admin product truth against the public Online Store representation merchants and automated systems can see.</s-paragraph>
        <Form method="post">
          <input type="hidden" name="intent" value="audit" />
          <s-button type="submit" variant="primary" loading={running}>Run catalog audit</s-button>
        </Form>
        {actionData?.message ? <s-banner tone={actionData.ok ? "success" : "critical"}>{actionData.message}</s-banner> : null}
      </s-section>

      <s-section heading="Status">
        <s-stack direction="inline" gap="base">
          <s-box padding="base" border="base" borderRadius="base"><s-heading>Critical</s-heading><s-text>{String(data.openCritical)}</s-text></s-box>
          <s-box padding="base" border="base" borderRadius="base"><s-heading>Warnings</s-heading><s-text>{String(data.openWarnings)}</s-text></s-box>
          <s-box padding="base" border="base" borderRadius="base"><s-heading>Last audit</s-heading><s-text>{data.lastRun?.finishedAt ? new Date(data.lastRun.finishedAt).toLocaleString() : "Not run yet"}</s-text></s-box>
        </s-stack>
      </s-section>

      <s-section heading="Recent open findings">
        {data.recent.length === 0 ? <s-paragraph>No open mismatches. Run an audit to verify the catalog.</s-paragraph> : (
          <s-stack direction="block" gap="small">
            {data.recent.map((i) => (
              <s-box key={i.id} padding="base" border="base" borderRadius="base">
                <s-heading>{i.kind}</s-heading>
                <s-paragraph>{i.productTitle || i.handle || "Catalog item"}{i.variantTitle ? ` — ${i.variantTitle}` : ""}</s-paragraph>
                <s-text>{i.expectedValue ? `Expected: ${i.expectedValue}. ` : ""}{i.observedValue ? `Observed: ${i.observedValue}.` : i.detail || ""}</s-text>
              </s-box>
            ))}
          </s-stack>
        )}
      </s-section>
    </s-page>
  );
}
