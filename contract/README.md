# Contract suite

The promises the backend keeps at its external surface, checked from outside. The suite
starts the real backend as a separate process on a fresh temp root and talks to it only
over HTTP and server-sent events. Nothing under `contract/` imports `Backend/app`, so the
tests stay the same when the backend's insides are rewritten, even in another language.

`python -m pytest contract --collect-only -q` lists the promises: every test name is a
sentence, and its docstring states the promise in one line.

## Run it

```
python -m pytest contract -q
```

From the repo root, with the backend's Python (the one in `Backend/venv` is picked when it
exists, otherwise the interpreter running pytest). It needs about 20 s on Windows and
must stay under 90 s.

- `--update-golden` rewrites `golden/*.sse.golden` from this run. Review the diff like code.
- `CONTRACT_KEEP_ROOT=1` keeps the temp root (backend log, DB, fake home) after the run.

## What the session fixture does (`conftest.py`)

- Makes a temp root and points `USERPROFILE`, `HOME`, `APPDATA`, `LOCALAPPDATA`, the XDG
  dirs, `CLAUDE_CONFIG_DIR`, `KIMI_CODE_HOME`, `CODEX_HOME`, `TMP`/`TEMP`/`TMPDIR` and
  `DB_PATH` into it. The root is removed at teardown.
- Sets `GAMACHINE_NO_UNITY_MCP=1`, so the backend never starts, probes or stops the Unity MCP
  server on 8080. It also sets `LOCAL_APP_TOKEN` to a test token and `API_KEY_ENCRYPTION_KEY`,
  so the backend never reads the OS keyring.
- Builds the child's environment from scratch. `PATH` is only three dirs: `fakebin/`, the
  interpreter's dir and the OS dir (System32, or `/usr/bin:/bin`). `fakebin/` holds stubs
  named after every provider CLI (claude, codex, opencode, agy, gemini, cursor-agent, kimi,
  copilot). A stub records its call in `fakebin/spawned.log` and fails, and teardown fails
  the session if that file exists.
- Sends any proxy-aware request that does not go to loopback to a dead local proxy
  (`127.0.0.1:9`), so a stray call to a real host fails at once and shows in the log.
- Picks a free port (bind to port 0, then close) that is never 8000, 8080 or 8799. It waits
  up to 30 s for `GET /health` and retries up to 3 starts. On failure it shows the tail of
  the child's log. After startup it checks that the DB file exists under the temp root.
- Kills the child's whole process tree at teardown, by the child's own pid only: Windows
  `taskkill /T /F /PID`, POSIX the child's process group. Every wait is bounded.
- Starts a 300 s timer at session start that kills the backend if the session runs over.
- Teardown also fails the session if the backend log shows a `localhost:8080` or
  `127.0.0.1:8080` contact.

## The fake provider, and why this one

The model provider is faked at its HTTP boundary by `fakes/fake_openai.py`, a small
OpenAI-compatible server on 127.0.0.1 inside the pytest process.

- The backend's `openai` provider builds its SDK client without a base URL
  (`agentic/agent_runner.py`). The `openai` SDK then reads `OPENAI_BASE_URL`, and the
  fixture points that at the fake. No product change was needed.
- A fake CLI would have to copy each CLI's private stream format and session handshake. The
  HTTP API is a public, documented format, and the backend's API loop is what every API
  provider goes through.
- The chat's model name picks the scenario: model `contract-<name>` replays
  `fixtures/openai_<name>.sse` (a streamed answer) or `fixtures/openai_<name>.json` (an
  HTTP error). In an `.sse` file, the line `: stall` holds the stream open until the
  backend hangs up.
- Chat titles are turned off in the session setup (`POST /chat-title-setting`): the title
  model has a fixed `api.openai.com` URL that the fake cannot take over.
- The fixtures are written by hand in the documented chunk format. No recording was
  possible without network access. Replace them with recorded responses when someone
  records them (`fixtures/` only, scrubbed of keys).

## Golden streams

A chat stream is checked in two ways.

- Structural invariants (`sse.terminal_problems`): exactly one `done` or `error`, and nothing
  after it except the one `context_usage` frame the backend always sends last.
- A golden file: the events are normalized first. Ids, timestamps, durations and token
  counts become `<key>` placeholders, `status` heartbeats are dropped, consecutive `text`
  chunks are merged and keys are sorted. The result is then compared with
  `golden/<name>.sse.golden`.

## How a rewrite uses it

Only the launch command changes:

```
CONTRACT_BACKEND_CMD='["/path/to/new-backend", "--serve"]' python -m pytest contract -q
```

`CONTRACT_BACKEND_CMD` takes a JSON list or a plain command line. The new backend gets the
same environment and must read `PORT`, `HOST`, `DB_PATH`, `LOCAL_APP_TOKEN` and
`OPENAI_BASE_URL` from it. Parity is the number of passing tests plus the lines in
`known_differences.txt`, where each deliberate difference is written down and reviewed
(the typescript-go "accepted baselines" model).

## Adding a promise

1. Find the promise at the external surface: an endpoint, a stream, a stored result. If it
   cannot be written as one sentence, do not write the test.
2. Name the test as that sentence (`test_a_deleted_conversation_is_gone`) and put the
   promise in a one-line docstring. One test per promise.
3. Talk to the backend only through `client.Api` and `sse`. No import of backend code. Fake
   only what sits outside the backend: model providers, Unity.
4. See the test fail once: break the behaviour, or run it against a commit without it.
5. It joins the CI gate after 20 consecutive green runs. A flaky test is fixed at its root
   cause or deleted, never retried.
