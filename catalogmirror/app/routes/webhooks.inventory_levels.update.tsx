import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import {
  enqueueAutoAuditTask,
  inventoryItemGidFromPayload,
  markShopChanged,
  processWebhookDelivery,
} from "../lib/webhook.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic, webhookId, payload } = await authenticate.webhook(request);
  await processWebhookDelivery({ shop, topic, webhookId }, async () => {
    const inventoryItemId = inventoryItemGidFromPayload(payload);
    if (inventoryItemId) {
      await enqueueAutoAuditTask({
        shop,
        topic,
        resourceType: "INVENTORY_ITEM",
        resourceId: inventoryItemId,
      });
    } else {
      await markShopChanged(shop, topic);
    }
  });
  return new Response(null, { status: 200 });
};
