import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import db from "../db.server";
import { processWebhookDelivery } from "../lib/webhook.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic, webhookId } = await authenticate.webhook(request);
  await processWebhookDelivery({ shop, topic, webhookId }, async () => {
    await db.$transaction([
      db.auditTask.deleteMany({ where: { shop } }),
      db.auditLease.deleteMany({ where: { shop } }),
      db.session.deleteMany({ where: { shop } }),
      db.shopAuditState.upsert({
        where: { shop },
        create: { shop, pendingChanges: 0, lastWebhookTopic: topic, lastWebhookAt: new Date() },
        update: { pendingChanges: 0, lastWebhookTopic: topic, lastWebhookAt: new Date() },
      }),
    ]);
  });
  return new Response(null, { status: 200 });
};
