"""Desktop routes of remote control (step 4 calls them; nothing here is
reachable from the relay - phones only get the RPC allow-list in remote/rpc.py).

Every route needs the app token. Routes that open the machine to a phone
(enable, start pairing - its answer holds the pair secret -, approve, relay
URL) also need the UI secret, like approval-mode writes: model-run children
can read the app token file, and with it alone a child could pair a phone it
controls and answer its own approval cards.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from agentic import approval_mode
from auth_utils import _check_token
from remote.bridge import BridgeError, RemoteBridge


def create_remote_router(bridge: RemoteBridge) -> APIRouter:
    router = APIRouter(prefix="/remote")

    def ui_only(x_session_token: str, x_ui_secret: str) -> None:
        _check_token(x_session_token)
        if not approval_mode.check_ui_secret(x_ui_secret):
            raise HTTPException(status_code=403,
                                detail="Uzaktan kontrol yalnız uygulama arayüzünden açılabilir.")

    def fail(exc: BridgeError):
        raise HTTPException(status_code=exc.status, detail={"code": exc.code})

    @router.get("/status")
    async def status(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return bridge.status()

    @router.post("/enable")
    async def enable(x_session_token: str = Header(alias="X-Session-Token", default=""),
                     x_ui_secret: str = Header(alias="X-Gamachine-UI-Secret", default="")):
        ui_only(x_session_token, x_ui_secret)
        return await bridge.enable()

    @router.post("/disable")
    async def disable(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return await bridge.disable()

    @router.post("/forget")
    async def forget(x_session_token: str = Header(alias="X-Session-Token", default="")):
        """Turn off and forget: reset_room at the relay, keys and devices deleted."""
        _check_token(x_session_token)
        return await bridge.forget()

    @router.post("/pair/start")
    async def pair_start(x_session_token: str = Header(alias="X-Session-Token", default=""),
                         x_ui_secret: str = Header(alias="X-Gamachine-UI-Secret", default="")):
        ui_only(x_session_token, x_ui_secret)
        try:
            return await bridge.start_pairing("qr")
        except BridgeError as exc:
            fail(exc)

    @router.get("/pair/pending")
    async def pair_pending(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return {"pending": bridge.pending_pairing()}

    @router.post("/pair/approve")
    async def pair_approve(x_session_token: str = Header(alias="X-Session-Token", default=""),
                           x_ui_secret: str = Header(alias="X-Gamachine-UI-Secret", default="")):
        ui_only(x_session_token, x_ui_secret)
        try:
            return await bridge.approve_pairing()
        except BridgeError as exc:
            fail(exc)

    @router.post("/pair/reject")
    async def pair_reject(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return {"rejected": await bridge.reject_pairing()}

    @router.get("/devices")
    async def devices(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return {"devices": bridge.devices()}

    @router.delete("/devices/{device_id}")
    async def remove_device(device_id: str, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        if not await bridge.remove_device(device_id):
            raise HTTPException(status_code=404, detail={"code": "unknown_device"})
        return {"removed": device_id}

    @router.delete("/devices")
    async def remove_all(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return {"removed": await bridge.remove_all_devices()}

    @router.get("/relay-url")
    async def get_relay_url(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        s = bridge.status()
        return {"relay_url": s["relay_url"], "default_relay_url": s["default_relay_url"],
                "custom": s["custom_relay"]}

    @router.put("/relay-url")
    async def set_relay_url(body: dict, x_session_token: str = Header(alias="X-Session-Token", default=""),
                            x_ui_secret: str = Header(alias="X-Gamachine-UI-Secret", default="")):
        """{"url": "https://..."} for "my own relay", null for the project default."""
        ui_only(x_session_token, x_ui_secret)
        url: Optional[str] = body.get("url")
        try:
            return await bridge.set_relay_url(url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"code": "bad_relay_url", "message": str(exc)})

    @router.get("/keep-awake")
    async def get_keep_awake(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        s = bridge.status()
        return {"keep_awake": s["keep_awake"], "keep_awake_active": s["keep_awake_active"]}

    @router.put("/keep-awake")
    async def set_keep_awake(body: dict, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        if not isinstance(body.get("enabled"), bool):
            raise HTTPException(status_code=400, detail={"code": "enabled_must_be_bool"})
        return bridge.set_keep_awake(body["enabled"])

    return router
