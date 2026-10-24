"""Refuse a Gamachine chat's tool call when the targeted Unity editor has another project open.

The backend owns the comparison (it knows which project Gamachine has open);
this module only reports where the call is about to go. Fail CLOSED: a backend
that cannot be reached means the project cannot be verified.
"""

from __future__ import annotations

import os
import time

import httpx

from transport.approval_gate import _BACKEND_URL, _headers

_CHECK_TIMEOUT = 3.0
_CACHE_TTL = 3.0
_UNREACHABLE = "Gamachine'e ulaşılamadı; Unity projesi doğrulanamadı, istek gönderilmedi."

# (conversation_id, project_path) -> (monotonic time, refusal text or None).
# Only answers from the backend are stored, never a failure to reach it.
_cache: dict[tuple[int, str], tuple[float, str | None]] = {}


async def project_scope_refusal(ctx, conversation_id: int | None) -> str | None:
    if conversation_id is None or not os.environ.get("LOCAL_APP_TOKEN", ""):
        return None
    # Lazy: manage_build imports the transport package, so a module-level
    # import here would be circular.
    from services.tools.manage_build import _target_project_path

    project_path = await _target_project_path(ctx) or ""
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
    _cache[key] = (now, refusal)
    return refusal
