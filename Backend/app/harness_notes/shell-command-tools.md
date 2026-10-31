---
title: How do run_command, bash, run_terminal_command and execute_shell_command work?
keywords: terminal, komut, kabuk, çalıştır, shell, bash, command, run, git, onay, timeout
covers: run_command, bash, run_terminal_command, execute_shell_command
---
These tools run one terminal command in the workspace. run_command is the API-loop name; bash, run_terminal_command and execute_shell_command are three names for the same tool on CLI providers.

- Safe, read-only commands run directly (as an argv, without a shell). Any other command shows an approval card; an approved raw string runs through the shell exactly as the user saw it.
- A command that tries to write a file (redirects, python -c open().write, printf > path) is routed to the diff card as a file write, and an empty file is refused.
- A command that touches a protected Unity file (.meta, YAML asset) is refused first, in every mode (see unity-file-rule).
- API loop result: stdout, stderr, exit_code and a summary. A command that printed anything counts as success even with a non-zero code, so read exit_code. Over 300 seconds it times out.
- Whether a card appears depends on the approval mode (see approval-modes).
- Read-only runs refuse run_command.

Sources: Backend/app/tools/file_tools.py (run_command), Backend/app/unity_ai_mcp/tools/bash_tool.py, Backend/app/agentic/shell_exec.py, docs/security.md.
