---
title: How do background commands (run_command background=true, job_status, job_output, job_stop) work?
keywords: arka plan, iş, komut, uzun, sunucu, background, job, long-running, server, output, stop
covers: job_status, job_output, job_stop
---
In the API loop, run_command with background=true starts the command and returns a job id at once instead of waiting; the same approval rules as a normal run_command apply.

- job_status(job_id): running, or exited with its exit code, or stopped.
- job_output(job_id, tail_chars): the last part of its combined stdout/stderr (the job keeps the last 256 KiB).
- job_stop(job_id): ends the job and everything it started.
- At most 8 jobs run at once per chat; jobs are visible only in the chat that started them.
- A Stop or a compaction of the chat does not end background jobs; only job_stop (or the job finishing) does.

Sources: Backend/app/agentic/shell_jobs.py, Backend/app/agentic/shell_exec.py (start_background), Backend/app/tools/tool_registry.py.
