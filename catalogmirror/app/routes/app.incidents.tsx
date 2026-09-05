import type { LoaderFunctionArgs } from "react-router";
import { useLoaderData } from "react-router";
import { authenticate } from "../shopify.server";
import db from "../db.server";

export async function loader({ request }: LoaderFunctionArgs) {
  const { session } = await authenticate.admin(request);
  const incidents = await db.incident.findMany({
    where: { shop: session.shop },
    orderBy: [{ status: "asc" }, { severity: "asc" }, { lastSeenAt: "desc" }],
    take: 250,
  });
  return { incidents };
}

export default function Incidents() {
  const { incidents } = useLoaderData<typeof loader>();
  return (
    <s-page heading="Incidents">
      <s-section heading="Catalog findings">
        <s-paragraph>Critical findings indicate storefront data that disagrees with the Shopify Admin source of truth. Expected exclusions are informational.</s-paragraph>
        <s-stack direction="block" gap="small">
          {incidents.length === 0 ? <s-paragraph>No audit findings yet.</s-paragraph> : incidents.map((i) => (
            <s-box key={i.id} padding="base" border="base" borderRadius="base">
              <s-heading>{i.status} · {i.severity} · {i.kind}</s-heading>
              <s-paragraph>{i.productTitle || i.handle || "Catalog item"}{i.variantTitle ? ` — ${i.variantTitle}` : ""}</s-paragraph>
              <s-text>{i.expectedValue ? `Expected: ${i.expectedValue}. ` : ""}{i.observedValue ? `Observed: ${i.observedValue}. ` : ""}{i.detail || ""}</s-text>
            </s-box>
          ))}
        </s-stack>
      </s-section>
    </s-page>
  );
}
