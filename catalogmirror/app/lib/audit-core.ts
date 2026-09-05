import { isIP } from "node:net";

export type AdminVariantCore = {
  id: string;
  title: string;
  sku: string | null;
  price: string;
  inventoryQuantity: number | null;
  inventoryPolicy: "DENY" | "CONTINUE";
  inventoryItem: { tracked: boolean } | null;
};

export type StorefrontVariantCore = {
  id: number | string;
  title: string;
  sku?: string | null;
  available: boolean;
  price: number;
};

export type FingerprintableFinding = {
  productId?: string;
  variantId?: string;
  kind: string;
};

export function fingerprintIdentity(finding: FingerprintableFinding) {
  return [finding.productId || "", finding.variantId || "", finding.kind].join("|");
}

export function expectedAvailable(variant: AdminVariantCore) {
  if (!variant.inventoryItem?.tracked) return true;
  if (variant.inventoryPolicy === "CONTINUE") return true;
  return (variant.inventoryQuantity ?? 0) > 0;
}

export function shopifyNumericId(gid: string | null | undefined) {
  if (!gid) return null;
  const match = gid.match(/\/(\d+)$/);
  return match?.[1] ?? null;
}

function normalizedSku(value: string | null | undefined) {
  const sku = value?.trim();
  return sku ? sku : null;
}

export function matchStorefrontVariant(
  admin: Pick<AdminVariantCore, "id" | "title" | "sku">,
  storefront: StorefrontVariantCore[],
) {
  const adminId = shopifyNumericId(admin.id);
  if (adminId) {
    const byId = storefront.filter((variant) => String(variant.id) === adminId);
    if (byId.length === 1) return { variant: byId[0], strategy: "ID" as const, ambiguous: false };
  }

  const sku = normalizedSku(admin.sku);
  if (sku) {
    const bySku = storefront.filter((variant) => normalizedSku(variant.sku) === sku);
    if (bySku.length === 1) return { variant: bySku[0], strategy: "SKU" as const, ambiguous: false };
    if (bySku.length > 1) return { variant: null, strategy: "SKU" as const, ambiguous: true };
  }

  const byTitle = storefront.filter((variant) => variant.title === admin.title);
  if (byTitle.length === 1) return { variant: byTitle[0], strategy: "TITLE" as const, ambiguous: false };
  if (byTitle.length > 1) return { variant: null, strategy: "TITLE" as const, ambiguous: true };

  return { variant: null, strategy: null, ambiguous: false };
}

export function adminPriceToCents(value: string) {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount < 0) return null;
  return Math.round(amount * 100);
}

function privateIpv4(address: string) {
  const octets = address.split(".").map(Number);
  if (octets.length !== 4 || octets.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return true;
  const [a, b] = octets;
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    a >= 224
  );
}

function privateIpv6(address: string) {
  const normalized = address.toLowerCase().replace(/^\[|\]$/g, "");
  return (
    normalized === "::" ||
    normalized === "::1" ||
    normalized.startsWith("fc") ||
    normalized.startsWith("fd") ||
    /^fe[89ab]/.test(normalized) ||
    normalized.startsWith("::ffff:127.") ||
    normalized.startsWith("::ffff:10.") ||
    normalized.startsWith("::ffff:192.168.") ||
    normalized.startsWith("::ffff:169.254.")
  );
}

export function isPrivateAddress(address: string) {
  const version = isIP(address.replace(/^\[|\]$/g, ""));
  if (version === 4) return privateIpv4(address);
  if (version === 6) return privateIpv6(address);
  return false;
}

export function validateStorefrontUrl(input: string) {
  const url = new URL(input);
  if (url.protocol !== "https:") throw new Error("Storefront URL must use HTTPS");
  if (url.username || url.password) throw new Error("Storefront URL must not contain credentials");
  if (url.port && url.port !== "443") throw new Error("Storefront URL must use the standard HTTPS port");

  const host = url.hostname.toLowerCase();
  if (
    host === "localhost" ||
    host.endsWith(".localhost") ||
    host === "metadata.google.internal" ||
    isPrivateAddress(host)
  ) {
    throw new Error("Storefront URL resolves to a disallowed host");
  }

  return url;
}

export function buildAjaxProductUrl(onlineStoreUrl: string) {
  const url = validateStorefrontUrl(onlineStoreUrl);
  url.search = "";
  url.hash = "";
  const path = url.pathname.replace(/\/+$/, "");
  url.pathname = path.endsWith(".js") ? path : `${path}.js`;
  return url;
}

function comparableHost(hostname: string) {
  return hostname.toLowerCase().replace(/^www\./, "");
}

export function isSameStorefrontHost(a: string, b: string) {
  return comparableHost(a) === comparableHost(b);
}
