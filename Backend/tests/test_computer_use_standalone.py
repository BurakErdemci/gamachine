"""Independent computer-use mode, without native dialogs or desktop input."""
import asyncio
import hashlib
import io
import json
import time
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from computer_use import gate, platform, policy, server
from computer_use.driver_client import DriverUnavailable
from computer_use.platform.windows import WindowsPlatform
from computer_use.standalone import config, dialog, runtime
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


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMACHINE_CU_STANDALONE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    win = WindowsPlatform(SimpleNamespace(set_dpi_awareness=lambda: None, parent_pids=lambda: {}))
    monkeypatch.setattr(platform, "current", lambda: win)
    monkeypatch.setattr(entry._win32, "self_elevated", lambda: False)
    monkeypatch.delenv("GAMACHINE_CONVERSATION_ID", raising=False)
    monkeypatch.setenv("GAMACHINE_CU_STANDALONE", "1")


def write_config(**changes):
    data = {"enabled": True, "driver_path": None, "grants": [], **changes}
    (config.STATE_DIR / "config.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def journal_rows():
    return [json.loads(line) for line in config.journal_path().read_text(encoding="utf-8").splitlines()]


def test_initial_config_is_disabled():
    assert config.load() == {"enabled": False, "driver_path": None, "grants": []}
    assert json.loads((config.STATE_DIR / "config.json").read_text())["enabled"] is False
    assert config.log_path().parent == config.STATE_DIR
    assert config.journal_path().name == "computer_use_actions.jsonl"


@pytest.mark.parametrize("content", ["{broken", "[]", "null", '"text"', "42"])
def test_invalid_config_is_never_overwritten(content):
    path = config.STATE_DIR / "config.json"
    path.write_text(content, encoding="utf-8")
    assert config.load() == {"enabled": False, "driver_path": None, "grants": [],
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


def test_grants_filter_invalid_and_denied_rows():
    good = {"bundle_id": APP, "app_name": "Editor", "granted_at": "2026-10-26T10:00:00+00:00"}
    bad = [None, "app", {}, {**good, "bundle_id": "unknown"},
           {**good, "bundle_id": r"win:c:\apps\cmd.exe"}, {**good, "app_name": " "},
           {**good, "app_name": 3}, {**good, "granted_at": None},
           {**good, "granted_at": "not a date"}]
    write_config(grants=[good, *bad])
    assert config.load()["grants"] == [good]
    write_config(grants="invalid")
    assert config.load()["grants"] == []


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
    assert entry._startup_refusal(hotkey) == expected
    assert hotkey.started is (stage >= 6)


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


def test_main_stops_hotkey_when_server_fails(monkeypatch):
    hotkey = FakeHotkey()
    monkeypatch.setattr(entry, "HaltHotkey", lambda: hotkey)
    monkeypatch.setattr(entry, "create_server", lambda **kw: (_ for _ in ()).throw(RuntimeError("server")))
    with pytest.raises(RuntimeError, match="server"):
        entry.main([])
    assert hotkey.stopped


def test_startup_refusal_is_journalled_without_dialog():
    ask = AsyncMock()
    approval = LocalApprovals("refused", FakeHotkey(), ask=ask)
    result = asyncio.run(approval.request("computer_list_apps", {}))
    assert result == {"approved": False, "error": "refused"}
    ask.assert_not_awaited()
    assert journal_rows()[0]["decision"] == "deny"
    assert journal_rows()[0]["conversation_id"] is None


def test_disabled_request_denied():
    write_config(enabled=False)
    ask = AsyncMock()
    result = asyncio.run(LocalApprovals(None, FakeHotkey(), ask=ask).request("computer_click", PARAMS))
    assert result == {"approved": False, "error": gate.INACTIVE_REASON}
    ask.assert_not_awaited()


@pytest.mark.parametrize("answer", [True, False])
def test_first_app_card_and_session_grant(answer):
    write_config()
    ask = AsyncMock(return_value=answer)
    approval = LocalApprovals(None, FakeHotkey(), ask=ask)
    async def scenario():
        first = await approval.request("computer_window_state", PARAMS)
        second = await approval.request("computer_window_state", PARAMS)
        assert first["approved"] is answer and second["approved"] is answer
    asyncio.run(scenario())
    assert ask.await_count == (1 if answer else 2)
    text = ask.call_args.args[1]
    assert "Editor:" in text and "İşlem: Read document" in text
    assert [row["decision"] for row in journal_rows()] == (
        ["card-approved", "allow"] if answer else ["card-rejected", "card-rejected"])
    if not answer:
        assert journal_rows()[0]["reason"] == "Kullanıcı reddetti."
    assert config.load()["grants"] == []


def test_persisted_grant_is_casefolded(monkeypatch):
    write_config()
    data = {"enabled": True, "grants": [{"bundle_id": APP.upper()}]}
    monkeypatch.setattr(config, "load", lambda: data)
    ask = AsyncMock()
    result = asyncio.run(LocalApprovals(None, FakeHotkey(), ask=ask).request("computer_click", PARAMS))
    assert result["approved"] is True
    ask.assert_not_awaited()


def test_risky_step_always_cards_even_when_granted():
    write_config(grants=[{"bundle_id": APP, "app_name": "Editor", "granted_at": "2026-10-26T10:00:00"}])
    ask = AsyncMock(return_value=True)
    approval = LocalApprovals(None, FakeHotkey(), ask=ask)
    async def scenario():
        for _ in range(2):
            assert (await approval.request("computer_click", {**PARAMS, "risky": True, "risk_reason": "Delete?"}))["approved"]
    asyncio.run(scenario())
    assert ask.await_count == 2
    assert all(row["decision"] == "card-approved" for row in journal_rows())


@pytest.mark.parametrize("elapsed, answer, approved, dialogs", [
    (1, True, False, 0), (4.99, True, False, 0),
    (5, False, False, 1), (6, True, True, 1),
])
def test_halt_and_resume(monkeypatch, elapsed, answer, approved, dialogs):
    write_config()
    monkeypatch.setattr(time, "monotonic", lambda: 100)
    hotkey = FakeHotkey()
    hotkey.halted_at = 100 - elapsed
    ask = AsyncMock(return_value=answer)
    approval = LocalApprovals(None, hotkey, ask=ask)
    result = asyncio.run(approval.request("computer_list_apps", {}))
    assert result["approved"] is approved
    assert ask.await_count == dialogs
    assert (hotkey.halted_at is None) is approved
    if not approved:
        assert result["error"] == gate.HALTED_REASON


@pytest.mark.parametrize("halted, enabled, expected", [(True, False, "halted"),
                                                      (False, False, "disabled"),
                                                      (False, True, None)])
def test_batch_stop(halted, enabled, expected):
    write_config(enabled=enabled)
    hotkey = FakeHotkey()
    hotkey.halted_at = 1 if halted else None
    assert asyncio.run(LocalApprovals(None, hotkey).batch_stop({})) == expected


def test_second_request_waits_for_open_card():
    write_config()
    async def scenario():
        opened, release = asyncio.Event(), asyncio.Event()
        async def ask(*args):
            opened.set()
            await release.wait()
            return True
        approval = LocalApprovals(None, FakeHotkey(), ask=ask)
        first = asyncio.create_task(approval.request("computer_click", PARAMS))
        await asyncio.wait_for(opened.wait(), 2)
        second = asyncio.create_task(approval.request("computer_list_apps", {}))
        await asyncio.sleep(0)
        assert not first.done() and not second.done()
        assert not config.journal_path().exists()
        release.set()
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


@pytest.mark.parametrize("response, expected", [(6, True), (7, False), (0, False)])
def test_dialog_only_accepts_yes(monkeypatch, response, expected):
    titles = []
    def message(hwnd, text, title, flags):
        titles.append(title)
        assert flags == 0x4 | 0x30 | 0x40000 | 0x10000 | 0x100
        return response
    monkeypatch.setattr(dialog, "_user32", lambda: SimpleNamespace(MessageBoxW=message))
    async def scenario():
        assert await dialog.ask("Consent", "Text") is expected
        assert await dialog.ask("Consent", "Text") is expected
    asyncio.run(scenario())
    assert titles[0].startswith("Consent #") and titles[0] != titles[1]


def test_dialog_timeout_closes_window(monkeypatch):
    import threading
    released = threading.Event()
    closed = []
    def message(*args):
        released.wait(2)
        return 6
    def close(*args):
        closed.append(args)
        released.set()
    api = SimpleNamespace(MessageBoxW=message, FindWindowW=lambda *a: 99, PostMessageW=close)
    monkeypatch.setattr(dialog, "_user32", lambda: api)
    assert asyncio.run(dialog.ask("Consent", "Text", timeout=0.02)) is False
    assert closed == [(99, 0x10, 0, 0)]


@pytest.mark.parametrize("mismatch", [None, "wheel", "exe"])
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
    assert entry._install_driver() == (0 if mismatch is None else 1)
    assert calls == [(config.WHEEL_URL, 120)]
    data = json.loads((config.STATE_DIR / "config.json").read_text())
    assert data["extra"] == "preserved" and data["enabled"] is True
    if mismatch:
        assert not (config.STATE_DIR / "driver" / "cua-driver.exe").exists()
        assert "mismatch" in capsys.readouterr().err
    else:
        assert data["driver_path"] == str((config.STATE_DIR / "driver" / "cua-driver.exe").resolve())
        assert (config.STATE_DIR / "driver" / "cua-driver-uia.exe").read_bytes() == b"helper"
