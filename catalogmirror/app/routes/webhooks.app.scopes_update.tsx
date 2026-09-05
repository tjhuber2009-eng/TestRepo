import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import db from "../db.server";
import { processWebhookDelivery } from "../lib/webhook.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic, webhookId, payload, session } = await authenticate.webhook(request);
  await processWebhookDelivery({ shop, topic, webhookId }, async () => {
    if (!session) return;
    const current = (payload as { current?: unknown }).current;
    const scope = Array.isArray(current) && current.every((value) => typeof value === "string")
      ? current.join(",")
      : session.scope;
    await db.session.updateMany({ where: { id: session.id }, data: { scope } });
  });
  return new Response(null, { status: 200 });
};
