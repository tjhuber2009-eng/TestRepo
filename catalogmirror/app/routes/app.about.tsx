export default function About() {
  return (
    <s-page heading="About CatalogMirror">
      <s-section heading="What it checks">
        <s-unordered-list>
          <s-list-item>Missing products or variants on the Online Store.</s-list-item>
          <s-list-item>Price mismatches between Shopify Admin and public product JSON.</s-list-item>
          <s-list-item>Availability mismatches using inventory tracking and inventory policy.</s-list-item>
          <s-list-item>Expected exclusions for products that have no Online Store URL.</s-list-item>
        </s-unordered-list>
      </s-section>
      <s-section heading="Data use">
        <s-paragraph>CatalogMirror requests read-only product and inventory access. It does not modify products, prices, inventory, orders, customers, or themes.</s-paragraph>
      </s-section>
      <s-section heading="Support">
        <s-paragraph>Support, privacy, and terms links should be set to your production support domain before App Store submission.</s-paragraph>
      </s-section>
    </s-page>
  );
}
