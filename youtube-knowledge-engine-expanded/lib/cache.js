export class LruTtlCache {
  constructor({ max = 500, ttlMs = 30 * 60_000 } = {}) {
    this.max = max;
    this.ttlMs = ttlMs;
    this.map = new Map();
    this.inflight = new Map();
  }
  get(key) {
    const item = this.map.get(key);
    if (!item) return undefined;
    if (item.expiresAt < Date.now()) { this.map.delete(key); return undefined; }
    this.map.delete(key); this.map.set(key, item);
    return item.value;
  }
  set(key, value, ttlMs = this.ttlMs) {
    this.map.delete(key);
    this.map.set(key, { value, expiresAt: Date.now() + ttlMs });
    while (this.map.size > this.max) this.map.delete(this.map.keys().next().value);
    return value;
  }
  async getOrCreate(key, factory, { ttlMs = this.ttlMs, cache = (value) => value != null } = {}) {
    const hit = this.get(key);
    if (hit !== undefined) return { value: hit, cached: true };
    if (this.inflight.has(key)) return { value: await this.inflight.get(key), cached: true, shared: true };
    const p = Promise.resolve().then(factory).then((value) => { if (cache(value)) this.set(key, value, ttlMs); return value; }).finally(() => this.inflight.delete(key));
    this.inflight.set(key, p);
    return { value: await p, cached: false };
  }
  clear() { this.map.clear(); }
  stats() { return { size: this.map.size, inflight: this.inflight.size, max: this.max, ttlMs: this.ttlMs }; }
}
