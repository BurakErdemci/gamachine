// Minimal in-process stand-in for the Workers runtime pieces the relay uses:
// WebSocketPair, 101 responses, Durable Object storage/alarms, the
// hibernation socket API and namespace stubs. Semantics follow the Cloudflare
// docs; anything the relay does not touch is left out on purpose.

const OriginalResponse = globalThis.Response;

export class FakeSocket {
  constructor() {
    this.sent = [];
    this.readyState = 1;
    this.closed = null;
    this.attachment = null;
    this.tags = [];
  }
  send(data) {
    if (this.readyState !== 1) throw new Error('socket closed');
    this.sent.push(data);
  }
  close(code, reason) {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.closed = { code, reason };
  }
  serializeAttachment(v) {
    this.attachment = structuredClone(v);
  }
  deserializeAttachment() {
    return structuredClone(this.attachment);
  }
  // Test helpers: what the remote end has received.
  json() {
    return this.sent.map((s) => JSON.parse(s));
  }
  last() {
    return JSON.parse(this.sent[this.sent.length - 1]);
  }
  take() {
    const out = this.json();
    this.sent = [];
    return out;
  }
}

export function installRuntimeGlobals() {
  globalThis.WebSocketPair = function WebSocketPair() {
    const client = new FakeSocket();
    const server = new FakeSocket();
    // Tests hold the client and drive the server end the object accepted.
    client.peer = server;
    return { 0: client, 1: server };
  };
  // Node's Response rejects status 101; the runtime allows it with a webSocket.
  globalThis.Response = class extends OriginalResponse {
    constructor(body, init) {
      if (init && init.status === 101) {
        return { status: 101, webSocket: init.webSocket, headers: new Headers(init.headers) };
      }
      super(body, init);
    }
    static redirect(url, status) {
      return OriginalResponse.redirect(url, status);
    }
  };
}

export class FakeStorage {
  constructor() {
    this.map = new Map();
    this.alarm = null;
  }
  async get(key) {
    if (Array.isArray(key)) {
      const out = new Map();
      for (const k of key) if (this.map.has(k)) out.set(k, structuredClone(this.map.get(k)));
      return out;
    }
    return this.map.has(key) ? structuredClone(this.map.get(key)) : undefined;
  }
  async put(key, value) {
    this.map.set(key, structuredClone(value));
  }
  async delete(key) {
    return this.map.delete(key);
  }
  async deleteAll() {
    this.map.clear();
    this.alarm = null;
  }
  async getAlarm() {
    return this.alarm;
  }
  async setAlarm(t) {
    this.alarm = t;
  }
  async deleteAlarm() {
    this.alarm = null;
  }
}

export class FakeState {
  constructor() {
    this.storage = new FakeStorage();
    this.accepted = [];
  }
  acceptWebSocket(ws, tags = []) {
    ws.tags = tags;
    this.accepted.push(ws);
  }
  getWebSockets(tag) {
    this.accepted = this.accepted.filter((s) => s.readyState !== 3);
    return this.accepted.filter((s) => tag === undefined || s.tags.includes(tag));
  }
  setWebSocketAutoResponse() {}
}

// Namespace whose stubs call straight into the object. `instances` keeps one
// FakeState per name so a test can rebuild the object (simulated hibernation).
export class FakeNamespace {
  constructor(Klass, env) {
    this.Klass = Klass;
    this.env = env;
    this.states = new Map();
    this.objects = new Map();
  }
  idFromName(name) {
    return name;
  }
  get(id) {
    return {
      fetch: (input, init) => {
        const req = input instanceof Request ? input : new Request(input, init);
        return this.object(id).fetch(req);
      },
    };
  }
  state(id) {
    if (!this.states.has(id)) this.states.set(id, new FakeState());
    return this.states.get(id);
  }
  object(id) {
    if (!this.objects.has(id)) this.objects.set(id, new this.Klass(this.state(id), this.env));
    return this.objects.get(id);
  }
  hibernate(id) {
    this.objects.delete(id);
  }
}
