import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { Link, Outlet, useLoaderData, useRouteError } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { AppProvider } from "@shopify/shopify-app-react-router/react";
import { NavMenu } from "@shopify/app-bridge-react";
import { authenticate } from "../shopify.server";
import { fetchActiveSubscription } from "../partner-api.server";
import db from "../db.server";

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { admin, redirect, session } = await authenticate.admin(request);

  if (process.env.BILLING_ENFORCED === "true") {
    const state = await db.shopAuditState.findUnique({
      where: { shop: session.shop },
      select: { shopGid: true },
    });
    let shopId = state?.shopGid ?? null;

    if (!shopId) {
      const shopResponse = await admin.graphql(`{ shop { id } }`);
      const shopBody = await shopResponse.json() as {
        data?: { shop?: { id?: string } };
        errors?: Array<{ message?: string }>;
      };
      if (!shopResponse.ok || shopBody.errors?.length) {
        throw new Error("Unable to resolve Shopify shop ID for billing");
      }
      shopId = shopBody.data?.shop?.id ?? null;
      if (!shopId) throw new Error("Unable to resolve Shopify shop ID for billing");

      await db.shopAuditState.upsert({
        where: { shop: session.shop },
        create: { shop: session.shop, shopGid: shopId },
        update: { shopGid: shopId },
      });
    }

    const subscription = await fetchActiveSubscription(shopId);
    if (!subscription) {
      const storeHandle = session.shop.replace(/\.myshopify\.com$/i, "");
      const appHandle = process.env.SHOPIFY_APP_HANDLE || "catalogmirror";
      return redirect(
        `https://admin.shopify.com/store/${storeHandle}/charges/${appHandle}/pricing_plans`,
        { target: "_top" },
      );
    }
  }

  return { apiKey: process.env.SHOPIFY_API_KEY || "" };
};

export default function App() {
  const { apiKey } = useLoaderData<typeof loader>();
  return (
    <AppProvider embedded apiKey={apiKey}>
      <NavMenu>
        <Link to="/app" rel="home">Home</Link>
        <Link to="/app/incidents">Incidents</Link>
        <Link to="/app/about">About</Link>
      </NavMenu>
      <Outlet />
    </AppProvider>
  );
}

export function ErrorBoundary() {
  return boundary.error(useRouteError());
}

export const headers: HeadersFunction = (args) => boundary.headers(args);
