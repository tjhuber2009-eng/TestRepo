import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { Outlet, useLoaderData, useRouteError } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { AppProvider } from "@shopify/shopify-app-react-router/react";
import { authenticate } from "../shopify.server";
import { fetchActiveSubscription } from "../partner-api.server";

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { admin, redirect, session } = await authenticate.admin(request);

  if (process.env.BILLING_ENFORCED === "true") {
    const shopResponse = await admin.graphql(`{ shop { id } }`);
    const shopBody = await shopResponse.json() as any;
    const shopId = shopBody.data?.shop?.id as string | undefined;
    if (!shopId) throw new Error("Unable to resolve Shopify shop ID for billing");

    const subscription = await fetchActiveSubscription(shopId);
    if (!subscription) {
      const storeHandle = session.shop.replace(/\.myshopify\.com$/i, "");
      const appHandle = process.env.SHOPIFY_APP_HANDLE || "catalogmirror";
      return redirect(`https://admin.shopify.com/store/${storeHandle}/charges/${appHandle}/pricing_plans`, { target: "_top" });
    }
  }

  return { apiKey: process.env.SHOPIFY_API_KEY || "" };
};

export default function App() {
  const { apiKey } = useLoaderData<typeof loader>();
  return (
    <AppProvider embedded apiKey={apiKey}>
      <s-app-nav>
        <s-link href="/app">Dashboard</s-link>
        <s-link href="/app/incidents">Incidents</s-link>
        <s-link href="/app/about">About</s-link>
      </s-app-nav>
      <Outlet />
    </AppProvider>
  );
}

export function ErrorBoundary() { return boundary.error(useRouteError()); }
export const headers: HeadersFunction = (args) => boundary.headers(args);
