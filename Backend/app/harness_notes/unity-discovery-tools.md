---
title: Which Unity tools look up documentation, reflect APIs, list tool groups or route to an instance?
keywords: dokümantasyon, yansıma, grup, örnek, instance, docs, reflection, api, tool groups, routing, set_active_instance, debug
covers: unity_docs, unity_reflect, manage_tools, debug_request_context, set_active_instance
---
These Unity MCP tools help discover APIs, tool availability and routing.

- unity_docs: fetches Unity documentation (ScriptReference, Manual, package docs). unity_reflect: inspects Unity C# APIs via live reflection. Both belong to the opt-in docs group.
- manage_tools: lists tool groups and which are available on this connection's URL. activate, deactivate and reset change nothing and return an error naming the URLs.
- debug_request_context: inspects the MCP request context.
- set_active_instance: checks an instance identifier and says how to route to it; it does not pin a selection. With several Unity projects open, name the target on each call with unity_instance="Name@hash" or the call is refused.
- Which tools you see depends on the URL: /mcp (groups enabled in the Editor), /mcp/gamachine (core, playtest and scripting_ext; what API models get) and /mcp/full (every group).

Sources: docs/unity-mcp.md, unity-mcp/manifest.json.
