export type AutoAuditResourceType = "PRODUCT" | "INVENTORY_ITEM" | "SHOP" | "RECONCILE";

export function autoAuditDebounceMs(raw = process.env.AUTO_AUDIT_DEBOUNCE_SECONDS) {
  const seconds = Number(raw ?? 20);
  const clamped = Number.isFinite(seconds) ? Math.max(5, Math.min(Math.trunc(seconds), 300)) : 20;
  return clamped * 1000;
}

export function autoAuditPollMs(raw = process.env.AUTO_AUDIT_POLL_MS) {
  const value = Number(raw ?? 10000);
  return Number.isFinite(value) ? Math.max(1000, Math.min(Math.trunc(value), 60000)) : 10000;
}

export function autoAuditBackoffMs(attempts: number) {
  const exponent = Math.max(0, Math.min(Math.trunc(attempts), 8));
  return Math.min(15 * 60_000, 15_000 * 2 ** exponent);
}

export function productGidFromPayload(payload: Record<string, unknown>) {
  const adminGid = payload.admin_graphql_api_id;
  if (typeof adminGid === "string" && /^gid:\/\/shopify\/Product\/\d+$/.test(adminGid)) {
    return adminGid;
  }

  const id = payload.id;
  if (typeof id === "number" && Number.isSafeInteger(id) && id >= 0) {
    return `gid://shopify/Product/${id}`;
  }
  if (typeof id === "string" && /^\d+$/.test(id)) {
    return `gid://shopify/Product/${id}`;
  }
  return null;
}

export function inventoryItemGidFromPayload(payload: Record<string, unknown>) {
  const adminGid = payload.admin_graphql_api_id;
  if (typeof adminGid === "string") {
    const queryMatch = adminGid.match(/[?&]inventory_item_id=(\d+)/);
    if (queryMatch) return `gid://shopify/InventoryItem/${queryMatch[1]}`;
    if (/^gid:\/\/shopify\/InventoryItem\/\d+$/.test(adminGid)) return adminGid;
  }

  const direct = payload.inventory_item_id;
  if (typeof direct === "number" && Number.isSafeInteger(direct) && direct >= 0) {
    return `gid://shopify/InventoryItem/${direct}`;
  }
  if (typeof direct === "string" && /^\d+$/.test(direct)) {
    return `gid://shopify/InventoryItem/${direct}`;
  }

  return null;
}

export function isAutoAuditEnabled(env = process.env) {
  if (env.AUTO_AUDIT_ENABLED === "false") return false;
  if (env.AUTO_AUDIT_ENABLED === "true") return true;
  return env.NODE_ENV === "production";
}

export function autoAuditTaskPriority(type: AutoAuditResourceType) {
  if (type === "PRODUCT" || type === "INVENTORY_ITEM") return 100;
  if (type === "SHOP") return 80;
  return 5;
}
