// Runs ONE model-written script inside a QuickJS-wasm sandbox and exits.
// Protocol (line-delimited JSON, see README.md):
//   stdin  line 1   {"type":"start","code":str,"tools":[str],"limits":{...}}
//   stdin  later    {"type":"result","id":n,"ok":true,"value":any}
//                   {"type":"result","id":n,"ok":false,"error":str}
//   stdout          {"type":"call","id":n,"tool":str,"args":obj}
//                   {"type":"log","text":str}
//                   {"type":"done","value":any,"undefined":bool}
//                   {"type":"error","kind":str,"message":str}
// The script sees only `tools`, `callTool` and `console`; everything else it could
// act through lives in this host process, which the VM cannot reach.
import { createInterface } from "node:readline";
import { newQuickJSWASMModuleFromVariant } from "./vendor/quickjs-emscripten-core/dist/index.mjs";

const variant = {
  type: "sync",
  importFFI: () => import("./vendor/quickjs-wasmfile-release-sync/dist/ffi.mjs").then((m) => m.QuickJSFFI),
  importModuleLoader: () =>
    import("./vendor/quickjs-wasmfile-release-sync/dist/emscripten-module.mjs").then((m) => m.default),
};

const DEFAULTS = {
  wallMs: 600_000,
  memoryBytes: 64 * 1024 * 1024,
  // Measured 25 Nov 2026 (Node 20.19 and 25.2): at 512 KB V8's own wasm stack
  // overflowed first and killed the host; at 256 KB QuickJS stops the recursion itself.
  stackBytes: 256 * 1024,
  callTimeoutMs: 300_000,
  maxCalls: 200,
  maxLogBytes: 1024 * 1024,
};

let finished = false;

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function finish(obj, code = 0) {
  if (finished) return;
  finished = true;
  // Exit only after the line is flushed: on Windows pipe writes are async.
  process.stdout.write(JSON.stringify(obj) + "\n", () => process.exit(code));
}

function fail(kind, message, extra = {}) {
  finish({ type: "error", kind, message: String(message), ...extra }, 0);
}

process.on("uncaughtException", (e) => {
  if (e instanceof RangeError && /call stack/i.test(e.message)) fail("memory", "stack overflow (too deep recursion)");
  else fail("internal", e?.stack || e);
});
process.on("unhandledRejection", (e) => fail("internal", e?.stack || e));

function describe(dumped) {
  if (dumped && typeof dumped === "object") {
    const name = dumped.name || "Error";
    const msg = dumped.message ?? JSON.stringify(dumped);
    const stack = typeof dumped.stack === "string" && dumped.stack.trim() ? "\n" + dumped.stack.trim() : "";
    return { name, text: `${name}: ${msg}${stack}` };
  }
  return { name: "Error", text: `Uncaught: ${typeof dumped === "string" ? dumped : JSON.stringify(dumped)}` };
}

function prelude(names) {
  return `(() => {
  const call = globalThis.__gm_call, log = globalThis.__gm_log;
  delete globalThis.__gm_call; delete globalThis.__gm_log;
  const NAMES = ${JSON.stringify(names)};
  const fmt = (v) => {
    if (typeof v === "string") return v;
    try { const s = JSON.stringify(v); return s === undefined ? String(v) : s; } catch (e) { return String(v); }
  };
  const out = (...a) => { log(a.map(fmt).join(" ")); };
  globalThis.console = Object.freeze({ log: out, info: out, warn: out, error: out, debug: out });
  const invoke = async (name, args) => {
    if (args === undefined) args = {};
    if (args === null || typeof args !== "object" || Array.isArray(args))
      throw new TypeError("tools." + name + "(args): args must be an object");
    return JSON.parse(await call(name, JSON.stringify(args)));
  };
  const table = {};
  for (const n of NAMES) table[n] = (args) => invoke(n, args);
  Object.freeze(table);
  const missing = (n) => new TypeError('no tool named "' + n + '"; available: ' +
    (NAMES.length ? NAMES.join(", ").slice(0, 1500) : "none"));
  globalThis.tools = new Proxy(table, {
    get(t, k) {
      if (typeof k !== "string" || k in t || k === "then" || k === "toJSON") return t[k];
      throw missing(k);
    },
  });
  globalThis.callTool = (n, args) => {
    if (!NAMES.includes(n)) return Promise.reject(missing(n));
    return invoke(n, args);
  };
})();`;
}

