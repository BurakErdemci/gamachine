---
title: Which Unity tools run tests, profile, build and manage packages?
keywords: test, profil, build, paket, derleme, platform, run tests, profiler, package, build player, test framework
covers: run_tests, get_test_job, manage_profiler, manage_build, manage_packages
---
These Unity MCP tools cover tests, profiling, builds and packages.

- run_tests starts Unity Test Framework tests (testing group, opt-in); get_test_job polls the async job. A start is refused while the compile verdict is not clean (see unity-compile-verdicts), and a pass read while it is not clean comes back as failed.
- manage_profiler: Profiler session control, counter reads, memory snapshots and Frame Debugger. It belongs to the opt-in profiling group, so it is listed only when that group is enabled or on /mcp/full.
- manage_build: trigger player builds, switch platforms, configure settings, batch builds. A build blocks Unity until it ends; follow it with action wait and job_id instead of repeated status calls.
- manage_packages: install, remove, embed packages and configure registries.
- run_tests is in the group of tools that refresh the project first, so it opens a card in step mode.

Sources: docs/unity-mcp.md, docs/security.md, unity-mcp/manifest.json, Backend/app/agent_guide.py.
