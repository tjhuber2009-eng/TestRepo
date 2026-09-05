export function loader() {
  return Response.json(
    {
      ok: true,
      app: "catalogmirror",
      build: process.env.RAILWAY_GIT_COMMIT_SHA?.slice(0, 12) || process.env.GIT_COMMIT_SHA?.slice(0, 12) || "unknown",
      uptimeSeconds: Math.round(process.uptime()),
      time: new Date().toISOString(),
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
