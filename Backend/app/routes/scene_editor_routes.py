"""Authenticated app access to Unity's user-only scene editor resources."""
import asyncio
import json
import logging
import os
import urllib.error
import urllib.request

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, StrictInt

from auth_utils import _check_token
from omnisharp.omnisharp_manager import _unwrap_unity_result
from secret_redaction import redact_secrets
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager


logger = logging.getLogger(__name__)


class SceneEditorSelection(BaseModel):
    id: StrictInt | None


def _loggable(text: object) -> str:
    """Upstream text for the log: the two secrets this route sends are masked by value
    (a pattern filter would miss a bare token echoed back), then the generic redaction, then a cap."""
    out = str(text)
    for secret in (os.environ.get("LOCAL_APP_TOKEN", ""), *unity_mcp_manager.api_headers().values()):
        if secret:
            out = out.replace(secret, "<REDACTED>")
    return redact_secrets(out)[:500]


def _raise_unity_error(error: str, status: int = 200) -> None:
    normalized = error.lower()
    if status == 504 or "timeout" in normalized or "timed out" in normalized:
        raise HTTPException(status_code=504, detail="unity_timeout")
    if status == 503 or "no unity instances" in normalized or normalized in {"unavailable", "unity_unavailable"}:
        raise HTTPException(status_code=503, detail="unity_unavailable")
    if error == "not_found":
        raise HTTPException(status_code=404, detail="not_found")
    logger.warning("Unity scene editor error: %s", _loggable(error))
    raise HTTPException(status_code=502, detail="unity_error")


def _post_unity(command: str, params: dict) -> dict:
    request = urllib.request.Request(
        "http://localhost:8080/api/command", method="POST",
        data=json.dumps({"type": command, "params": params}).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 **unity_mcp_manager.api_headers(),
                 "X-UnityAI-Maintenance": os.environ.get("LOCAL_APP_TOKEN", "")},
    )
    try:
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = exc.code
            with exc:
                text = exc.read().decode("utf-8", "replace")
            try:
                body = json.loads(text)
            except ValueError:
                _raise_unity_error(text or str(exc), status)
        if not isinstance(body, dict):
            _raise_unity_error("Invalid Unity response", status)
        inner = _unwrap_unity_result(body)
        error = inner.get("error") or inner.get("detail") or body.get("error")
        if status >= 400 or inner.get("success") is False or body.get("status") == "error" or error:
            _raise_unity_error(str(error or inner.get("message") or f"Unity HTTP {status}"), status)
        data = inner.get("data", inner)
        if not isinstance(data, dict):
            raise ValueError("Invalid Unity response data")
        return data
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="unity_timeout") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise HTTPException(status_code=504, detail="unity_timeout") from exc
        raise HTTPException(status_code=503, detail="unity_unavailable") from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail="unity_unavailable") from exc
    except Exception as exc:
        logger.warning("Unity scene editor error: %s", _loggable(exc))
        raise HTTPException(status_code=502, detail="unity_error") from exc


async def _call_unity(command: str, params: dict) -> dict:
    return await asyncio.to_thread(_post_unity, command, params)


def create_scene_editor_router() -> APIRouter:
    router = APIRouter(prefix="/scene-editor", tags=["scene-editor"])

    @router.get("/tree")
    async def tree(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_tree", {})

    @router.get("/inspect/{id}")
    async def inspect(id: int, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_inspect", {"id": id})

    @router.get("/version")
    async def version(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_version", {})

    @router.post("/select")
    async def select(body: SceneEditorSelection,
                     x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_select", {"id": body.id})

    return router
