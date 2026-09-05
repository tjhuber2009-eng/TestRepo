import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import db from "../db.server";
import {
  cancelAutoAuditTask,
  processWebhookDelivery,
  productGidFromPayload,
  syncPendingAuditCount,
} from "../lib/webhook.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic, webhookId, payload } = await authenticate.webhook(request);
  await processWebhookDelivery({ shop, topic, webhookId }, async () => {
    const productId = productGidFromPayload(payload);
    if (productId) {
      await db.$transaction([
        db.incident.updateMany({
          where: { shop, productId, status: "OPEN" },
          data: { status: "RESOLVED", resolvedAt: new Date() },
        }),
        db.auditTask.deleteMany({
          where: { shop, resourceType: "PRODUCT", resourceId: productId },
        }),
      ]);
    }
    await syncPendingAuditCount(shop, { topic, webhookAt: new Date() });
  });
  return new Response(null, { status: 200 });
};
