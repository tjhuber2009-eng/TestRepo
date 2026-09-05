import type { LoaderFunctionArgs } from "react-router";
import { Form, Link, useLoaderData } from "react-router";
import { authenticate } from "../shopify.server";
import db from "../db.server";

const PAGE_SIZE = 50;

export async function loader({ request }: LoaderFunctionArgs) {
  const { session } = await authenticate.admin(request);
  const url = new URL(request.url);
  const status = ["OPEN", "RESOLVED", "ALL"].includes(url.searchParams.get("status") || "")
    ? url.searchParams.get("status")!
    : "OPEN";
  const severity = ["CRITICAL", "WARNING", "INFO", "ALL"].includes(url.searchParams.get("severity") || "")
    ? url.searchParams.get("severity")!
    : "ALL";
  const page = Math.max(1, Math.min(Number(url.searchParams.get("page") || 1) || 1, 10_000));

  const where: { shop: string; status?: string; severity?: string } = { shop: session.shop };
  if (status !== "ALL") where.status = status;
  if (severity !== "ALL") where.severity = severity;

  const [incidents, total] = await Promise.all([
    db.incident.findMany({
      where,
      orderBy: [{ status: "asc" }, { severity: "asc" }, { lastSeenAt: "desc" }],
      skip: (page - 1) * PAGE_SIZE,
      take: PAGE_SIZE,
    }),
    db.incident.count({ where }),
  ]);

  return {
    incidents,
    total,
    page,
    pages: Math.max(1, Math.ceil(total / PAGE_SIZE)),
    filters: { status, severity },
  };
}

function pageHref(page: number, status: string, severity: string) {
  const params = new URLSearchParams({ page: String(page), status, severity });
  return `/app/incidents?${params.toString()}`;
}

export default function Incidents() {
  const { incidents, total, page, pages, filters } = useLoaderData<typeof loader>();

  return (
    <s-page heading="Incidents">
      <s-section heading="Catalog findings">
        <s-paragraph>
          Critical findings indicate storefront data that disagrees with Shopify Admin. Warnings indicate incomplete or unreliable verification and are deliberately not auto-resolved until a later trustworthy audit.
        </s-paragraph>

        <Form method="get">
          <label htmlFor="status">Status </label>
          <select id="status" name="status" defaultValue={filters.status}>
            <option value="OPEN">Open</option>
            <option value="RESOLVED">Resolved</option>
            <option value="ALL">All</option>
          </select>{" "}
          <label htmlFor="severity">Severity </label>
          <select id="severity" name="severity" defaultValue={filters.severity}>
            <option value="ALL">All</option>
            <option value="CRITICAL">Critical</option>
            <option value="WARNING">Warning</option>
            <option value="INFO">Info</option>
          </select>{" "}
          <s-button type="submit">Filter</s-button>
        </Form>

        <s-paragraph>{String(total)} matching incident{total === 1 ? "" : "s"}.</s-paragraph>

        <s-stack direction="block" gap="small">
          {incidents.length === 0 ? <s-paragraph>No matching incidents.</s-paragraph> : incidents.map((incident) => (
            <s-box key={incident.id} padding="base" border="base" borderRadius="base">
              <s-heading>{incident.status} · {incident.severity} · {incident.kind}</s-heading>
              <s-paragraph>
                {incident.productTitle || incident.handle || "Catalog item"}
                {incident.variantTitle ? ` — ${incident.variantTitle}` : ""}
              </s-paragraph>
              <s-text>
                {incident.expectedValue ? `Expected: ${incident.expectedValue}. ` : ""}
                {incident.observedValue ? `Observed: ${incident.observedValue}. ` : ""}
                {incident.detail || ""}
              </s-text>
              <s-paragraph>
                First seen {new Date(incident.firstSeenAt).toLocaleString()}; last seen {new Date(incident.lastSeenAt).toLocaleString()}; observed {String(incident.occurrenceCount)} time{incident.occurrenceCount === 1 ? "" : "s"}.
                {incident.resolvedAt ? ` Resolved ${new Date(incident.resolvedAt).toLocaleString()}.` : ""}
              </s-paragraph>
            </s-box>
          ))}
        </s-stack>

        {pages > 1 ? (
          <s-paragraph>
            {page > 1 ? <Link to={pageHref(page - 1, filters.status, filters.severity)}>Previous</Link> : null}
            {page > 1 && page < pages ? " · " : ""}
            {page < pages ? <Link to={pageHref(page + 1, filters.status, filters.severity)}>Next</Link> : null}
            {` — Page ${page} of ${pages}`}
          </s-paragraph>
        ) : null}
      </s-section>
    </s-page>
  );
}
