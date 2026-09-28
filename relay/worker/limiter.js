// One Durable Object per client IP, used only for pairing attempts
// (20 per hour, docs/remote-control.md "Pairing" step 5). The per-pairing-id
// limit lives in Room; a per-IP count has to span all pairing ids, so it needs
// its own object. Workers' built-in rate limiting only offers 10 s / 60 s
// windows, which cannot express "per hour".

export const IP_LIMIT = { perHour: 20, windowMs: 3_600_000 };

export class IpLimiter {
  constructor(ctx) {
    this.ctx = ctx;
  }

  async fetch() {
    const now = Date.now();
    const hits = ((await this.ctx.storage.get('hits')) ?? []).filter((t) => now - t < IP_LIMIT.windowMs);
    if (hits.length >= IP_LIMIT.perHour) return new Response('limited', { status: 429 });
    hits.push(now);
    await this.ctx.storage.put('hits', hits);
    // Wipe the record an hour after the last attempt so idle IPs leave nothing behind.
    await this.ctx.storage.setAlarm(now + IP_LIMIT.windowMs);
    return new Response(null, { status: 204 });
  }

  async alarm() {
    await this.ctx.storage.deleteAll();
  }
}
