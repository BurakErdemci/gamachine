// One Durable Object per client IP. A per-IP count has to span all pairing
// ids, so it needs its own object; Workers' built-in rate limiting only offers
// 10 s / 60 s windows, which cannot express "per hour". Buckets:
//   pair - pairing attempts, 20 per hour (docs/remote-control.md "Pairing" step 5)
//   room - new rooms opened by a PC, 10 per hour. Without it one caller could
//          create unbounded persistent rooms (Codex relayaudit, 28 Sep 2026).

export const IP_LIMITS = {
  pair: { max: 20, key: 'hits' },
  room: { max: 10, key: 'rooms' },
};
export const IP_WINDOW_MS = 3_600_000;

export class IpLimiter {
  constructor(ctx) {
    this.ctx = ctx;
  }

  async fetch(request) {
    const bucket = IP_LIMITS[new URL(request.url).pathname.slice(1)];
    if (!bucket) return new Response('unknown bucket', { status: 404 });
    const now = Date.now();
    const hits = ((await this.ctx.storage.get(bucket.key)) ?? []).filter((t) => now - t < IP_WINDOW_MS);
    if (hits.length >= bucket.max) return new Response('limited', { status: 429 });
    hits.push(now);
    await this.ctx.storage.put(bucket.key, hits);
    // Wipe the record an hour after the last hit so idle IPs leave nothing behind.
    await this.ctx.storage.setAlarm(now + IP_WINDOW_MS);
    return new Response(null, { status: 204 });
  }

  async alarm() {
    await this.ctx.storage.deleteAll();
  }
}
