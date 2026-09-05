export function loader() {
  return Response.json({ ok: true, app: "catalogmirror", version: 1, time: new Date().toISOString() });
}
