import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import {
  enqueueAutoAuditTask,
  markShopChanged,
  processWebhookDelivery,
  productGidFromPayload,
} from "../lib/webhook.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic, webhookId, payload } = await authenticate.webhook(request);
  await processWebhookDelivery({ shop, topic, webhookId }, async () => {
    const productId = productGidFromPayload(payload);
    if (productId) {
      await enqueueAutoAuditTask({ shop, topic, resourceType: "PRODUCT", resourceId: productId });
    } else {
      await markShopChanged(shop, topic);
    }
  });
  return new Response(null, { status: 200 });
};
