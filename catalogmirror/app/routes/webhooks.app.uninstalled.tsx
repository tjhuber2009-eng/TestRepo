import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import db from "../db.server";
import { processWebhookDelivery } from "../lib/webhook.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic, webhookId } = await authenticate.webhook(request);
  await processWebhookDelivery({ shop, topic, webhookId }, async () => {
    await db.session.deleteMany({ where: { shop } });
  });
  return new Response(null, { status: 200 });
};
