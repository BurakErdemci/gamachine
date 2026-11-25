# script_runner — the sandbox behind `run_script`

`runner.mjs` runs one model-written JavaScript program inside QuickJS compiled to WebAssembly
(quickjs-emscripten 0.32.0, release-sync variant) and exits. The Python side is
`Backend/app/script_tool.py`; it starts one runner per `run_script` call and kills it when the
call ends, times out or is cancelled.

## Runtime

Electron's own Node (`GAMACHINE_NODE_PATH`, set by `Frontend/frontend/main/background.ts`, run with
`ELECTRON_RUN_AS_NODE=1`), else the packaged app's executable found next to `resources/`, else a
`node` on PATH; without any of them the tool says it is unavailable. The runner process gets no
environment beyond what Node needs, `--max-old-space-size=256`, and Node's permission model
(read access to this folder only; no file writes, no child processes, no workers) as a second wall
behind the VM.

## Protocol (one JSON object per line)

| direction | message |
|---|---|
| parent → runner, first line | `{"type":"start","code":str,"tools":[name],"limits":{...}}` |
| runner → parent | `{"type":"call","id":n,"tool":name,"args":{}}` |
| parent → runner | `{"type":"result","id":n,"ok":true,"value":any}` or `{"ok":false,"error":str}` |
| runner → parent | `{"type":"log","text":str}` |
| runner → parent, last | `{"type":"done","value":any,"undefined":bool}` or `{"type":"error","kind":k,"message":str}` |

`kind` is one of `syntax`, `thrown`, `timeout`, `memory`, `call_timeout` (with `callId`),
`call_limit`, `protocol`, `internal`. A refusal that must end the script is the parent's decision:
it kills the runner. When stdin closes the runner exits at once, so it cannot outlive its parent.

Limits (defaults; the parent sends its own): `wallMs` 600000, `memoryBytes` 64 MiB, `stackBytes`
256 KiB, `callTimeoutMs` 300000, `maxCalls` 200, `maxLogBytes` 1 MiB. The wall clock is enforced
twice: QuickJS's interrupt handler while JS runs, a host timer while the script waits on a call.

## Inside the VM

Only `tools.<name>(args)` (a Proxy: an unknown name throws and lists the real ones),
`callTool(name, args)` and `console.log/info/warn/error/debug` exist. No `require`, `import`,
`fetch`, `process`, timers, `std` or `os`. The script body is wrapped in an async function, so
top-level `await` and `return` work, and line numbers in errors match the model's code.

## vendor/

Copied from the npm packages `quickjs-emscripten-core`, `@jitl/quickjs-wasmfile-release-sync`
and `@jitl/quickjs-ffi-types`, all 0.32.0, MIT (licence files kept next to each). Only the files
the release-sync variant needs are here. One change: the package-name import
`"@jitl/quickjs-ffi-types"` in `quickjs-emscripten-core/dist/index.mjs` and
`dist/chunk-V2S4ZYJR.mjs` now reads `"../../quickjs-ffi-types/dist/index.mjs"`, so the runner
resolves everything by relative path and needs no node_modules (electron-builder's resource copy
is not trusted to carry one). Upgrading means copying the same files from the new release and
repeating that rewrite.
