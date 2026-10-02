"""Independent subscription usage: fixture parsing and fake-only probes."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import usage_limits as ul
from routes.usage_routes import create_usage_router


FIXTURES = Path(__file__).parent / "fixtures" / "usage"
NOW = datetime(2026, 10, 2, 8)


def fixture(name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith("json") else text


def data(pct=17):
    return {"plan": "plus", "windows": [{
        "id": "5h", "group": None, "label": "5-hour limit", "kind": "5h",
        "used_pct": pct, "resets_at": None, "resets_text": None,
    }]}


def test_claude_fixture_and_local_reset():
    parsed = ul.parse_claude_usage(fixture("claude_usage.txt"), now=NOW)
    windows = parsed["windows"]
    assert parsed["plan"] is None
    assert [w["id"] for w in windows] == ["session", "week", "week-fable"]
    assert [w["kind"] for w in windows] == ["5h", "week", "week"]
    assert [w["used_pct"] for w in windows] == [35, 41, 0]
    assert [w["group"] for w in windows] == [None, None, "Fable"]
    assert [w["label"] for w in windows] == [
        "Current session", "Current week (all models)", "Current week (Fable)"]
    assert windows[0]["resets_text"] == "Oct 2, 9:50am (Europe/Istanbul)"
    assert windows[0]["resets_at"] == datetime(2026, 10, 2, 9, 50).astimezone(
        timezone.utc).isoformat()
    assert windows[1]["resets_text"] == "Oct 8, 2am (Europe/Istanbul)"
    assert windows[1]["resets_at"] == datetime(2026, 10, 8, 2).astimezone(
        timezone.utc).isoformat()


def test_claude_rollover_parse_miss_and_recent_past():
    text = "Current session: 1% used · resets Jan 1, 12am (Local)"
    w = ul.parse_claude_usage(text, now=datetime(2026, 12, 31))["windows"][0]
    assert w["resets_at"] == datetime(2027, 1, 1).astimezone(timezone.utc).isoformat()
    recent = ul.parse_claude_usage(text, now=datetime(2026, 1, 1, 23))["windows"][0]
    assert recent["resets_at"] == datetime(2026, 1, 1).astimezone(timezone.utc).isoformat()
    w = ul.parse_claude_usage("Current session: 1% used · resets unknown")["windows"][0]
    assert w["resets_at"] is None and w["resets_text"] == "unknown"


def test_codex_fixture_and_other_durations():
    parsed = ul.parse_codex_ratelimits(fixture("codex_ratelimits.json"))
    assert parsed["plan"] == "plus"
    windows = parsed["windows"]
    assert [w["id"] for w in windows] == ["5h", "week"]
    assert [w["kind"] for w in windows] == ["5h", "week"]
    assert [w["used_pct"] for w in windows] == [17, 36]
    assert [w["resets_at"] for w in windows] == [
        datetime.fromtimestamp(n, timezone.utc).isoformat() for n in (1790926753, 1791304030)]
    result = {"rateLimits": {"primary": {"usedPercent": 150, "windowDurationMins": 60},
                             "secondary": {"usedPercent": -1, "windowDurationMins": 1440}}}
    windows = ul.parse_codex_ratelimits(result)["windows"]
    assert [(w["id"], w["kind"], w["used_pct"]) for w in windows] == [
        ("w60", "5h", 100), ("w1440", "week", 0)]


def test_agy_fixture():
    parsed = ul.parse_agy_usage(fixture("agy_usage.json"))
    windows = parsed["windows"]
    assert len(windows) == 4
    assert [w["id"] for w in windows] == ["gemini-weekly", "gemini-5h", "3p-weekly", "3p-5h"]
    assert [w["used_pct"] for w in windows] == [3, 0, 0, 0]
    assert [w["group"] for w in windows] == ["Gemini Models"] * 2 + ["Claude and GPT models"] * 2
    assert [w["kind"] for w in windows] == ["week", "5h", "week", "5h"]
    assert [w["resets_at"] for w in windows] == [
        "2026-10-02T09:00:23Z", "2026-10-02T10:04:51Z",
        "2026-10-09T05:47:11Z", "2026-10-02T10:47:11Z"]


@pytest.mark.parametrize("parser", [ul.parse_claude_usage, ul.parse_codex_ratelimits, ul.parse_agy_usage])
@pytest.mark.parametrize("garbage", [None, "", "garbage", {}, [], 5, {"rateLimits": []},
                                     {"command": {"data": {"groups": [None, {"buckets": 1}]}}}])
def test_parser_garbage(parser, garbage):
    assert parser(garbage)["windows"] == []


def test_unknown_claude_lines_ignored():
    assert ul.parse_claude_usage("Unknown: 5% used · resets Oct 2, 9am\n94% of usage")["windows"] == []


async def test_service_single_flight_ttl_force_and_copy():
    clock = [0]
    release = asyncio.Event()
    calls = []

    async def fetch():
        calls.append(1)
        await release.wait()
        return data()

    service = ul.UsageService(dict.fromkeys(ul.FAMILIES, fetch), clock=lambda: clock[0])
    try:
        first = service.snapshot()
        assert [e["family"] for e in first["families"]] == list(ul.FAMILIES)
        assert all(e["status"] == "loading" for e in first["families"])
        assert all(e["measured_at"] is None for e in first["families"])
        datetime.fromisoformat(first["now"])
        service.snapshot(force=True)
        await asyncio.sleep(0)
        assert len(calls) == 3
        pending = [asyncio.create_task(service.refresh(f)) for f in ul.FAMILIES]
        await asyncio.sleep(0)
        assert len(calls) == 3
        release.set()
        await asyncio.gather(*pending)
        entries = service.snapshot()["families"]
        assert all(e["status"] == "ok" and not e["stale"] for e in entries)
        assert all(e["measured_at"] is not None for e in entries)
        entries[0]["windows"][0]["used_pct"] = 99
        assert service.snapshot()["families"][0]["windows"][0]["used_pct"] == 17
        clock[0] = 119
        service.snapshot()
        await asyncio.sleep(0)
        assert len(calls) == 3
        clock[0] = 121
        assert all(e["stale"] for e in service.snapshot()["families"])
        await service.wait_for_refreshes()
        assert len(calls) == 6
        service.snapshot(force=True)
        await service.wait_for_refreshes()
        assert len(calls) == 9
    finally:
        await service.aclose()


async def test_service_errors_are_isolated_and_keep_last_good():
    broken = [False]

    async def fetch():
        if broken[0]:
            raise RuntimeError("probe failed")
        return data(35)

    async def good():
        return data()

    service = ul.UsageService({"claude": fetch, "codex": good, "agy": None})
    try:
        service.snapshot()
        await service.wait_for_refreshes()
        broken[0] = True
        service.snapshot(force=True)
        await service.wait_for_refreshes()
        c, x, a = service.snapshot()["families"]
        assert c["status"] == "error" and c["stale"] and c["error"] == "probe failed"
        assert c["windows"][0]["used_pct"] == 35
        assert x["status"] == "ok" and not x["stale"]
        assert a["status"] == "unavailable" and a["windows"] == []
    finally:
        await service.aclose()
    service = ul.UsageService({"claude": fetch})
    try:
        await service.refresh("claude")
        assert service.snapshot()["families"][0]["windows"] == []
    finally:
        await service.aclose()


async def test_service_unavailable_and_close_cancel():
    cancelled = asyncio.Event()
    started = asyncio.Event()

    async def waiting():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def unavailable():
        raise ul.CLIUnavailable("missing")

    service = ul.UsageService({"claude": waiting, "codex": unavailable, "agy": None})
    service.snapshot()
    await started.wait()
    await service.refresh("codex")
    assert service.snapshot(force=True)["families"][1]["status"] == "unavailable"
    assert set(service._tasks) == {"claude"}
    await service.aclose()
    assert cancelled.is_set()
    assert not service._tasks
    assert not any(e["status"] == "loading" for e in service.snapshot(force=True)["families"])


def fake_messages(monkeypatch):
    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, text):
            self.content = [TextBlock(text)]

    class ResultMessage:
        is_error = False

    import claude_agent_sdk
    for cls in (TextBlock, AssistantMessage, ResultMessage):
        monkeypatch.setattr(claude_agent_sdk, cls.__name__, cls)
    return AssistantMessage, ResultMessage


async def test_claude_probe_reuse_error_rebuild_and_service_close(monkeypatch):
    AssistantMessage, ResultMessage = fake_messages(monkeypatch)
    clients = []

    class Client:
        connected = 0
        closed = 0
        broken = False

        async def connect(self):
            self.connected += 1
            self.owner = asyncio.current_task()

        async def disconnect(self):
            assert asyncio.current_task() is self.owner
            self.closed += 1

        async def query(self, text):
            assert asyncio.current_task() is self.owner
            assert text == "/usage"
            if self.broken:
                raise RuntimeError("broken client")

        async def receive_response(self):
            yield SimpleNamespace(content=[SimpleNamespace(text="ignore system")])
            yield AssistantMessage(fixture("claude_usage.txt"))
            yield ResultMessage()

    def factory():
        client = Client()
        clients.append(client)
        return client

    probe = ul.ClaudeUsageProbe(cwd=".", client_factory=factory)
    service = ul.UsageService({"claude": probe.fetch})
    await service.refresh("claude")
    await service.refresh("claude")
    assert len(clients) == 1 and clients[0].connected == 1
    clients[0].broken = True
    await service.refresh("claude")
    assert clients[0].closed == 1
    await service.refresh("claude")
    assert len(clients) == 2 and clients[1].connected == 1
    await service.aclose()
    assert clients[1].closed == 1


async def test_service_close_before_first_task_runs():
    async def forbidden():
        pytest.fail("fetch must be cancelled before it starts")

    service = ul.UsageService({"claude": forbidden})
    service.snapshot()
    await service.aclose()
    assert service.snapshot()["families"][0]["status"] == "error"
    assert not service._tasks


async def test_claude_timeout_closes_in_owner_task(monkeypatch):
    fake_messages(monkeypatch)
    monkeypatch.setattr(ul, "FETCH_TIMEOUT_S", 0.01)
    closed = []

    class Client:
        async def connect(self):
            self.owner = asyncio.current_task()

        async def disconnect(self):
            assert asyncio.current_task() is self.owner
            closed.append(1)

        async def query(self, text):
            await asyncio.Event().wait()

    probe = ul.ClaudeUsageProbe(client_factory=Client)
    try:
        with pytest.raises(TimeoutError):
            await probe.fetch()
        assert closed == [1]
    finally:
        await probe.aclose()


async def test_unavailable_fetchers_never_spawn(monkeypatch):
    from providers import agy_provider, claude_sdk_session
    monkeypatch.setattr(ul.shutil, "which", lambda name: None)
    monkeypatch.setattr(ul.os.path, "isfile", lambda path: False)
    monkeypatch.setattr(claude_sdk_session, "claude_ikilisini_coz", lambda: None)
    monkeypatch.setattr(agy_provider.AgyProvider, "_agy_binary", lambda: "agy")

    async def forbidden(*args, **kwargs):
        pytest.fail("unavailable family spawned a process")

    monkeypatch.setattr(ul.asyncio, "create_subprocess_exec", forbidden)
    probe = ul.ClaudeUsageProbe()
    try:
        for fetch in (probe.fetch, ul.fetch_codex_usage, ul.fetch_agy_usage):
            with pytest.raises(ul.CLIUnavailable):
                await fetch()
    finally:
        await probe.aclose()


class FakeStdin:
    def __init__(self):
        self.messages = []

    def write(self, payload):
        self.messages.append(json.loads(payload))

    async def drain(self):
        pass


@pytest.mark.parametrize("mode", ["ok", "rpc_error", "eof", "timeout", "cancel"])
async def test_codex_protocol_and_cleanup(monkeypatch, mode):
    from providers import cli_base, codex_session
    monkeypatch.setattr(ul.shutil, "which", lambda name: name)
    monkeypatch.setattr(codex_session, "_resolve_codex_appserver_cmd", lambda: ["fake-codex", "app-server"])
    monkeypatch.setattr(cli_base, "build_spawn_env", lambda **kw: {"family": kw["family"]})
    if mode == "timeout":
        monkeypatch.setattr(ul, "FETCH_TIMEOUT_S", 0.01)
    started = asyncio.Event()
    lines = [b"diagnostic\n", b'[{"ignore": true}]\n',
             b'{"method":"notification"}\n', b'{"id":1,"result":{}}\n']
    response = {"id": 2, "result": fixture("codex_ratelimits.json")}
    if mode == "rpc_error":
        response = {"id": 2, "error": {"message": "not logged in"}}
    lines.append((json.dumps(response) + "\n").encode())

    class Stdout:
        async def readline(self):
            if mode in ("timeout", "cancel"):
                started.set()
                await asyncio.Event().wait()
            if mode == "eof":
                return b""
            return lines.pop(0)

    proc = SimpleNamespace(stdin=FakeStdin(), stdout=Stdout())
    killed = []

    async def spawn(*args, **kwargs):
        assert args == ("fake-codex", "app-server")
        assert kwargs["env"] == {"family": "codex"}
        return proc

    async def kill(child):
        assert child is proc
        killed.append(child)

    monkeypatch.setattr(ul.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(ul, "_kill_probe", kill)
    if mode == "ok":
        result = await ul.fetch_codex_usage()
        assert result["plan"] == "plus" and len(result["windows"]) == 2
        assert [m["method"] for m in proc.stdin.messages] == [
            "initialize", "initialized", "account/rateLimits/read"]
    elif mode == "cancel":
        task = asyncio.create_task(ul.fetch_codex_usage())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(TimeoutError if mode == "timeout" else RuntimeError):
            await ul.fetch_codex_usage()
    assert killed == [proc]


@pytest.mark.parametrize("mode", ["ok", "exit", "invalid_json", "timeout"])
async def test_agy_command_env_and_cleanup(monkeypatch, mode):
    from providers import cli_base
    monkeypatch.setattr(ul, "_agy_binary", lambda: "fake-agy")
    monkeypatch.setattr(cli_base.BaseCLIProvider, "_resolve_exec", lambda args: args)
    monkeypatch.setattr(cli_base, "build_spawn_env", lambda **kw: {"family": kw["family"]})
    if mode == "timeout":
        monkeypatch.setattr(ul, "FETCH_TIMEOUT_S", 0.01)

    class Proc:
        returncode = 1 if mode == "exit" else 0

        async def communicate(self):
            if mode == "timeout":
                await asyncio.Event().wait()
            return (b"invalid" if mode == "invalid_json" else json.dumps(fixture("agy_usage.json")).encode(), b"failed")

    proc = Proc()
    killed = []

    async def spawn(*args, **kwargs):
        assert args == ("fake-agy", "-p", "/usage", "--output-format", "json")
        assert kwargs["env"] == {"family": "agy"}
        return proc

    async def kill(child):
        killed.append(child)

    monkeypatch.setattr(ul.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(ul, "_kill_probe", kill)
    if mode == "ok":
        assert len((await ul.fetch_agy_usage())["windows"]) == 4
    else:
        expected = TimeoutError if mode == "timeout" else (ValueError if mode == "invalid_json" else RuntimeError)
        with pytest.raises(expected):
            await ul.fetch_agy_usage()
    assert killed == [proc]


@pytest.fixture
def route_app():
    calls = []

    async def fetch():
        calls.append(1)
        await asyncio.sleep(0.01)
        return data()

    service = ul.UsageService(dict.fromkeys(ul.FAMILIES, fetch))
    app = FastAPI()
    app.include_router(create_usage_router(service))
    yield app, service, calls


def test_route_token_gate(route_app, monkeypatch):
    app, service, calls = route_app
    monkeypatch.setenv("LOCAL_APP_TOKEN", "test-usage-token")
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
    with TestClient(app) as client:
        assert client.get("/usage/limits").status_code == 401
        assert client.get("/usage/limits", headers={"X-Session-Token": "wrong"}).status_code == 401
        assert not calls and not service._tasks
        response = client.get("/usage/limits?wait=1", headers={"X-Session-Token": "test-usage-token"})
        assert response.status_code == 200
        assert all(e["status"] == "ok" for e in response.json()["families"])
        client.portal.call(service.aclose)


def test_route_loading_wait_and_force(route_app):
    app, service, calls = route_app
    with TestClient(app) as client:
        first = client.get("/usage/limits")
        assert first.status_code == 200
        assert all(e["status"] == "loading" for e in first.json()["families"])
        assert all(e["status"] == "ok" for e in client.get("/usage/limits?wait=1").json()["families"])
        assert len(calls) == 3
        client.get("/usage/limits?wait=1")
        assert len(calls) == 3
        response = client.get("/usage/limits?refresh=1&wait=1")
        assert all(e["status"] == "ok" for e in response.json()["families"])
        assert len(calls) == 6
        client.portal.call(service.aclose)
