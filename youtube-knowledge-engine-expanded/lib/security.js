import { safeEqual, envInt, parseBool } from './util.js';
import { YoutubeError } from './youtube.js';

const buckets = new Map();
const WINDOW_MS = 60 * 60_000;
const MAX_BUCKETS = 100_000;
export function clientIp(req) {
  if (parseBool(process.env.TRUST_PROXY, false)) return String(req.headers['x-forwarded-for'] || '').split(',')[0].trim() || req.socket?.remoteAddress || 'unknown';
  return req.socket?.remoteAddress || 'unknown';
}
export function rateLimit(req, bucket, limit) {
  const key = `${bucket}:${clientIp(req)}`; const now = Date.now();
  let item = buckets.get(key);
  if (!item && buckets.size >= MAX_BUCKETS) { pruneRateLimits(); if (buckets.size >= MAX_BUCKETS) buckets.delete(buckets.keys().next().value); }
  if (!item || item.resetAt <= now) item = { count: 0, resetAt: now + WINDOW_MS };
  item.count += 1; buckets.set(key, item);
  if (item.count > limit) throw new YoutubeError('Rate limit exceeded. Try again later.', 'RATE_LIMITED', 429, { resetAt: item.resetAt });
  return item;
}
export function pruneRateLimits() { const now = Date.now(); for (const [k, v] of buckets) if (v.resetAt <= now) buckets.delete(k); }
export function assertJson(req) {
  const type = String(req.headers['content-type'] || '').split(';')[0].trim().toLowerCase();
  if (req.method === 'POST' && type !== 'application/json') throw new YoutubeError('POST requests must use application/json.', 'UNSUPPORTED_MEDIA_TYPE', 415);
}
function hostName(value) { try { return new URL(`http://${String(value || '')}`).hostname.toLowerCase().replace(/^\[|\]$/g, ''); } catch { return ''; } }
function hostPort(value) { try { return new URL(`http://${String(value || '')}`).host.toLowerCase(); } catch { return ''; } }
export function assertSafeOrigin(req) {
  const origin = String(req.headers.origin || '');
  const secFetchSite = String(req.headers['sec-fetch-site'] || '');
  const host = String(req.headers.host || '');
  if (secFetchSite && !['same-origin', 'same-site', 'none'].includes(secFetchSite)) throw new YoutubeError('Cross-site requests are blocked.', 'CROSS_SITE_BLOCKED', 403);
  if (origin) {
    let u; try { u = new URL(origin); } catch { throw new YoutubeError('Invalid Origin header.', 'BAD_ORIGIN', 403); }
    const expectedHost = hostPort(host);
    if (u.host.toLowerCase() !== expectedHost) throw new YoutubeError('Cross-origin requests are blocked.', 'CROSS_ORIGIN_BLOCKED', 403);
  }
}
export function assertHost(req) {
  const host = hostName(req.headers.host);
  if (!host) throw new YoutubeError('Invalid Host header.', 'BAD_HOST', 421);
  const allowed = String(process.env.ALLOWED_HOSTS || '').split(',').map(x => x.trim().toLowerCase()).filter(Boolean);
  if (allowed.length && !allowed.includes(host)) throw new YoutubeError('Host is not allowed.', 'BAD_HOST', 421);
  const local = ['127.0.0.1', 'localhost', '::1'];
  const bind = String(process.env.HOST || '').toLowerCase();
  if (bind === '127.0.0.1' || bind === 'localhost' || bind === '::1' || !process.env.HOST) {
    if (!local.includes(host)) throw new YoutubeError('Invalid Host header.', 'BAD_HOST', 421);
  }
}

function isLoopbackAddress(value='') {
  const addr=String(value||'').toLowerCase();
  return addr==='127.0.0.1'||addr==='::1'||addr.startsWith('::ffff:127.');
}
export function monitorAllowed(req, suppliedToken) {
  const configured = process.env.MONITOR_ACCESS_TOKEN || '';
  if (configured) return safeEqual(suppliedToken, configured);
  // Monitoring mutates durable server state and can trigger unattended network work.
  // Tokenless access is therefore limited to direct local development only.
  if (process.env.NODE_ENV === 'production' || parseBool(process.env.TRUST_PROXY, false)) return false;
  return isLoopbackAddress(req.socket?.remoteAddress) && isLoopbackAddress(req.socket?.localAddress);
}

export function aiAllowed(req, suppliedToken) {
  const configured = process.env.AI_ACCESS_TOKEN || '';
  if (configured) return safeEqual(suppliedToken, configured);
  if (parseBool(process.env.AI_PUBLIC_FALLBACK, false)) return true;
  // Automatic tokenless access is intentionally limited to direct local development.
  // A public reverse proxy commonly connects to the app over loopback; checking only
  // socket.localAddress would therefore mistake remote users for local users.
  if (process.env.NODE_ENV === 'production' || parseBool(process.env.TRUST_PROXY, false)) return false;
  return isLoopbackAddress(req.socket?.remoteAddress) && isLoopbackAddress(req.socket?.localAddress);
}
export function securityHeaders({ isHttps = false } = {}) {
  return {
    'content-security-policy': "default-src 'self'; img-src 'self' https://i.ytimg.com https://yt3.ggpht.com data:; style-src 'self'; script-src 'self'; worker-src 'self'; manifest-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
    'x-content-type-options': 'nosniff',
    'x-frame-options': 'DENY',
    'referrer-policy': 'no-referrer',
    'permissions-policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
    'cross-origin-opener-policy': 'same-origin',
    'cross-origin-resource-policy': 'same-origin',
    'origin-agent-cluster': '?1',
    ...(isHttps ? { 'strict-transport-security': 'max-age=31536000; includeSubDomains' } : {}),
  };
}
export const limits = {
  discover: envInt('DISCOVER_RATE_LIMIT_PER_HOUR', 30, 1, 10000),
  transcript: envInt('TRANSCRIPT_RATE_LIMIT_PER_HOUR', 1200, 1, 100000),
  comments: envInt('COMMENTS_RATE_LIMIT_PER_HOUR', 120, 1, 10000),
  ai: envInt('AI_RATE_LIMIT_PER_HOUR', 12, 1, 10000),
  research: envInt('RESEARCH_RATE_LIMIT_PER_HOUR', 60, 1, 10000),
  embedding: envInt('EMBEDDING_RATE_LIMIT_PER_HOUR', 120, 1, 10000),
  visual: envInt('VISUAL_RATE_LIMIT_PER_HOUR', 24, 1, 10000),
  monitor: envInt('MONITOR_RATE_LIMIT_PER_HOUR', 240, 1, 10000),
};
