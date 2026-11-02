"""Connectors (CONTRACT.md section 3): catalog view, install plan/job/cancel, remove, switch, app path.

All routes need X-Session-Token. Writes also need X-Gamachine-UI-Secret and are refused with
X-UnityAI-Maintenance; the phone's remote dispatcher denies them as well (remote/ui_dispatch.py).
"""
import logging
import os
from typing import Callable, Optional

from fastapi import APIRouter, Body, Header, HTTPException

from agentic import approval_mode
from auth_utils import _check_token
from connectors import catalog, detect, settings, views
from connectors.installer import Busy, Installer

logger = logging.getLogger(__name__)


def create_connector_router(db, installer: Optional[Installer] = None, entries: Optional[dict] = None,
                            detector: Callable = detect.detect, prober: Callable = detect.probe,
                            connected: Callable[[str], bool] = views.bridge_answers) -> APIRouter:
    router = APIRouter()
    installer = installer or Installer()
    entries = catalog.load() if entries is None else entries

    def _entry(cid: str) -> dict:
        entry = entries.get(cid)
        if entry is None:
            raise HTTPException(status_code=404, detail="Bağlayıcı bulunamadı.")
        return entry

    def _check_write(token: str, secret: str, maintenance: str) -> None:
        _check_token(token)
        if maintenance:
            raise HTTPException(status_code=403, detail="Bu kanaldan bağlayıcılar değiştirilemez.")
        if not approval_mode.check_ui_secret(secret):
            raise HTTPException(status_code=403, detail="Bağlayıcılar yalnız uygulama arayüzünden değiştirilebilir.")

    def _view(entry: dict) -> dict:
        detection = views.detect_for(entry, db, detector)
        return views.connector_view(entry, db, installer, detection, connected)

    # Plain `def` handlers: detection and removal start processes, so they run in the threadpool.
    @router.get("/connectors")
    def list_connectors(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return {"connectors": [_view(entry) for entry in entries.values()]}

    @router.post("/connectors/{cid}/plan")
    def plan(cid: str,
             x_session_token: str = Header(alias="X-Session-Token", default=""),
             x_ui_secret: str = Header(alias="X-Gamachine-UI-Secret", default=""),
             x_maintenance: str = Header(alias="X-UnityAI-Maintenance", default="")):
        _check_write(x_session_token, x_ui_secret, x_maintenance)
        entry = _entry(cid)
        return views.plan_view(entry, installer, views.detect_for(entry, db, detector))

    @router.post("/connectors/{cid}/install", status_code=202)
    def install(cid: str, body: Optional[dict] = Body(default=None),
                x_session_token: str = Header(alias="X-Session-Token", default=""),
                x_ui_secret: str = Header(alias="X-Gamachine-UI-Secret", default=""),
                x_maintenance: str = Header(alias="X-UnityAI-Maintenance", default="")):
        _check_write(x_session_token, x_ui_secret, x_maintenance)
        entry = _entry(cid)
        replace = (body or {}).get("replace_addon", False)
        if type(replace) is not bool:
            raise HTTPException(status_code=400, detail="replace_addon true ya da false olmalı.")
        if installer.running(cid):
            raise HTTPException(status_code=409, detail="Bu bağlayıcının kurulumu zaten sürüyor.")
        detection = views.detect_for(entry, db, detector)
        chosen = detection["chosen"]
        if not chosen or not detect.at_least(chosen["version"], entry["app"]["min_version"]):
            raise HTTPException(status_code=409, detail="Bağlayıcının uygulaması bulunamadı ya da sürümü çok eski.")
        if not replace and installer.addon_conflict(entry, chosen):
            raise HTTPException(status_code=409,
                                detail="Uygulamada uyumsuz bir eklenti kurulu; değiştirmek için onay gerekli.")
        try:
            job = installer.start(entry, chosen, replace_addon=replace)
        except Busy as exc:
            raise HTTPException(status_code=409, detail="Bağlayıcı kaldırılırken kurulamaz." if exc.removing
                                else "Bu bağlayıcının kurulumu zaten sürüyor.")
        return {"job": job.view()}

    @router.post("/connectors/{cid}/install/cancel")
    def cancel(cid: str,
               x_session_token: str = Header(alias="X-Session-Token", default=""),
               x_ui_secret: str = Header(alias="X-Gamachine-UI-Secret", default=""),
               x_maintenance: str = Header(alias="X-UnityAI-Maintenance", default="")):
        _check_write(x_session_token, x_ui_secret, x_maintenance)
        _entry(cid)
        return {"cancelled": installer.cancel(cid)}

    @router.get("/connectors/{cid}/install")
    def install_status(cid: str, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        _entry(cid)
        job = installer.job(cid)
        return {"job": job.view() if job else None}

    @router.post("/connectors/{cid}/remove")
    def remove(cid: str,
               x_session_token: str = Header(alias="X-Session-Token", default=""),
               x_ui_secret: str = Header(alias="X-Gamachine-UI-Secret", default=""),
               x_maintenance: str = Header(alias="X-UnityAI-Maintenance", default="")):
        _check_write(x_session_token, x_ui_secret, x_maintenance)
        entry = _entry(cid)
        try:
            problems = installer.remove(entry)
        except Busy as exc:
            raise HTTPException(status_code=409, detail="Bağlayıcının kaldırılması zaten sürüyor." if exc.removing
                                else "Bağlayıcı kurulurken kaldırılamaz.")
        settings.set_enabled(db, cid, False)
        if problems:
            for problem in problems:
                logger.warning("[connectors] remove %s: %s", cid, problem)
            # Never a success while files remain; the next Remove retries what is named here.
            left = "; ".join(problems)[:600]
            raise HTTPException(status_code=409,
                                detail=f"Bağlayıcı tamamen kaldırılamadı; kalanlar: {left}")
        return _view(entry)

    @router.put("/connectors/{cid}/enabled")
    def set_enabled(cid: str, body: dict,
                    x_session_token: str = Header(alias="X-Session-Token", default=""),
                    x_ui_secret: str = Header(alias="X-Gamachine-UI-Secret", default=""),
                    x_maintenance: str = Header(alias="X-UnityAI-Maintenance", default="")):
        _check_write(x_session_token, x_ui_secret, x_maintenance)
        entry = _entry(cid)
        on = body.get("enabled")
        if type(on) is not bool:
            raise HTTPException(status_code=400, detail="enabled true ya da false olmalı.")
        # An older installed version still works, so update_available may be switched on too.
        if on and _view(entry)["state"] not in ("ready", "update_available"):
            raise HTTPException(status_code=409, detail="Bağlayıcı yalnız kurulu ve hazırken açılabilir.")
        settings.set_enabled(db, cid, on)
        return _view(entry)

    @router.put("/connectors/{cid}/app-path")
    def set_app_path(cid: str, body: dict,
                     x_session_token: str = Header(alias="X-Session-Token", default=""),
                     x_ui_secret: str = Header(alias="X-Gamachine-UI-Secret", default=""),
                     x_maintenance: str = Header(alias="X-UnityAI-Maintenance", default="")):
        _check_write(x_session_token, x_ui_secret, x_maintenance)
        entry = _entry(cid)
        if "path" not in body or (body["path"] is not None and not isinstance(body["path"], str)):
            raise HTTPException(status_code=400, detail="path bir metin ya da null olmalı.")
        path = body["path"]
        if path is not None:
            if not os.path.isabs(path) or not os.path.isfile(path) or not prober(path):
                raise HTTPException(status_code=400,
                                    detail="Seçilen dosya sürümü okunabilen bir uygulama değil.")
        settings.set_app_path(db, cid, path)
        return _view(entry)

    return router
