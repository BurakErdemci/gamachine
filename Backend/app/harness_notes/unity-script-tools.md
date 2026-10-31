---
title: Which Unity tools read, write, validate and search C# scripts?
keywords: script, c#, kod, betik, düzenle, doğrula, ara, script, edit, create, validate, sha, find in file, apply edits
covers: manage_script, script_apply_edits, apply_text_edits, create_script, delete_script, validate_script, get_sha, manage_script_capabilities, find_in_file
---
These Unity MCP tools work on C# scripts.

- create_script, manage_script (create, read, modify), delete_script.
- script_apply_edits: code edits with validation; apply_text_edits: plain text edits; get_sha: hash of script content; validate_script: syntax and compilation check.
- find_in_file: search inside project files; it is read-only and needs no card in step mode.
- manage_script_capabilities: asks what script management supports.
- The writing tools (create_script, script_apply_edits, apply_text_edits, manage_script create) wait for the compile their write causes and return the verdict in data.compile; wait_for_compile=false returns right after the write.
- After writing .cs files with any other tool, call refresh_unity; Unity does not import them on its own while unfocused (see unity-compile-verdicts).
- Script changes during play mode carry a warning.

Sources: unity-mcp/manifest.json, docs/unity-mcp.md.
