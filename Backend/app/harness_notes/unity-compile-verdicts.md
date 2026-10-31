---
title: How do I know whether my C# compiled? What do compile_status, refresh_unity and read_console tell me?
keywords: derleme, hata, konsol, temiz, derle, compile, console, errors, verdict, stale, pending, clean, refresh
covers: compile_status, refresh_unity, read_console
---
An empty Unity console does not mean a clean compile: it reads 0 errors before and while a compile runs.

- compile_status: read-only, returns the live verdict and never compiles or refreshes.
- Verdicts: clean (compiled and the domain reload finished), errors (last compile failed, with CS code, file, line), compiling, pending (import or reload still running), stale (scripts changed on disk since the last compile started; call refresh_unity), timeout (a wait gave up after 90 s), unknown (could not be read; not the same as clean).
- Only clean means new types can be used.
- refresh_unity: refreshes the asset database and returns data.compile; call it after writing .cs files with other tools.
- read_console (action get) adds compile_state when the verdict is not clean; read it for messages, not as a pass.
- run_tests and get_test_job are refused while the verdict is not clean (errors and stale answer "compile", the others answer "busy").
- A build blocks Unity: follow it with manage_build action wait.

Sources: docs/unity-mcp.md (Compile verdicts), Backend/app/agent_guide.py, unity-mcp/manifest.json.
