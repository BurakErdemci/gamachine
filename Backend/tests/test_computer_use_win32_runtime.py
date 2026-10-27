"""The runtime socket check follows the platform: a Windows named pipe is valid there, a file path is not."""
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode
from computer_use import platform as computer_platform
from computer_use.platform import macos
from computer_use.platform.macos import MacPlatform
from computer_use.platform.windows import WindowsPlatform
from routes.conversation_routes import create_conversation_router

PIPE = r"\\.\pipe\gamachine-cua-1a2b3c4d"
AUTH = {"X-Session-Token": "runtime-token", "X-Gamachine-UI-Secret": "runtime-secret"}


@pytest.fixture
def windows():
    return WindowsPlatform(win32=MagicMock())


@pytest.mark.parametrize("path,ok", [
    (PIPE, True), (r"\\.\pipe\a", True), (r"\\.\pipe\x.y_z-1", True),
    ("\\\\.\\pipe\\a" + "b" * 200, False), ("\\\\.\\pipe\\", False),
    (r"\\.\pipe\a\b", False), (r"\\.\pipe\a b", False), (r"\\?\pipe\abc", False),
    ("relative", False), ("", False), (r"C:\Users\x\d.sock", False), ("/tmp/d.sock", False),
    (PIPE + "\n", False),
])
def test_windows_accepts_only_pipe_names(windows, path, ok):
    assert windows.valid_socket(path) is ok


def test_mac_keeps_the_absolute_path_rule():
    mac = MacPlatform()
    assert mac.valid_socket(os.path.abspath("d-1.sock")) is True
    assert mac.valid_socket("relative.sock") is False
    assert mac.valid_socket(PIPE) is os.path.isabs(PIPE)


@pytest.fixture
def runtime(tmp_path, monkeypatch, windows):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "runtime-token")
    monkeypatch.setattr(computer_platform, "current", lambda: windows)
    approval_mode.set_ui_secret("runtime-secret")
    db = MagicMock()
    db.db_path = str(tmp_path / "db.sqlite")
    db.get_setting.return_value = None
    app = FastAPI()
    app.include_router(create_conversation_router(db, {}))
    with TestClient(app) as client:
        yield client
    approval_mode.set_ui_secret("")


def test_route_accepts_a_running_runtime_with_a_pipe_socket(runtime, tmp_path):
    binary = tmp_path / "cua-driver.exe"
    binary.write_bytes(b"fake")
    body = {"running": True, "driver_path": str(binary), "socket_path": PIPE}
    assert runtime.post("/computer-use/runtime", json=body, headers=AUTH).status_code == 200
    assert runtime.get("/computer-use/settings", headers=AUTH).json()["runtime"] == body


@pytest.mark.parametrize("socket_path", ["C:\\work\\d.sock", "relative", "\\\\.\\pipe\\bad name"])
def test_route_rejects_a_non_pipe_socket_on_windows(runtime, tmp_path, socket_path):
    binary = tmp_path / "cua-driver.exe"
    binary.write_bytes(b"fake")
    body = {"running": True, "driver_path": str(binary), "socket_path": socket_path}
    assert runtime.post("/computer-use/runtime", json=body, headers=AUTH).status_code == 400


class _Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _client(data):
    client = MagicMock()
    client.get = AsyncMock(return_value=_Response(data))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return lambda *a, **k: client


@pytest.mark.asyncio
async def test_fetch_runtime_returns_a_pipe_runtime_on_windows(tmp_path, monkeypatch, windows):
    binary = tmp_path / "cua-driver.exe"
    binary.write_bytes(b"fake")
    monkeypatch.setattr(computer_platform, "current", lambda: windows)
    runtime = {"running": True, "driver_path": str(binary), "socket_path": PIPE}
    monkeypatch.setattr(macos.httpx, "AsyncClient", _client({"runtime": runtime}))
    assert await macos.fetch_runtime() == (str(binary), PIPE)


@pytest.mark.asyncio
async def test_fetch_runtime_rejects_a_file_socket_on_windows(tmp_path, monkeypatch, windows):
    binary = tmp_path / "cua-driver.exe"
    binary.write_bytes(b"fake")
    monkeypatch.setattr(computer_platform, "current", lambda: windows)
    runtime = {"running": True, "driver_path": str(binary), "socket_path": str(tmp_path / "d.sock")}
    monkeypatch.setattr(macos.httpx, "AsyncClient", _client({"runtime": runtime}))
    with pytest.raises(macos.DriverUnavailable):
        await macos.fetch_runtime()
