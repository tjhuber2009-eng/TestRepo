export function loader() {
  return new Response("User-agent: *\nDisallow: /app\nDisallow: /auth\nDisallow: /webhooks\n", {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=86400",
    },
  });
}
