import { useState } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData } from "react-router";
import { login } from "../../shopify.server";
import { loginErrorMessage } from "./error.server";

export const loader = async ({ request }: LoaderFunctionArgs) => ({
  errors: loginErrorMessage(await login(request)),
});

export const action = async ({ request }: ActionFunctionArgs) => ({
  errors: loginErrorMessage(await login(request)),
});

export default function Auth() {
  const loaderData = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>();
  const [shop, setShop] = useState("");
  const { errors } = actionData || loaderData;

  return (
    <main
      style={{
        maxWidth: 480,
        margin: "64px auto",
        padding: 24,
        fontFamily: "Inter, system-ui, sans-serif",
      }}
    >
      <h1>CatalogMirror</h1>
      <p>Enter your Shopify store domain to continue.</p>
      <Form method="post">
        <div style={{ display: "grid", gap: 12 }}>
          <label htmlFor="shop">Shop domain</label>
          <input
            id="shop"
            name="shop"
            type="text"
            inputMode="url"
            autoComplete="url"
            placeholder="example.myshopify.com"
            value={shop}
            onChange={(event) => setShop(event.currentTarget.value)}
            aria-invalid={Boolean(errors.shop)}
            aria-describedby={errors.shop ? "shop-error" : "shop-help"}
            required
          />
          {errors.shop ? (
            <p id="shop-error" role="alert">{errors.shop}</p>
          ) : (
            <p id="shop-help">For example: example.myshopify.com</p>
          )}
          <button type="submit">Log in</button>
        </div>
      </Form>
    </main>
  );
}