async function main(job) {
  const limits = { ...DEFAULTS, ...(job.limits || {}) };
  const names = Array.isArray(job.tools) ? job.tools.filter((n) => typeof n === "string") : [];
  const QJS = await newQuickJSWASMModuleFromVariant(variant);
  const rt = QJS.newRuntime();
  rt.setMemoryLimit(limits.memoryBytes);
  rt.setMaxStackSize(limits.stackBytes);
  const deadline = Date.now() + limits.wallMs;
  let interrupted = false;
  rt.setInterruptHandler(() => {
    if (Date.now() >= deadline) interrupted = true;
    return interrupted;
  });
  // The interrupt only fires while JS runs; a script parked on a tool call is
  // ended by this timer instead.
  setTimeout(() => fail("timeout", `script ran past its ${limits.wallMs / 1000} s limit`), limits.wallMs).unref();

  const vm = rt.newContext();
  const pending = new Map();
  let calls = 0;
  let logBytes = 0;
  let logCut = false;

  const classify = (dumped, stage) => {
    const { name, text } = describe(dumped);
    if (interrupted || /\binterrupted\b/.test(text)) return ["timeout", `script ran past its ${limits.wallMs / 1000} s limit`];
    if (/out of memory/i.test(text)) return ["memory", `script ran out of memory (limit ${limits.memoryBytes} bytes)`];
    if (/stack overflow/i.test(text)) return ["memory", "stack overflow (too deep recursion)"];
    if (stage === "compile" && name === "SyntaxError") return ["syntax", text];
    return ["thrown", text];
  };

  const pump = () => {
    const r = rt.executePendingJobs();
    if (r.error) {
      const d = vm.dump(r.error);
      r.error.dispose();
      fail(...classify(d, "run"));
    }
  };

  const hostCall = vm.newFunction("__gm_call", (nameH, argsH) => {
    const tool = vm.getString(nameH);
    let args;
    try {
      args = JSON.parse(vm.getString(argsH));
    } catch (e) {
      args = {};
    }
    calls += 1;
    if (calls > limits.maxCalls) {
      fail("call_limit", `script made more than ${limits.maxCalls} tool calls; stopped before call ${calls} (${tool})`);
      return vm.newPromise().handle;
    }
    const id = calls;
    const deferred = vm.newPromise();
    const timer = setTimeout(
      () => fail("call_timeout", `tools.${tool} (call ${id}) did not answer within ${limits.callTimeoutMs / 1000} s`, { callId: id }),
      limits.callTimeoutMs,
    );
    pending.set(id, { deferred, timer });
    emit({ type: "call", id, tool, args });
    return deferred.handle;
  });
  vm.setProp(vm.global, "__gm_call", hostCall);
  hostCall.dispose();

  const hostLog = vm.newFunction("__gm_log", (textH) => {
    if (logCut) return;
    const text = vm.getString(textH);
    logBytes += Buffer.byteLength(text) + 1;
    if (logBytes > limits.maxLogBytes) {
      logCut = true;
      emit({ type: "log", text: `[console output past ${limits.maxLogBytes} bytes dropped]` });
      return;
    }
    emit({ type: "log", text });
  });
  vm.setProp(vm.global, "__gm_log", hostLog);
  hostLog.dispose();

  const pre = vm.evalCode(prelude(names), "prelude.js");
  if (pre.error) {
    const d = vm.dump(pre.error);
    pre.error.dispose();
    fail("internal", describe(d).text);
    return;
  }
  pre.value.dispose();

  const onResult = (msg) => {
    const entry = pending.get(msg.id);
    if (!entry || finished) return;
    pending.delete(msg.id);
    clearTimeout(entry.timer);
    if (msg.ok) {
      const h = vm.newString(JSON.stringify(msg.value === undefined ? null : msg.value));
      entry.deferred.resolve(h);
      h.dispose();
    } else {
      const e = vm.newError(String(msg.error || "tool call failed"));
      entry.deferred.reject(e);
      e.dispose();
    }
    pump();
  };

  // Line 1 of the script stays line 1 in error positions: the wrapper opens on it.
  const wrapped =
    "(async () => { const __gm_v = await (async () => {" +
    String(job.code ?? "") +
    "\n})(); return __gm_v === undefined ? undefined : JSON.stringify(__gm_v); })()";
  const res = vm.evalCode(wrapped, "script.js");
  if (res.error) {
    const d = vm.dump(res.error);
    res.error.dispose();
    fail(...classify(d, "compile"));
    return onResult;
  }
  const settled = vm.resolvePromise(res.value);
  res.value.dispose();
  settled.then((out) => {
    if (finished) return;
    if (out.error) {
      const d = vm.dump(out.error);
      out.error.dispose();
      fail(...classify(d, "run"));
      return;
    }
    const text = vm.dump(out.value);
    out.value.dispose();
    if (text === undefined) finish({ type: "done", value: null, undefined: true });
    else finish({ type: "done", value: JSON.parse(text), undefined: false });
  });
  pump();
  return onResult;
}

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
let onResult = null;
const early = [];
rl.on("line", (line) => {
  if (!line.trim()) return;
  let msg;
  try {
    msg = JSON.parse(line);
  } catch (e) {
    fail("protocol", "stdin line is not JSON");
    return;
  }
  if (msg.type === "start" && onResult === null && early.length === 0) {
    early.push(msg);
    main(msg).then(
      (handler) => {
        onResult = handler || (() => {});
        for (const m of early.splice(1)) onResult(m);
      },
      (e) => fail("internal", e?.stack || e),
    );
  } else if (msg.type === "result") {
    if (onResult) onResult(msg);
    else early.push(msg);
  }
});
// The parent is gone (or closed the pipe): nothing may outlive it.
rl.on("close", () => {
  if (!finished) process.exit(3);
});
