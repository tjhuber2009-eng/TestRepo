import "@shopify/shopify-app-react-router/adapters/node";
import { ApiVersion, AppDistribution, shopifyApp } from "@shopify/shopify-app-react-router/server";
import { PrismaSessionStorage } from "@shopify/shopify-app-session-storage-prisma";
import prisma from "./db.server";

function requiredEnv(name: string) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

const apiKey = requiredEnv("SHOPIFY_API_KEY");
const apiSecretKey = requiredEnv("SHOPIFY_API_SECRET");
const appUrl = requiredEnv("SHOPIFY_APP_URL");
const parsedAppUrl = new URL(appUrl);

if (process.env.NODE_ENV === "production" && parsedAppUrl.protocol !== "https:") {
  throw new Error("SHOPIFY_APP_URL must use HTTPS in production");
}

const scopes = (process.env.SCOPES || "read_products,read_inventory")
  .split(",")
  .map((scope) => scope.trim())
  .filter(Boolean);

for (const required of ["read_products", "read_inventory"]) {
  if (!scopes.includes(required)) throw new Error(`CatalogMirror requires the ${required} scope`);
}

if (scopes.some((scope) => scope.startsWith("write_"))) {
  throw new Error("CatalogMirror is read-only and must not request write scopes");
}

const shopify = shopifyApp({
  apiKey,
  apiSecretKey,
  apiVersion: ApiVersion.July26,
  scopes,
  appUrl,
  authPathPrefix: "/auth",
  sessionStorage: new PrismaSessionStorage(prisma),
  distribution: AppDistribution.AppStore,
  future: { expiringOfflineAccessTokens: true },
});

export default shopify;
export const apiVersion = ApiVersion.July26;
export const addDocumentResponseHeaders = shopify.addDocumentResponseHeaders;
export const authenticate = shopify.authenticate;
export const unauthenticated = shopify.unauthenticated;
export const login = shopify.login;
export const registerWebhooks = shopify.registerWebhooks;
export const sessionStorage = shopify.sessionStorage;
