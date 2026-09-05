import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import { processWebhookDelivery } from "../lib/webhook.server";
import { bulkOperationGidFromPayload } from "../lib/reconciliation-core";
import { wakeReconciliationTask } from "../lib/auto-audit-queue.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic, webhookId, payload } = await authenticate.webhook(request);

  await processWebhookDelivery({ shop, topic, webhookId }, async () => {
    const operationId = bulkOperationGidFromPayload(payload);
    if (!operationId) return;
    await wakeReconciliationTask(shop, operationId);
  });

  return new Response(null, { status: 200 });
};
