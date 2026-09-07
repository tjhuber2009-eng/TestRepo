export class AsyncSemaphore {
  constructor(limit = 1) {
    this.limit = Math.max(1, Math.trunc(Number(limit) || 1));
    this.active = 0;
    this.queue = [];
  }
  get pending() { return this.queue.length; }
  async acquire() {
    if (this.active < this.limit) {
      this.active += 1;
      return this.#releaseFactory();
    }
    return new Promise((resolve) => this.queue.push(resolve)).then(() => {
      this.active += 1;
      return this.#releaseFactory();
    });
  }
  async run(fn) {
    const release = await this.acquire();
    try { return await fn(); }
    finally { release(); }
  }
  #releaseFactory() {
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.active = Math.max(0, this.active - 1);
      const next = this.queue.shift();
      if (next) next();
    };
  }
}
