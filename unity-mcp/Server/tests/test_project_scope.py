"""A Gamachine chat's tool call is refused when the targeted editor has another project open."""

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.tools import manage_build as mb  # noqa: E402
from transport import approval_gate, project_scope  # noqa: E402
from fastmcp.exceptions import ResourceError  # noqa: E402
from transport.plugin_hub import PluginHub  # noqa: E402
from transport.unity_instance_middleware import ToolError, UnityInstanceMiddleware  # noqa: E402

CTX = object()
_REAL_TARGET_PATH = mb._target_project_path
UNREACHABLE = "Gamachine'e ulaşılamadı; Unity projesi doğrulanamadı, istek gönderilmedi."


def _run(coro):
    return asyncio.run(coro)


class _Client:
    """httpx.AsyncClient stand-in: records posts, answers from `answer`."""

    posts: list = []
    answer = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        _Client.posts.append((url, json, headers))
        return _Client.answer()


def _reply(body, status=200):
    return types.SimpleNamespace(status_code=status, json=lambda: body)


def _raise_network():
    raise OSError("down")


class _Clock:
    now = 1000.0


@pytest.fixture
def scope(monkeypatch):
    from services.tools import manage_build

    project_scope._cache.clear()
    _Client.posts = []
    _Client.answer = lambda: _reply({"ok": True, "detail": None})
    _Clock.now = 1000.0
    monkeypatch.setenv("LOCAL_APP_TOKEN", "tok")
    monkeypatch.setattr(project_scope.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(project_scope.time, "monotonic", lambda: _Clock.now)
    monkeypatch.setattr(PluginHub, "session_wait_budget", classmethod(lambda cls: 0.0))
    monkeypatch.setattr(manage_build, "_target_project_path", AsyncMock(return_value="/projects/GameB"))
    return _Client


def test_no_check_without_conversation_id(scope):
    assert _run(project_scope.project_scope_refusal(CTX, None)) is None
    assert scope.posts == []


def test_no_check_without_app_token(scope, monkeypatch):
    monkeypatch.delenv("LOCAL_APP_TOKEN")
    assert _run(project_scope.project_scope_refusal(CTX, 5)) is None
    assert scope.posts == []


def test_missing_fastmcp_context_is_still_checked(scope):
    # No context means no resolvable editor, never a free pass.
    scope.answer = lambda: _reply({"ok": False, "detail": "yol yok"})
    assert _run(project_scope.project_scope_refusal(None, 5)) == "yol yok"
    assert len(scope.posts) == 1


def test_sends_conversation_and_target_path_with_token(scope):
    assert _run(project_scope.project_scope_refusal(CTX, 5)) is None
    url, body, headers = scope.posts[0]
    assert url.endswith("/mcp-project-check")
    assert body == {"conversation_id": 5, "project_path": "/projects/GameB"}
    assert headers == {"X-Session-Token": "tok"}


def test_backend_refusal_text_is_returned(scope):
    scope.answer = lambda: _reply({"ok": False, "detail": "başka proje"})
    assert _run(project_scope.project_scope_refusal(CTX, 5)) == "başka proje"


def test_unresolved_target_is_sent_as_empty_path(scope, monkeypatch):
    from services.tools import manage_build

    monkeypatch.setattr(manage_build, "_target_project_path", AsyncMock(return_value=None))
    _run(project_scope.project_scope_refusal(CTX, 5))
    assert scope.posts[0][1]["project_path"] == ""


@pytest.mark.parametrize("answer", [
    _raise_network,
    lambda: _reply({"ok": True}, status=500),
    lambda: _reply(["ok"]),
    lambda: _reply({"ok": "yes"}),
    lambda: _reply({}),
])
def test_fails_closed(scope, answer):
    scope.answer = answer
    assert _run(project_scope.project_scope_refusal(CTX, 5)) == UNREACHABLE


def test_answer_is_cached_for_three_seconds(scope):
    _run(project_scope.project_scope_refusal(CTX, 5))
    _Clock.now += 2.9
    _run(project_scope.project_scope_refusal(CTX, 5))
    assert len(scope.posts) == 1
    _Clock.now += 0.2
    _run(project_scope.project_scope_refusal(CTX, 5))
    assert len(scope.posts) == 2


def test_network_failure_is_not_cached(scope):
    scope.answer = _raise_network
    assert _run(project_scope.project_scope_refusal(CTX, 5)) == UNREACHABLE
    scope.answer = lambda: _reply({"ok": True, "detail": None})
    assert _run(project_scope.project_scope_refusal(CTX, 5)) is None
    assert len(scope.posts) == 2


def test_real_target_path_resolution_feeds_the_check(scope, monkeypatch):
    monkeypatch.setattr(mb, "_target_project_path", _REAL_TARGET_PATH)
    monkeypatch.setattr(mb, "get_unity_instance_from_context", AsyncMock(return_value="Game@hash-one"))
    monkeypatch.setattr(mb.config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "get_project_path", AsyncMock(return_value="/projects/GameA"))
    _run(project_scope.project_scope_refusal(CTX, 9))
    assert scope.posts[0][1] == {"conversation_id": 9, "project_path": "/projects/GameA"}


class _Ctx:
    def __init__(self, name="manage_gameobject", arguments=None):
        self.message = types.SimpleNamespace(name=name, arguments=arguments or {"action": "create"})
        self.fastmcp_context = object()


def _middleware(monkeypatch):
    mw = UnityInstanceMiddleware()

    async def no_inject(_ctx):
        return None

    monkeypatch.setattr(mw, "_inject_unity_instance", no_inject)
    monkeypatch.setattr(mw, "_request_conversation_id", lambda: 5)
    return mw


def test_refusal_is_raised_before_the_approval_gate(scope, monkeypatch):
    scope.answer = lambda: _reply({"ok": False, "detail": "başka proje"})
    mw = _middleware(monkeypatch)
    gate_calls, reached = [], []

    async def gate(*a, **k):
        gate_calls.append(a)
        return {"approved": True}

    async def call_next(_ctx):
        reached.append(True)

    monkeypatch.setattr(approval_gate, "_onay_iste", gate)
    with pytest.raises(ToolError, match="başka proje"):
        _run(mw.on_call_tool(_Ctx(), call_next))
    assert gate_calls == [] and reached == []


def test_reads_are_checked_too(scope, monkeypatch):
    scope.answer = lambda: _reply({"ok": False, "detail": "başka proje"})
    mw = _middleware(monkeypatch)
    with pytest.raises(ToolError, match="başka proje"):
        _run(mw.on_call_tool(_Ctx("read_console", {"action": "get"}), AsyncMock()))


def test_resource_reads_are_checked_too(scope, monkeypatch):
    scope.answer = lambda: _reply({"ok": False, "detail": "başka proje"})
    mw = _middleware(monkeypatch)
    reached = []

    async def handler(_ctx):
        reached.append(True)

    with pytest.raises(ResourceError, match="başka proje"):
        _run(mw.on_read_resource(_Ctx(), handler))
    assert reached == []


@pytest.mark.parametrize("name", ["set_active_instance", "manage_script_capabilities"])
def test_server_only_tools_are_not_checked(scope, monkeypatch, name):
    scope.answer = lambda: _reply({"ok": False, "detail": "başka proje"})
    mw = _middleware(monkeypatch)
    monkeypatch.setattr(mw, "_require_approval", AsyncMock())
    call_next = AsyncMock(return_value="done")
    assert _run(mw.on_call_tool(_Ctx(name, {}), call_next)) == "done"
    assert scope.posts == []


def test_unity_reaching_tools_marked_unity_target_none_stay_checked():
    for name in ("execute_custom_tool", "unity_dialog", "unity_window_capture", "manage_gameobject"):
        assert project_scope.is_server_only_tool(name) is False


def test_stale_cache_entries_are_dropped_on_store(scope):
    _run(project_scope.project_scope_refusal(CTX, 1))
    _Clock.now += 10
    _run(project_scope.project_scope_refusal(CTX, 2))
    assert list(project_scope._cache) == [(2, "/projects/GameB")]


def test_waits_for_the_session_after_a_domain_reload(scope, monkeypatch):
    answers = iter(["", "", "/projects/GameA"])
    monkeypatch.setattr(PluginHub, "session_wait_budget", classmethod(lambda cls: 5.0))
    monkeypatch.setattr(mb, "_target_project_path", AsyncMock(side_effect=lambda ctx: next(answers)))
    monkeypatch.setattr(project_scope, "_RELOAD_POLL_S", 0)
    assert _run(project_scope.project_scope_refusal(CTX, 5)) is None
    assert scope.posts[0][1]["project_path"] == "/projects/GameA"


def test_gives_up_waiting_when_the_budget_is_spent(scope, monkeypatch):
    monkeypatch.setattr(PluginHub, "session_wait_budget", classmethod(lambda cls: 1.0))
    monkeypatch.setattr(mb, "_target_project_path", AsyncMock(return_value=""))

    async def tick(_seconds):
        _Clock.now += 0.5

    monkeypatch.setattr(project_scope.asyncio, "sleep", tick)
    _run(project_scope.project_scope_refusal(CTX, 5))
    assert scope.posts[0][1]["project_path"] == ""


def test_session_wait_budget_is_clamped(monkeypatch):
    for raw, expected in [("5", 5.0), ("99", 20.0), ("-3", 0.0), ("junk", 20.0)]:
        monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", raw)
        assert PluginHub.session_wait_budget() == expected


def _sessions(*entries):
    return dict(entries)


def test_select_session_by_path_picks_the_one_matching_session(tmp_path):
    a, b = tmp_path / "GameA", tmp_path / "GameB"
    a.mkdir()
    b.mkdir()
    sessions = _sessions(("s1", str(b)), ("s2", str(a) + "/"))
    assert project_scope.select_session_by_path(sessions, str(a)) == ("s2", None)


def test_select_session_by_path_follows_symlinks(tmp_path):
    a = tmp_path / "GameA"
    a.mkdir()
    link = tmp_path / "link"
    link.symlink_to(a)
    assert project_scope.select_session_by_path(_sessions(("s1", str(link))), str(a)) == ("s1", None)


def test_select_session_by_path_none_open(tmp_path):
    sessions = _sessions(("s1", str(tmp_path / "GameB")), ("s2", ""))
    assert project_scope.select_session_by_path(sessions, str(tmp_path / "GameA")) == (
        None, "Unity'de bu proje açık değil: GameA.")


def test_select_session_by_path_several_open(tmp_path):
    p = str(tmp_path / "GameA")
    sessions = _sessions(("s1", p), ("s2", p))
    assert project_scope.select_session_by_path(sessions, p) == (
        None, "Bu proje Unity'de birden fazla kez açık: GameA; hangisine gönderileceği belirsiz.")


def test_two_editors_and_no_target_is_refused_without_waiting(scope, monkeypatch):
    monkeypatch.setattr(PluginHub, "session_project_paths",
                        AsyncMock(return_value={"s1": "/p/A", "s2": "/p/B"}))
    monkeypatch.setattr(PluginHub, "session_wait_budget", classmethod(lambda cls: 20.0))
    monkeypatch.setattr(mb, "_target_project_path", AsyncMock(return_value=""))
    slept = []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(project_scope.asyncio, "sleep", no_sleep)
    refusal = _run(project_scope.project_scope_refusal(CTX, 5))
    assert refusal == "Birden fazla Unity editörü bağlı ve hedef seçilmedi; istek gönderilmedi."
    assert slept == [] and scope.posts == [] and project_scope._cache == {}


def test_two_editors_with_a_named_target_is_still_checked(scope, monkeypatch):
    monkeypatch.setattr(PluginHub, "session_project_paths",
                        AsyncMock(return_value={"s1": "/p/A", "s2": "/p/B"}))
    monkeypatch.setattr(project_scope, "_resolve_project_path", AsyncMock(return_value="/p/A"))
    import services.tools as tools

    monkeypatch.setattr(tools, "get_unity_instance_from_context", AsyncMock(return_value="A@hash"))
    assert _run(project_scope.project_scope_refusal(CTX, 5)) is None
    assert len(scope.posts) == 1


def test_counting_failure_is_a_refusal(scope, monkeypatch):
    monkeypatch.setattr(PluginHub, "session_project_paths", AsyncMock(side_effect=RuntimeError("boom")))
    assert _run(project_scope.project_scope_refusal(CTX, 5)) == UNREACHABLE
    assert scope.posts == []
