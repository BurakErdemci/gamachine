"""Refuse a Gamachine chat's tool call when the targeted Unity editor has another project open.

The backend owns the comparison (it knows which project Gamachine has open);
this module only reports where the call is about to go. Fail CLOSED: a backend
that cannot be reached means the project cannot be verified.
"""

from __future__ import annotations

import asyncio
import os
import time

import httpx

from services.registry.tool_actions import tool_entry
from transport.approval_gate import _BACKEND_URL, _headers
from transport.plugin_hub import PluginHub

_CHECK_TIMEOUT = 3.0
_CACHE_TTL = 3.0
_UNREACHABLE = "Gamachine'e ulaşılamadı; Unity projesi doğrulanamadı, istek gönderilmedi."

# (conversation_id, project_path) -> (monotonic time, refusal text or None).
# Only answers from the backend are stored, never a failure to reach it.
_cache: dict[tuple[int, str], tuple[float, str | None]] = {}


_RELOAD_POLL_S = 0.25


def is_server_only_tool(tool_name: str | None) -> bool:
    """True for tools the ledger marks `unity_target=None`: they never open the Unity project.

    The ledger, not the runtime registry: the registry also marks tools that do
    reach Unity (execute_custom_tool, unity_dialog, unity_window_capture) with
    unity_target=None, and those must stay checked.
    """
    entry = tool_entry(tool_name) if isinstance(tool_name, str) else None
    return bool(entry) and str(entry.get("note", "")).startswith("unity_target=None")


def _same_path(a: str, b: str) -> bool:
    if os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b)):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def select_session_by_path(sessions: dict, project_path: str) -> tuple[str | None, str | None]:
    """The one session whose editor has `project_path` open, or an error text."""
    matches = [sid for sid, details in sessions.items()
               if getattr(details, "project_path", None) and _same_path(details.project_path, project_path)]
    name = os.path.basename(os.path.normpath(project_path))
    if not matches:
        return None, f"Unity'de bu proje açık değil: {name}."
    if len(matches) > 1:
        return None, f"Bu proje Unity'de birden fazla kez açık: {name}; hangisine gönderileceği belirsiz."
    return matches[0], None


async def _resolve_project_path(ctx) -> str:
    # Lazy: manage_build imports the transport package, so a module-level
    # import here would be circular.
    from services.tools.manage_build import _target_project_path

    # A domain reload drops the editor's session for a moment; wait for it
    # like PluginHub._resolve_session_id does before calling the target unknown.
    deadline = time.monotonic() + PluginHub.session_wait_budget()
    while True:
        project_path = await _target_project_path(ctx) or ""
        if project_path or time.monotonic() >= deadline:
            return project_path
        await asyncio.sleep(_RELOAD_POLL_S)


async def project_scope_refusal(ctx, conversation_id: int | None) -> str | None:
    if conversation_id is None or not os.environ.get("LOCAL_APP_TOKEN", ""):
        return None
    project_path = await _resolve_project_path(ctx)
    key = (conversation_id, project_path)
    cached = _cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _CACHE_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
            resp = await client.post(
                f"{_BACKEND_URL}/mcp-project-check",
                json={"conversation_id": conversation_id, "project_path": project_path},
                headers=_headers(),
            )
        if resp.status_code != 200:
            return _UNREACHABLE
        body = resp.json()
        if not isinstance(body, dict) or not isinstance(body.get("ok"), bool):
            return _UNREACHABLE
        detail = body.get("detail")
        refusal = None if body["ok"] else (detail if isinstance(detail, str) and detail else _UNREACHABLE)
    except Exception:
        return _UNREACHABLE
    for stale in [k for k, (stored, _) in _cache.items() if now - stored >= _CACHE_TTL]:
        del _cache[stale]
    _cache[key] = (now, refusal)
    return refusal
