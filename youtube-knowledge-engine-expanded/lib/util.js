import { createHash, timingSafeEqual } from 'node:crypto';

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export const clamp = (n, min, max) => Math.max(min, Math.min(max, Number(n)));
export const sha256 = (value) => createHash('sha256').update(value).digest('hex');
export function safeEqual(a, b) {
  const aa = Buffer.from(String(a || '')); const bb = Buffer.from(String(b || ''));
  return aa.length === bb.length && timingSafeEqual(aa, bb);
}
export function parseBool(value, fallback = false) {
  if (value == null || value === '') return fallback;
  return /^(1|true|yes|on)$/i.test(String(value));
}
export function envInt(name, fallback, min = 0, max = Number.MAX_SAFE_INTEGER) {
  const n = Number(process.env[name]); return Number.isFinite(n) ? clamp(n, min, max) : fallback;
}
export function validVideoId(id) { return /^[A-Za-z0-9_-]{11}$/.test(String(id || '')); }
export function validLanguage(value) { return value === '' || /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$/.test(String(value || '')); }
export function normalizeSpaces(s) { return String(s || '').replace(/\s+/g, ' ').trim(); }
export function errorToObject(error) { return { name: error?.name, message: error?.message, code: error?.code, status: error?.status }; }

export async function readResponseTextLimited(response, maxBytes = 8 * 1024 * 1024) {
  const limit = Math.max(1024, Math.trunc(Number(maxBytes) || 0));
  const declared = Number(response?.headers?.get?.('content-length') || 0);
  if (declared > limit) { const e = new Error(`Response body exceeds ${limit} bytes.`); e.code = 'RESPONSE_TOO_LARGE'; throw e; }
  if (!response?.body?.getReader) {
    const text = await response.text();
    if (Buffer.byteLength(text, 'utf8') > limit) { const e = new Error(`Response body exceeds ${limit} bytes.`); e.code = 'RESPONSE_TOO_LARGE'; throw e; }
    return text;
  }
  const reader = response.body.getReader(); const chunks=[]; let total=0;
  try {
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      total += value.byteLength;
      if (total > limit) { try { await reader.cancel(); } catch {} const e = new Error(`Response body exceeds ${limit} bytes.`); e.code = 'RESPONSE_TOO_LARGE'; throw e; }
      chunks.push(value);
    }
  } finally { try { reader.releaseLock(); } catch {} }
  const out = new Uint8Array(total); let offset=0; for (const chunk of chunks) { out.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder().decode(out);
}
