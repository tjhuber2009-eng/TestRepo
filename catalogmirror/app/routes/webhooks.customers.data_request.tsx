import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import { processWebhookDelivery } from "../lib/webhook.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic, webhookId } = await authenticate.webhook(request);
  await processWebhookDelivery({ shop, topic, webhookId }, async () => {
    // CatalogMirror does not store customer records, so there is no customer data to export or redact.
  });
  return new Response(null, { status: 200 });
};
