---
title: How do read_file, search_in_project, find_files and list_directory work and what are their limits?
keywords: dosya, oku, ara, listele, klasör, read, search, find, list, directory, file, grep, workspace
covers: read_file, search_in_project, find_files, list_directory
---
These four tools read the project without changing it and are allowed in read-only runs.

- read_file(file_path): reads one text file. On the API loop only the first 500 lines come back; the result says total_lines and truncated. On CLI providers read_file has start_line and max_lines (1 to 2000, default 500) and a footer tells where to continue.
- search_in_project(query, file_extensions): text search. Default extensions are .cs, .shader, .compute, .hlsl, .json, .xml, .yaml and .txt; default max_results is 30; case-insensitive by default.
- find_files(pattern): finds files by name, case-insensitive; default extensions are .cs and .shader.
- list_directory(dir_path, extensions): lists files and subfolders, optionally only some extensions.
- Paths are relative to the workspace root. A path that resolves outside the workspace is refused with a security error.
- A missing file answers "Dosya bulunamadı".
- On CLI providers the stdio server has read_file and list_directory only; searching goes through the provider's own tools.

Sources: Backend/app/tools/file_tools.py, Backend/app/tools/search_tools.py, Backend/app/unity_ai_mcp/tools/file_tools.py, Backend/app/agentic/agent_runner.py (_READ_ONLY_BUILTIN_TOOLS), docs/security.md.
