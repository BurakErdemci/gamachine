"""Independent computer-use mode, without native dialogs or desktop input."""
import asyncio
import hashlib
import io
import json
import sys
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from computer_use import gate, platform, policy, server
from computer_use.driver_client import DriverUnavailable
from computer_use.platform.windows import WindowsPlatform
from computer_use.standalone import config, runtime
from computer_use.standalone import __main__ as entry
from computer_use.standalone.approvals import LocalApprovals


APP = r"win:c:\apps\editor.exe"
PARAMS = {"bundle_id": APP, "app_name": "Editor", "pid": 41, "summary": "Read document"}


class FakeHotkey:
    def __init__(self, registered=True):
        self.registered = registered
        self.halted_at = None
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def clear(self):
        self.halted_at = None


# The server's session lock uses msvcrt, which exists only on Windows.
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="session lock uses msvcrt")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMACHINE_CU_STANDALONE_DIR", str(tmp_path))
    monkeypatch.setenv("GAMACHINE_CU_CLAUDE_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    win = WindowsPlatform(SimpleNamespace(set_dpi_awareness=lambda: None, parent_pids=lambda: {}))
    monkeypatch.setattr(platform, "current", lambda: win)
    monkeypatch.setattr(entry._win32, "self_elevated", lambda: False)
    monkeypatch.delenv("GAMACHINE_CONVERSATION_ID", raising=False)
    monkeypatch.setenv("GAMACHINE_CU_STANDALONE", "1")


def write_config(**changes):
    data = {"enabled": True, "driver_path": None, **changes}
    (config.STATE_DIR / "config.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def journal_rows():
    return [json.loads(line) for line in config.journal_path().read_text(encoding="utf-8").splitlines()]


def test_initial_config_is_disabled():
    assert config.load() == {"enabled": False, "driver_path": None}
    assert json.loads((config.STATE_DIR / "config.json").read_text())["enabled"] is False
    assert config.log_path().parent == config.STATE_DIR
    assert config.journal_path().name == "computer_use_actions.jsonl"


@pytest.mark.parametrize("content", ["{broken", "[]", "null", '"text"', "42"])
def test_invalid_config_is_never_overwritten(content):
    path = config.STATE_DIR / "config.json"
    path.write_text(content, encoding="utf-8")
    assert config.load() == {"enabled": False, "driver_path": None,
                             "error": "config.json okunamadı"}
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("value", [1, "true", [], {}, None, False])
def test_enabled_requires_json_true(value):
    write_config(enabled=value)
    assert config.load()["enabled"] is False


def test_unreadable_config(monkeypatch):
    write_config()
    monkeypatch.setattr(type(config.STATE_DIR), "read_text", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    assert config.load()["error"] == "config.json okunamadı"


def test_config_ignores_extra_keys():
    stored = write_config(grants=[{"bundle_id": APP}], extra="ignored")
    assert config.load() == {"enabled": True, "driver_path": None}
    assert json.loads((config.STATE_DIR / "config.json").read_text()) == stored


@pytest.mark.parametrize("stage, expected", [
    (0, "Bu mod yalnız Windows'ta çalışır."),
    (1, "Bağımsız bilgisayar kullanımı kapalı (GAMACHINE_CU_STANDALONE=1 gerekli)."),
    (2, "Bu mod Gamachine sohbetinde kullanılmaz."),
    (3, "Yönetici olarak çalışırken bilgisayar kullanımı başlatılmaz."),
    (4, "config.json okunamadı"),
    (5, "disabled"),
    (6, "Durdurma kısayolu (Ctrl+Alt+Esc) alınamadı; bilgisayar kullanımı başlatılmadı."),
    (7, None),
])
def test_startup_refusals_are_ordered(monkeypatch, stage, expected):
    # All later conditions also fail: only the first failing check must win.
    if stage == 0:
        monkeypatch.setattr(platform, "current", lambda: SimpleNamespace(name="other"))
    if stage <= 1:
        monkeypatch.delenv("GAMACHINE_CU_STANDALONE", raising=False)
    if stage <= 2:
        monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", "")
    monkeypatch.setattr(entry._win32, "self_elevated", lambda: stage <= 3)
    if stage <= 4:
        (config.STATE_DIR / "config.json").write_text("{", encoding="utf-8")
    else:
        write_config(enabled=stage > 5)
    hotkey = FakeHotkey(registered=stage == 7)
    if expected == "disabled":
        expected = ("Bağımsız bilgisayar kullanımı kapalı; config.json içinde enabled true yapın: "
                    + str(config.STATE_DIR / "config.json"))
    session_lock = SimpleNamespace(acquire=lambda: True)
    assert entry._startup_refusal(hotkey, session_lock) == expected
    assert hotkey.started is (stage >= 6)


@windows_only
def test_main_supplies_local_components_and_stops_hotkey(monkeypatch):
    write_config()
    hotkey = FakeHotkey()
    calls = {}
    monkeypatch.setattr(entry, "HaltHotkey", lambda: hotkey)
    def create(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(run=lambda **kw: calls.update(kw))
    monkeypatch.setattr(entry, "create_server", create)
    entry.main([])
    assert calls["name"] == "gamachineComputerLocal" and calls["transport"] == "stdio"
    assert isinstance(calls["approvals"], LocalApprovals)
    assert isinstance(calls["runtime"], runtime.StandaloneRuntime)
    assert calls["approvals"].startup_refusal is None
    assert hotkey.started and hotkey.stopped


@windows_only
def test_main_stops_hotkey_when_server_fails(monkeypatch):
    hotkey = FakeHotkey()
    monkeypatch.setattr(entry, "HaltHotkey", lambda: hotkey)
    monkeypatch.setattr(entry, "create_server", lambda **kw: (_ for _ in ()).throw(RuntimeError("server")))
    with pytest.raises(RuntimeError, match="server"):
        entry.main([])
    assert hotkey.stopped


def test_startup_refusal_is_journalled_without_dialog():
    approval = LocalApprovals("refused", FakeHotkey())
    assert approval.preflight() == "refused"
    result = asyncio.run(approval.request("computer_list_apps", {}))
    assert result == {"approved": False, "error": "refused"}
    assert [row["decision"] for row in journal_rows()] == ["deny", "deny"]
    assert journal_rows()[0]["conversation_id"] is None


def test_dead_hotkey_denies_request():
    result = asyncio.run(LocalApprovals(None, FakeHotkey(False)).request("computer_click", PARAMS))
    assert result == {"approved": False, "error":
                      "Durdurma kısayolu (Ctrl+Alt+Esc) çalışmıyor; bilgisayar kullanımı durduruldu."}
    assert journal_rows()[0]["decision"] == "deny"


def test_ordinary_calls_need_no_grants():
    write_config()
    approval = LocalApprovals(None, FakeHotkey())
    assert approval.preflight() is None
    async def scenario():
        first = await approval.request("computer_window_state", PARAMS)
        second = await approval.request("computer_window_state", PARAMS)
        assert first["approved"] and second["approved"]
    asyncio.run(scenario())
    assert [row["decision"] for row in journal_rows()] == ["allow", "allow"]
    assert not hasattr(approval, "ask") and not hasattr(approval, "_session_grants")


@pytest.mark.parametrize("confirmed", [False, True])
def test_critical_request_requires_confirmation(confirmed):
    params = {**PARAMS, "critical_reason": "Delete", "confirmed": confirmed}
    result = asyncio.run(LocalApprovals(None, FakeHotkey(), confirm_available=True).request("computer_click", params))
    assert result["approved"] is confirmed
    if not confirmed:
        assert "args.confirm" in result["error"]
    assert journal_rows()[0]["decision"] == ("confirmed" if confirmed else "deny")


def test_risky_step_needs_confirmation_every_time():
    approval = LocalApprovals(None, FakeHotkey(), confirm_available=True)
    async def scenario():
        params = {**PARAMS, "risky": True, "risk_reason": "Delete?"}
        assert not (await approval.request("computer_click", params))["approved"]
        assert (await approval.request("computer_click", {**params, "confirmed": True}))["approved"]
        assert not (await approval.request("computer_click", params))["approved"]
        assert (await approval.request("computer_confirm_risky_step", params))["approved"]
    asyncio.run(scenario())
    assert [row["decision"] for row in journal_rows()] == ["deny", "confirmed", "deny", "confirmed"]


@pytest.mark.parametrize("confirmed", [False, True])
def test_halt_requires_server_restart(confirmed):
    hotkey = FakeHotkey()
    hotkey.halted_at = 0.0
    approval = LocalApprovals(None, hotkey, confirm_available=True)
    result = asyncio.run(approval.request("computer_list_apps", {"confirmed": confirmed}))
    assert result["approved"] is False
    assert hotkey.halted_at == 0.0
    assert result["error"] == gate.HALTED_REASON + " Bilgisayar kullanımını sürdürmek için sunucuyu yeniden başlat (Claude Code: /mcp)."


@pytest.mark.parametrize("halted, enabled, expected", [(True, False, "halted"),
                                                      (False, False, "disabled"),
                                                      (False, True, None)])
def test_batch_stop(halted, enabled, expected):
    write_config(enabled=enabled)
    hotkey = FakeHotkey()
    hotkey.halted_at = 1 if halted else None
    assert asyncio.run(LocalApprovals(None, hotkey).batch_stop({})) == expected


def test_requests_are_serialized_and_journalled():
    write_config()
    async def scenario():
        approval = LocalApprovals(None, FakeHotkey())
        await approval._lock.acquire()
        first = asyncio.create_task(approval.request("computer_click", PARAMS))
        second = asyncio.create_task(approval.request("computer_list_apps", {}))
        await asyncio.sleep(0)
        assert not first.done() and not second.done()
        assert not config.journal_path().exists()
        approval._lock.release()
        assert all(row["approved"] for row in await asyncio.gather(first, second))
    asyncio.run(scenario())
    rows = journal_rows()
    assert len(rows) == 2 and [row["tool"] for row in rows] == ["computer_click", "computer_list_apps"]
    assert all(row["conversation_id"] is None and row["ts"] for row in rows)
    assert rows[0]["pid"] == 41 and rows[0]["bundle_id"] == APP


@pytest.mark.parametrize("kind", ["missing", "wrong-name", "wrong-hash", "relative", "null"])
def test_runtime_rejects_unverified_file(kind):
    path = config.STATE_DIR / ("other.exe" if kind == "wrong-name" else "cua-driver.exe")
    if kind != "missing":
        path.write_bytes(b"not the pinned driver")
    value = None if kind == "null" else "cua-driver.exe" if kind == "relative" else str(path)
    write_config(driver_path=value)
    async def scenario():
        instance = runtime.StandaloneRuntime()
        with pytest.raises(DriverUnavailable, match="doğrulanamadı"):
            await instance()
        assert instance._daemon is None
        await instance.aclose()
        await instance.aclose()
    asyncio.run(scenario())


class FakeDaemon:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -1

    async def wait(self):
        return self.returncode


def verified_driver(monkeypatch):
    path = config.STATE_DIR / "cua-driver.exe"
    path.write_bytes(b"test driver")
    monkeypatch.setattr(config, "EXE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    write_config(driver_path=str(path))
    return path


def test_runtime_owns_single_daemon_and_filtered_environment(monkeypatch):
    path = verified_driver(monkeypatch)
    daemon = FakeDaemon()
    spawn = AsyncMock(return_value=daemon)
    env = {"CUA_DRIVER_EMBEDDED": "1"}
    monkeypatch.setattr(platform, "current", lambda: SimpleNamespace(driver_command=lambda *a: ([], env)))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(runtime, "_pipe_ready", lambda p: True)
    async def scenario():
        instance = runtime.StandaloneRuntime()
        results = await asyncio.gather(instance(), instance())
        assert results == [(str(path), instance._pipe)] * 2
        assert instance._pipe.startswith("\\\\.\\pipe\\gamachine-cu-standalone-")
        await instance.aclose()
        await instance.aclose()
    asyncio.run(scenario())
    spawn.assert_awaited_once()
    assert spawn.call_args.args[1:] == ("serve", "--embedded", "--socket", spawn.call_args.args[4], "--no-overlay")
    assert spawn.call_args.kwargs["env"] is env
    assert daemon.terminated and not daemon.killed


def test_runtime_rehashes_before_restart(monkeypatch):
    path = verified_driver(monkeypatch)
    daemon = FakeDaemon()
    spawn = AsyncMock(return_value=daemon)
    monkeypatch.setattr(platform, "current", lambda: SimpleNamespace(driver_command=lambda *a: ([], {})))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(runtime, "_pipe_ready", lambda p: True)
    async def scenario():
        instance = runtime.StandaloneRuntime()
        await instance()
        daemon.returncode = 1
        path.write_bytes(b"tampered")
        with pytest.raises(DriverUnavailable, match="doğrulanamadı"):
            await instance()
        await instance.aclose()
    asyncio.run(scenario())
    assert spawn.await_count == 1


def test_runtime_daemon_exit_is_refused(monkeypatch):
    verified_driver(monkeypatch)
    daemon = FakeDaemon()
    daemon.returncode = 1
    monkeypatch.setattr(platform, "current", lambda: SimpleNamespace(driver_command=lambda *a: ([], {})))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=daemon))
    async def scenario():
        instance = runtime.StandaloneRuntime()
        with pytest.raises(DriverUnavailable, match="başlatılamadı"):
            await instance()
        assert instance._log is None and instance._daemon is None
    asyncio.run(scenario())


def test_server_name_and_unsupported_tools():
    async def scenario():
        mcp = server.create_server(name="local-test", approvals=object(), runtime=AsyncMock())
        assert mcp.name == "local-test"
        tools = {tool.name for tool in await mcp.list_tools()}
        assert tools == set(policy.TOOLS) - {"computer_launch_app", "computer_invoke_menu"}
    asyncio.run(scenario())


def test_server_runtime_injection_and_close_order(monkeypatch):
    order = []
    class FakeRuntime:
        async def __call__(self):
            return "driver", "pipe"
        async def aclose(self):
            order.append("runtime")
    class FakeDriver:
        def __init__(self, binary, socket):
            self.binary, self.socket = binary, socket
        async def close(self):
            order.append("driver")
    monkeypatch.setattr(server, "DriverClient", FakeDriver)
    async def scenario():
        instance = server.ComputerServer(runtime=FakeRuntime())
        first = await instance._runtime_driver()
        assert await instance._runtime_driver() is first
        await instance.close()
    asyncio.run(scenario())
    assert order == ["driver", "runtime"]


@windows_only
def test_session_lock_excludes_second_session_and_releases():
    first, second = entry.SessionLock(), entry.SessionLock()
    try:
        assert first.acquire()
        assert not second.acquire()
        first.close()
        assert second.acquire()
    finally:
        first.close()
        second.close()


def test_lock_refusal_precedes_remaining_startup_checks(monkeypatch):
    monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", "present")
    hotkey = FakeHotkey()
    assert entry._startup_refusal(hotkey, SimpleNamespace(acquire=lambda: False)) == (
        "Başka bir oturum bilgisayar kullanımını tutuyor.")
    assert not hotkey.started


@pytest.mark.parametrize("mismatch", [None, "wheel", "exe", "written-exe", "written-helper", "replace", "config-replace"])
def test_installer_pins_artifacts_without_network(monkeypatch, capsys, mismatch):
    binary = b"test driver"
    wheel = io.BytesIO()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("cua_driver/bin/cua-driver.exe", binary)
        archive.writestr("cua_driver/bin/cua-driver-uia.exe", b"helper")
    content = wheel.getvalue()
    monkeypatch.setattr(config, "WHEEL_SHA256", "bad" if mismatch == "wheel" else hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(config, "EXE_SHA256", "bad" if mismatch == "exe" else hashlib.sha256(binary).hexdigest())
    calls = []
    def download(url, timeout):
        calls.append((url, timeout))
        return io.BytesIO(content)
    monkeypatch.setattr(entry.urllib.request, "urlopen", download)
    write_config(extra="preserved")
    directory = config.STATE_DIR / "driver"
    directory.mkdir()
    previous = {"cua-driver.exe": b"previous driver", "cua-driver-uia.exe": b"previous helper"}
    for name, value in previous.items():
        (directory / name).write_bytes(value)
    config_before = (config.STATE_DIR / "config.json").read_bytes()
    original_read = type(directory).read_bytes
    def read_bytes(path):
        data = original_read(path)
        corrupt = "cua-driver.exe.new" if mismatch == "written-exe" else "cua-driver-uia.exe.new"
        return b"corrupted" if mismatch in {"written-exe", "written-helper"} and path.name == corrupt else data
    monkeypatch.setattr(type(directory), "read_bytes", read_bytes)
    original_replace = entry.os.replace
    def replace(source, target):
        name = "config.json.new" if mismatch == "config-replace" else "cua-driver-uia.exe.new"
        if mismatch in {"replace", "config-replace"} and source.name == name:
            raise OSError("replace mismatch")
        return original_replace(source, target)
    monkeypatch.setattr(entry.os, "replace", replace)
    assert entry._install_driver() == (0 if mismatch is None else 1)
    assert calls == [(config.WHEEL_URL, 120)]
    data = json.loads((config.STATE_DIR / "config.json").read_text())
    assert data["extra"] == "preserved" and data["enabled"] is True
    if mismatch:
        assert (config.STATE_DIR / "config.json").read_bytes() == config_before
        assert all((directory / name).read_bytes() == value for name, value in previous.items())
        assert not list(directory.glob("*.new"))
        assert not (config.STATE_DIR / "config.json.new").exists()
        assert "mismatch" in capsys.readouterr().err
    else:
        assert data["driver_path"] == str((config.STATE_DIR / "driver" / "cua-driver.exe").resolve())
        assert (config.STATE_DIR / "driver" / "cua-driver-uia.exe").read_bytes() == b"helper"
