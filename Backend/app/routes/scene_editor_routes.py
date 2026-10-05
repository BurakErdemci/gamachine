"""Authenticated app access to Unity's user-only scene editor resources."""
import asyncio
import json
import logging
import math
import os
import urllib.error
import urllib.request
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator

from auth_utils import _check_token
from omnisharp.omnisharp_manager import _unwrap_unity_result
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager


logger = logging.getLogger(__name__)


class SceneEditorSelection(BaseModel):
    id: StrictInt | None


class SceneEditorCreate(BaseModel):
    item: StrictStr
    parentId: StrictInt | None


class SceneEditorObject(BaseModel):
    id: StrictInt


class SceneEditorRename(SceneEditorObject):
    name: StrictStr


class SceneEditorActive(SceneEditorObject):
    active: StrictBool


class SceneEditorComponent(BaseModel):
    componentId: StrictInt


class SceneEditorField(SceneEditorComponent):
    field: StrictStr
    value: StrictBool | StrictInt | StrictFloat | StrictStr | list[StrictInt | StrictFloat]

    @field_validator("value", mode="before")
    @classmethod
    def finite_value(cls, value):
        values = value if isinstance(value, list) else [value]
        if any(isinstance(item, float) and not math.isfinite(item) for item in values):
            # FastAPI's validation error response cannot serialize a non-finite input.
            raise HTTPException(status_code=422, detail="invalid_value")
        return value


class SceneEditorComponentEnable(SceneEditorComponent):
    enabled: StrictBool


class SceneEditorAddComponent(SceneEditorObject):
    item: StrictStr


class SceneEditorComponentAction(SceneEditorComponent):
    action: Literal["reset", "remove", "up", "down"]


def _raise_unity_error(error: str, status: int = 200) -> None:
    normalized = error.lower()
    if status == 504 or "timeout" in normalized or "timed out" in normalized:
        raise HTTPException(status_code=504, detail="unity_timeout")
    if status == 503 or "no unity instances" in normalized or normalized in {"unavailable", "unity_unavailable"}:
        raise HTTPException(status_code=503, detail="unity_unavailable")
    if error == "not_found":
        raise HTTPException(status_code=404, detail="not_found")
    if error in {"locked", "compiling", "prefab_part", "invalid_name", "invalid_value",
                 "invalid_item", "create_failed", "write_failed", "already_present", "required", "add_failed"}:
        raise HTTPException(status_code=409, detail=error)
    try:
        logger.warning("Unity scene editor error (HTTP %s)", status)
    except Exception:
        pass  # A logging handler must not change the HTTP response.
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
        try:
            logger.warning("Unity scene editor error: %s", type(exc).__name__)
        except Exception:
            pass  # A logging handler must not change the HTTP response.
        raise HTTPException(status_code=502, detail="unity_error") from exc


async def _call_unity(command: str, params: dict) -> dict:
    return await asyncio.to_thread(_post_unity, command, params)


def create_scene_editor_router() -> APIRouter:
    async def check_token(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)

    router = APIRouter(prefix="/scene-editor", tags=["scene-editor"], dependencies=[Depends(check_token)])

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

    @router.get("/create-menu")
    async def create_menu(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_create_menu", {})

    @router.post("/create")
    async def create(body: SceneEditorCreate,
                     x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_create", {"item": body.item, "parentId": body.parentId})

    @router.post("/rename")
    async def rename(body: SceneEditorRename,
                     x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_rename", {"id": body.id, "name": body.name})

    @router.post("/set-active")
    async def set_active(body: SceneEditorActive,
                         x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_set_active", {"id": body.id, "active": body.active})

    @router.post("/duplicate")
    async def duplicate(body: SceneEditorObject,
                        x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_duplicate", {"id": body.id})

    @router.post("/delete")
    async def delete(body: SceneEditorObject,
                     x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_delete", {"id": body.id})

    @router.post("/set-field")
    async def set_field(body: SceneEditorField,
                        x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_set_field", {
            "componentId": body.componentId, "field": body.field, "value": body.value})

    @router.post("/component-enable")
    async def component_enable(body: SceneEditorComponentEnable,
                               x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_component_enable", {
            "componentId": body.componentId, "enabled": body.enabled})

    @router.get("/component-menu/{id}")
    async def component_menu(id: int, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_component_menu", {"id": id})

    @router.post("/add-component")
    async def add_component(body: SceneEditorAddComponent,
                            x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_add_component", {"id": body.id, "item": body.item})

    @router.post("/component-action")
    async def component_action(body: SceneEditorComponentAction,
                               x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await _call_unity("gm_editor_component_action", {
            "componentId": body.componentId, "action": body.action})

    return router
