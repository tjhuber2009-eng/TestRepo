export default function About() {
  return (
    <s-page heading="About CatalogMirror">
      <s-section heading="What it checks">
        <s-unordered-list>
          <s-list-item>Missing Online Store products and variants.</s-list-item>
          <s-list-item>Price mismatches between Shopify Admin and public Ajax product JSON.</s-list-item>
          <s-list-item>Availability mismatches using inventory tracking, quantity, and inventory policy.</s-list-item>
          <s-list-item>Unexpected storefront variants and product identity mismatches.</s-list-item>
          <s-list-item>Expected exclusions for products not published to the Online Store.</s-list-item>
        </s-unordered-list>
      </s-section>

      <s-section heading="Safety and accuracy">
        <s-paragraph>
          CatalogMirror is read-only. It preserves locale-aware Online Store URLs, retries transient failures, blocks unsafe storefront fetch targets, deduplicates Shopify webhooks, prevents overlapping audits, and only auto-resolves findings for products that were successfully re-verified.
        </s-paragraph>
        <s-paragraph>
          Shopify's Ajax Product API returns at most 250 variants per product. For larger products, CatalogMirror reports a coverage warning instead of treating variants beyond that limit as missing or falsely resolving older findings.
        </s-paragraph>
        <s-paragraph>
          Partial catalog audits are also conservative: findings for products outside the scanned slice remain unchanged.
        </s-paragraph>
      </s-section>

      <s-section heading="Automatic monitoring">
        <s-paragraph>
          Product and inventory webhooks are stored as durable, debounced verification tasks. Product changes are rechecked directly; inventory changes are mapped back to their Shopify product before verification. Queue generation checks preserve newer webhooks that arrive while an older audit is running, and renewable leases let another process recover work after a crash.
        </s-paragraph>
      </s-section>

      <s-section heading="Missed-webhook reconciliation">
        <s-paragraph>
          CatalogMirror periodically asks Shopify for products updated since the last successful reconciliation. Large discovery runs use Shopify Bulk Operations, stream the resulting JSONL file without loading it all into memory, and enqueue low-priority targeted checks so live webhook changes remain first in line.
        </s-paragraph>
      </s-section>

      <s-section heading="Data use">
        <s-paragraph>
          CatalogMirror requests read-only product and inventory access. It does not modify products, prices, inventory, orders, customers, themes, or storefront code. Customer privacy webhooks are authenticated; no customer records are stored by the app.
        </s-paragraph>
      </s-section>

      <s-section heading="Support">
        <s-paragraph>
          Support, privacy, and terms links must be configured on the production App Store listing before submission.
        </s-paragraph>
      </s-section>
    </s-page>
  );
}
