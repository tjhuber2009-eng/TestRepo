import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import db from "../db.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop } = await authenticate.webhook(request);
  await db.$transaction([
    db.incident.deleteMany({ where: { shop } }),
    db.auditRun.deleteMany({ where: { shop } }),
    db.session.deleteMany({ where: { shop } }),
    db.webhookReceipt.deleteMany({ where: { shop } }),
    db.auditLease.deleteMany({ where: { shop } }),
    db.shopAuditState.deleteMany({ where: { shop } }),
  ]);
  return new Response(null, { status: 200 });
};
