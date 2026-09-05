import type { ActionFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";

export const action = async ({ request }: ActionFunctionArgs) => {
  const { shop, topic } = await authenticate.webhook(request);
  console.log(`CatalogMirror received ${topic} for ${shop}; next merchant audit will reconcile storefront parity.`);
  return new Response();
};
