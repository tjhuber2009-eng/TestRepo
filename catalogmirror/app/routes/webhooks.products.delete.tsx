import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import db from "../db.server";
import {
  markShopChanged,
  processWebhookDelivery,
  productGidFromPayload,
} from "../lib/webhook.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic, webhookId, payload } = await authenticate.webhook(request);
  await processWebhookDelivery({ shop, topic, webhookId }, async () => {
    const productId = productGidFromPayload(payload);
    if (productId) {
      await db.incident.updateMany({
        where: { shop, productId, status: "OPEN" },
        data: { status: "RESOLVED", resolvedAt: new Date() },
      });
    }
    await markShopChanged(shop, topic);
  });
  return new Response(null, { status: 200 });
};
