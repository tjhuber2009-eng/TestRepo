import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import { markShopChanged, processWebhookDelivery } from "../lib/webhook.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic, webhookId } = await authenticate.webhook(request);
  await processWebhookDelivery({ shop, topic, webhookId }, async () => {
    await markShopChanged(shop, topic);
  });
  return new Response(null, { status: 200 });
};
