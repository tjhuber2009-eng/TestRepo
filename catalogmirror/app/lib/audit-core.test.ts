import assert from "node:assert/strict";
import test from "node:test";
import {
  adminPriceToCents,
  buildAjaxProductUrl,
  expectedAvailable,
  fingerprintIdentity,
  isPrivateAddress,
  isSameStorefrontHost,
  matchStorefrontVariant,
  shopifyNumericId,
  validateStorefrontUrl,
  type AdminVariantCore,
} from "./audit-core.ts";

const baseVariant: AdminVariantCore = {
  id: "gid://shopify/ProductVariant/123",
  title: "Large",
  sku: "SKU-L",
  price: "19.99",
  inventoryQuantity: 4,
  inventoryPolicy: "DENY",
  inventoryItem: { tracked: true },
};

test("incident identity remains stable when observed values change", () => {
  assert.equal(
    fingerprintIdentity({ productId: "p1", variantId: "v1", kind: "PRICE_MISMATCH" }),
    fingerprintIdentity({ productId: "p1", variantId: "v1", kind: "PRICE_MISMATCH" }),
  );
});

test("availability respects tracking, continue-selling, and inventory", () => {
  assert.equal(expectedAvailable(baseVariant), true);
  assert.equal(expectedAvailable({ ...baseVariant, inventoryQuantity: 0 }), false);
  assert.equal(expectedAvailable({ ...baseVariant, inventoryQuantity: 0, inventoryPolicy: "CONTINUE" }), true);
  assert.equal(expectedAvailable({ ...baseVariant, inventoryItem: { tracked: false }, inventoryQuantity: 0 }), true);
});

test("variant matching prefers the Shopify variant ID", () => {
  const match = matchStorefrontVariant(baseVariant, [
    { id: 123, title: "Wrong title", sku: "OTHER", price: 1999, available: true },
  ]);
  assert.equal(match.strategy, "ID");
  assert.equal(String(match.variant?.id), "123");
});

test("duplicate SKU fallback is treated as ambiguous", () => {
  const match = matchStorefrontVariant(
    { ...baseVariant, id: "gid://shopify/ProductVariant/999" },
    [
      { id: 1, title: "One", sku: "SKU-L", price: 1999, available: true },
      { id: 2, title: "Two", sku: "SKU-L", price: 1999, available: true },
    ],
  );
  assert.equal(match.ambiguous, true);
  assert.equal(match.variant, null);
});

test("locale-aware Online Store paths are preserved for Ajax product JSON", () => {
  assert.equal(
    buildAjaxProductUrl("https://example.com/en-ca/products/red-shirt?variant=123").toString(),
    "https://example.com/en-ca/products/red-shirt.js",
  );
});

test("unsafe storefront targets are rejected", () => {
  assert.throws(() => validateStorefrontUrl("http://example.com/products/x"));
  assert.throws(() => validateStorefrontUrl("https://127.0.0.1/products/x"));
  assert.throws(() => validateStorefrontUrl("https://169.254.169.254/latest/meta-data"));
  assert.equal(isPrivateAddress("10.0.0.1"), true);
  assert.equal(isPrivateAddress("8.8.8.8"), false);
});

test("www and apex hostnames can be treated as the same storefront", () => {
  assert.equal(isSameStorefrontHost("www.example.com", "example.com"), true);
  assert.equal(isSameStorefrontHost("example.com", "other.example.com"), false);
});

test("Shopify GIDs and decimal prices normalize predictably", () => {
  assert.equal(shopifyNumericId("gid://shopify/ProductVariant/123456"), "123456");
  assert.equal(adminPriceToCents("19.99"), 1999);
  assert.equal(adminPriceToCents("not-a-price"), null);
});
