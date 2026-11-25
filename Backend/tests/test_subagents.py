"""Sub-agents (agentic/subagents.py, routes/subagent_routes.py): a CLI main
agent starts hidden child chats that run in the background; each child turn's
result reaches the parent as one queued input that wakes an idle parent; depth
1; a fuse of 10 automatic parent turns without a user message; stop and delete
cascade to children. Every runner is a fake; no CLI or provider starts, and
`network_guard` fails a test on any connection it did not stub."""
import asyncio
import socket
import sqlite3
import types
from collections import defaultdict

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

import agentic.agent_runner as ar
import routes.conversation_routes as cr
import routes.subagent_routes as sr
from agentic import cards, chat_model, subagents, turn_events, turn_replies, turn_runner, wake_queue
from agentic.command_gates import register_gate
from database import DatabaseManager
from schemas import ChatRequest

MAIN = "gpt-6-sol"
CODEX = "gpt-6-luna"
CLAUDE = "claude-sonnet-5"
DONE = ("done", {"iterations": 1, "stop_reason": "complete", "session_id": "sess-1"})
CATALOG = {"subscription": [
    {"id": CODEX, "name": "GPT-6 Luna", "provider": "subscription"},
    {"id": MAIN, "name": "GPT-6 Sol", "provider": "subscription"},
    {"id": CLAUDE, "name": "Claude Sonnet 5", "provider": "subscription"},
    {"id": "kimi-code/k3", "name": "Kimi K3", "provider": "subscription"},
    {"id": "copilot-gpt-9", "name": "Copilot locked", "provider": "subscription", "disabled": True},
]}
FORBIDDEN_PORTS = {8000, 8080, 8799, 8888}
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def network_guard(monkeypatch):
    """Fail the test on any connection it did not stub: real HTTP transports,
    event-loop connections, and raw sockets except a loopback pair this
    process bound itself (the event loop's self-pipe)."""
    violations = []
    bound = set()
    real_bind, real_connect, real_connect_ex = (
        socket.socket.bind, socket.socket.connect, socket.socket.connect_ex)

    def _bind(self, address):
        real_bind(self, address)
        try:
            bound.add(self.getsockname()[1])
        except OSError:
            pass

    def _allowed(address):
        if isinstance(address, tuple) and len(address) >= 2:
            host, port = address[0], address[1]
            return host in _LOOPBACK and port in bound and port not in FORBIDDEN_PORTS
        return False

    def _connect(self, address):
        if not _allowed(address):
            violations.append(address)
            raise ConnectionRefusedError(f"test network guard: {address}")
        return real_connect(self, address)

    def _connect_ex(self, address):
        if not _allowed(address):
            violations.append(address)
            raise ConnectionRefusedError(f"test network guard: {address}")
        return real_connect_ex(self, address)

    def _http(self, request):
        violations.append(str(request.url))
        raise httpx.ConnectError(f"test network guard: {request.url}")

    async def _ahttp(self, request):
        violations.append(str(request.url))
        raise httpx.ConnectError(f"test network guard: {request.url}")

    async def _loop_connect(self, *a, **kw):
        violations.append((a, kw))
        raise ConnectionRefusedError("test network guard: event-loop connection")

    monkeypatch.setattr(socket.socket, "bind", _bind)
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _http)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _ahttp)
    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "create_connection", _loop_connect)
    yield violations
    assert not violations, f"unstubbed connections: {violations}"


class _Runner:
    """Fake AgentRunner. Per chat: `replies[cid]` is the answer, `scripts[cid]`
    the steps ("hold" waits for `holds[cid]`, "hang" forever, an exception
    raises, a tuple is an event)."""
    replies: dict = {}
    scripts: dict = {}
    holds: dict = {}
    calls: list = []
    kwargs: list = []

    def __init__(self, **kw):
        self.kw = kw
        _Runner.kwargs.append(kw)

    async def run(self, message):
        cid = self.kw["conversation_id"]
        _Runner.calls.append((cid, message))
        steps = _Runner.scripts.get(cid) or [
            ("response", {"content": _Runner.replies.get(cid, f"RAPOR {cid}")}), DONE]
        for step in steps:
            if step == "hold":
                await _Runner.holds.setdefault(cid, asyncio.Event()).wait()
                continue
            if step == "hang":
                await asyncio.Event().wait()
            if isinstance(step, Exception):
                raise step
            yield ar.AgentEvent(*step)

    @classmethod
    def of(cls, cid):
        return [m for c, m in cls.calls if c == cid]


def _ready(families):
    return lambda family: {"installed": family in families, "loggedIn": None}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    monkeypatch.setattr(cr, "AgentRunner", _Runner)
    _Runner.replies, _Runner.scripts, _Runner.holds = {}, {}, {}
    _Runner.calls, _Runner.kwargs = [], []
    turn_replies.clear()
    turn_runner.reset_all()
    wake_queue.reset_all()
    cards.reset()
    turn_events.RING.reset()  # chat ids repeat across tests; a deleted id's ring stays dropped

    async def _no_close(_cid):
        return None

    from providers import agy_session, claude_sdk_session, codex_session, oneshot_cli
    for mod in (claude_sdk_session, codex_session, agy_session):
        monkeypatch.setattr(mod, "close_session", _no_close)
    monkeypatch.setattr(oneshot_cli, "close_conversation_sessions", _no_close)
    for name in ("_EXECUTOR", "_ACCEPT", "_HAS_PENDING"):
        monkeypatch.setattr(turn_runner, name, None, raising=False)
    monkeypatch.setattr(chat_model, "_cli_state", _ready({"codex", "claude"}))
    in_flight = set()
    monkeypatch.setattr(sr, "_turn_in_flight", lambda cid: cid in in_flight)
    db = DatabaseManager(str(tmp_path / "sub.db"))
    db.save_ai_config(1, "subscription", MAIN, "")

    async def catalog(_user_id, refresh=False):
        return CATALOG

    router = cr.create_conversation_router(db, {})
    srouter = sr.create_subagent_router(db, list_models=catalog)
    ns = types.SimpleNamespace(db=db, router=router, srouter=srouter, in_flight=in_flight,
                               path=str(tmp_path / "sub.db"), catalog=catalog)
    yield ns
    turn_replies.clear()
    turn_runner.reset_all()
    wake_queue.reset_all()
    cards.reset()
    cards.set_tagger(None)
    turn_events.RING.reset()
    subagents.reset()


def _ep(router, path, method=None):
    return next(r.endpoint for r in router.routes if getattr(r, "path", "") == path
                and (method is None or method in r.methods))


def _main(env, title="Ana"):
    cid = env.db.create_conversation(1, title)
    env.db.set_conversation_model(cid, "subscription", MAIN)
    env.in_flight.add(cid)
    return cid


async def _start(env, parent, model=CODEX, effort="high", brief="Dosyaları say.", name=None):
    return await _ep(env.srouter, "/subagents/start")(
        body={"conversation_id": parent, "model": model, "effort": effort, "brief": brief,
              "name": name}, x_session_token="")


async def _post(env, path, **body):
    return await _ep(env.srouter, path)(body=body, x_session_token="")


async def _idle(*convs, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    await asyncio.sleep(0)
    while any(turn_runner.busy(c) for c in convs) or turn_runner._TASKS:
        assert loop.time() < end, "turn never ended"
        await asyncio.sleep(0.005)


async def _until(cond, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        assert loop.time() < end, "condition never held"
        await asyncio.sleep(0.005)


def _rows(db, cid):
    return [(m["role"], m["content"]) for m in db.get_conversation_messages(cid)]


def _results(db, cid):
    return [c for r, c in _rows(db, cid) if r == "system" and subagents.is_result_message(c)]


async def _http_turn(env, cid, text):
    resp = await _ep(env.router, "/chat-stream")(
        ChatRequest(conversation_id=cid, message=text, user_id=1),
        x_session_token="", x_ui_lang=None)
    return [c async for c in resp.body_iterator]


# ── start -> background child -> result wakes the idle parent ───────────────
async def test_a_result_reaches_an_idle_parent_as_one_queued_input_and_wakes_it(env):
    parent = _main(env)
    started = await _start(env, parent, name="sayıcı")
    child = started["chat_id"]
    _Runner.replies[child] = "3 dosya var."
    await _idle(parent, child)

    # The child ran its brief with the result contract, on its own model and effort.
    (brief,) = _Runner.of(child)
    assert brief.startswith("Dosyaları say.") and subagents.RESULT_CONTRACT in brief
    child_kw = next(k for k in _Runner.kwargs if k["conversation_id"] == child)
    assert child_kw["model_name"] == CODEX and child_kw["effort_level"] == "high"
    assert _rows(env.db, child)[0] == ("user", brief)
    # A hidden chat keeps its agent name: no first-message or AI title.
    assert env.db.get_conversation_title(child) == "sayıcı"
    # One result row in the parent, and one parent turn that ran on it.
    (result,) = _results(env.db, parent)
    assert result.startswith(f'{subagents.RESULT_MARKER} #{started["agent_id"]} "sayıcı" ({CODEX}): done')
    assert result.endswith("3 dosya var.")
    assert _Runner.of(parent) == [result]
    row = env.db.get_subagent(started["agent_id"])
    assert (row["status"], row["last_result"], row["parent_conv"]) == ("done", "3 dosya var.", parent)
    assert subagents.fuse_count(parent) == 1


async def test_two_results_that_finish_while_the_user_turn_runs_ride_one_parent_turn(env):
    parent = _main(env)
    a = (await _start(env, parent, name="a"))["chat_id"]
    b = (await _start(env, parent, name="b"))["chat_id"]
    for c in (a, b):
        _Runner.scripts[c] = ["hold", ("response", {"content": f"sonuç {c}"}), DONE]
    _Runner.scripts[parent] = ["hold", ("response", {"content": "ana cevap"}), DONE]
    await asyncio.sleep(0.02)
    user = asyncio.create_task(_http_turn(env, parent, "kullanıcı yazıyor"))
    await asyncio.sleep(0.02)
    for c in (a, b):
        _Runner.holds[c].set()
    await _until(lambda: len(_results(env.db, parent)) == 2)
    _Runner.scripts[parent] = None
    _Runner.holds[parent].set()
    await user
    await _idle(parent, a, b)
    turns = _Runner.of(parent)
    assert turns[0] == "kullanıcı yazıyor"
    assert len(turns) == 2 and turns[1].count(subagents.RESULT_MARKER) == 2
    assert f"sonuç {a}" in turns[1] and f"sonuç {b}" in turns[1]
    assert subagents.fuse_count(parent) == 1


# ── message, stop ────────────────────────────────────────────────────────────
async def test_a_message_to_a_running_child_queues_to_its_next_turn(env):
    parent = _main(env)
    started = await _start(env, parent)
    child = started["chat_id"]
    _Runner.scripts[child] = ["hold", ("response", {"content": "bitti"}), DONE]
    await asyncio.sleep(0.02)
    sent = await _post(env, "/subagents/message", conversation_id=parent,
                       agent_id=started["agent_id"], text="Bir de testleri say.")
    assert sent["status"] == "queued" and "next turn boundary" in sent["text"]
    assert len(_Runner.of(child)) == 1
    _Runner.holds[child].set()
    await _idle(parent, child)
    assert _Runner.of(child)[1] == "Bir de testleri say."
    assert len(_results(env.db, parent)) == 2
    assert env.db.get_subagent(started["agent_id"])["status"] == "done"


async def test_stop_cancels_the_running_turn_and_drops_queued_messages(env):
    parent = _main(env)
    started = await _start(env, parent)
    child = started["chat_id"]
    _Runner.scripts[child] = [("text", {"content": "yarım"}), "hang"]
    await asyncio.sleep(0.02)
    await _post(env, "/subagents/message", conversation_id=parent,
                agent_id=started["agent_id"], text="sonra bunu yap")
    stopped = await _post(env, "/subagents/stop", conversation_id=parent,
                          agent_id=started["agent_id"])
    assert stopped["status"] == "stopped"
    await _idle(parent, child)
    assert _Runner.of(child) == [_Runner.of(child)[0]]  # the queued message never ran
    assert env.db.count_turn_inputs(child) == 0
    assert _results(env.db, parent) == [] and _Runner.of(parent) == []
    assert env.db.get_subagent(started["agent_id"])["status"] == "stopped"
    again = await _post(env, "/subagents/stop", conversation_id=parent, agent_id=started["agent_id"])
    assert again["status"] == "stopped" and "not running" in again["text"]


async def test_the_parents_stop_stops_its_children_and_rejects_their_cards(env):
    parent = _main(env)
    started = await _start(env, parent)
    child = started["chat_id"]
    _Runner.scripts[child] = ["hang"]
    await asyncio.sleep(0.02)
    gate = register_gate("child-gate", child, kind="approval", tool="Bash", params={"cmd": "x"})
    assert cards.is_open("child-gate")
    await env.router.stop_chat(parent)
    await _idle(parent, child)
    assert gate.is_set() and not cards.is_open("child-gate")
    assert env.db.get_subagent(started["agent_id"])["status"] == "stopped"
    assert _results(env.db, parent) == []


# ── depth 1 and ownership ────────────────────────────────────────────────────
async def test_a_sub_agent_cannot_start_or_manage_sub_agents(env):
    parent = _main(env)
    started = await _start(env, parent)
    child = started["chat_id"]
    env.in_flight.add(child)
    with pytest.raises(HTTPException) as exc:
        await _start(env, child)
    assert exc.value.status_code == 403 and "depth 1" in exc.value.detail
    with pytest.raises(HTTPException):
        await _ep(env.srouter, "/subagents/list")(conversation_id=child, x_session_token="")
    other = _main(env, "Başka")
    with pytest.raises(HTTPException) as exc:
        await _post(env, "/subagents/stop", conversation_id=other, agent_id=started["agent_id"])
    assert exc.value.status_code == 404
    await _idle(parent, child)
    assert len(env.db.list_subagents(parent)) == 1


async def test_changes_need_a_running_turn_of_the_caller(env):
    parent = _main(env)
    env.in_flight.discard(parent)
    with pytest.raises(HTTPException) as exc:
        await _start(env, parent)
    assert exc.value.status_code == 403
    listed = await _ep(env.srouter, "/subagents/list")(conversation_id=parent, x_session_token="")
    assert listed["text"] == "No sub-agents in this chat."


# ── picks ────────────────────────────────────────────────────────────────────
async def test_model_and_effort_are_checked_against_the_catalog_and_registry(env):
    parent = _main(env)
    for model, effort, status, code in (
            ("kimi-code/k3", "auto", 409, "not_ready"),     # kimi not installed
            ("copilot-gpt-9", "auto", 409, None),           # plan-locked in the catalog
            ("gpt-unknown-1", "auto", 400, None),           # not a catalog model
            (CODEX, "minimal", 400, None),                  # GPT-6 has no minimal
            ("", "auto", 400, None)):
        with pytest.raises(HTTPException) as exc:
            await _start(env, parent, model=model, effort=effort)
        assert exc.value.status_code == status, model
        if code:
            assert exc.value.detail["code"] == code and exc.value.detail["needs"] == "install"
    assert env.db.list_subagents(parent) == []
    # Live-list families take any well-formed id of theirs.
    chat_model_state = _ready({"codex", "claude", "opencode"})
    chat_model._cli_state = chat_model_state
    ok = await _start(env, parent, model="opencode:anthropic/claude-x", effort="medium")
    assert ok["status"] == "running"
    text = (await _ep(env.srouter, "/subagents/models")(conversation_id=parent,
                                                        x_session_token=""))["text"]
    assert "codex:" in text and f"- {CODEX}" in text and "effort: auto, low" in text
    assert "copilot-gpt-9" not in text and "kimi (install)" in text
    await _idle(parent, ok["chat_id"])


# ── the fuse ─────────────────────────────────────────────────────────────────
async def test_the_fuse_holds_the_eleventh_automatic_parent_turn_until_a_user_message(env):
    parent = _main(env)
    for i in range(subagents.FUSE_LIMIT):
        await _start(env, parent, name=f"a{i}")
        await _idle()
    assert len(_Runner.of(parent)) == subagents.FUSE_LIMIT
    eleventh = await _start(env, parent, name="on-birinci")
    _Runner.replies[eleventh["chat_id"]] = "ON BIRINCI SONUC"
    await _idle()
    # Stored and shown, but no parent turn ran on it.
    assert len(_Runner.of(parent)) == subagents.FUSE_LIMIT
    assert any("ON BIRINCI SONUC" in c for c in _results(env.db, parent))
    assert any(c.startswith(subagents.FUSE_MARKER) for r, c in _rows(env.db, parent))
    assert env.db.count_turn_inputs(parent) == 0
    # The user's message resets the fuse and carries the held result.
    await _http_turn(env, parent, "devam et")
    await _idle()
    last = _Runner.of(parent)[-1]
    assert last.startswith("devam et") and "ON BIRINCI SONUC" in last
    assert subagents.fuse_count(parent) == 0
    await _start(env, parent, name="sonra")
    await _idle()
    assert len(_Runner.of(parent)) == subagents.FUSE_LIMIT + 2


# ── delete, hidden, restart ──────────────────────────────────────────────────
async def test_deleting_the_parent_deletes_its_children_and_stops_their_turns(env):
    parent = _main(env)
    done = await _start(env, parent)
    await _idle()
    running = await _start(env, parent)
    _Runner.scripts[running["chat_id"]] = ["hang"]
    await asyncio.sleep(0.02)
    assert turn_runner.busy(running["chat_id"])
    res = await _ep(env.router, "/conversations/{conv_id}", "DELETE")(conv_id=parent,
                                                                       x_session_token="")
    assert set(res["deleted_ids"]) == {parent, done["chat_id"], running["chat_id"]}
    await _idle()
    for cid in (parent, done["chat_id"], running["chat_id"]):
        assert env.db.get_conversation_owner(cid) is None
    with sqlite3.connect(env.db.db_path) as c:
        assert c.execute("SELECT COUNT(*) FROM subagents").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM turn_inputs").fetchone()[0] == 0


async def test_children_are_hidden_from_the_sidebar_closed_tabs_branches_and_mail(env):
    parent = _main(env)
    child = (await _start(env, parent))["chat_id"]
    await _idle()
    assert [c["id"] for c in env.db.get_user_conversations(1)] == [parent]
    assert [c["id"] for c in env.db.list_mail_chats(1)] == [parent]
    assert env.db.get_branch_ids(parent) == []
    assert env.db.add_mail(parent, child, "not", "queued") is None
    assert env.db.add_mail(child, parent, "not", "queued") is None
    listed = await _ep(env.router, "/mailbox/chats")(conversation_id=None, x_session_token="")
    assert child not in [c["id"] for c in listed["chats"]]


async def test_a_restart_marks_running_children_interrupted_and_a_message_continues_them(env):
    parent = _main(env)
    started = await _start(env, parent)
    child = started["chat_id"]
    _Runner.scripts[child] = ["hang"]
    await asyncio.sleep(0.02)
    await turn_runner.shutdown()          # the app stops while the child runs
    assert env.db.get_subagent(started["agent_id"])["status"] == "running"
    assert _results(env.db, parent) == []
    turn_runner.reset_all()
    db2 = DatabaseManager(env.path)       # the next start of the app
    cr.create_conversation_router(db2, {})
    srouter2 = sr.create_subagent_router(db2, list_models=env.catalog)
    assert db2.get_subagent(started["agent_id"])["status"] == "interrupted"
    _Runner.scripts[child] = None
    _Runner.replies[child] = "kaldığım yerden bitti"
    sent = await _ep(srouter2, "/subagents/message")(
        body={"conversation_id": parent, "agent_id": started["agent_id"], "text": "devam et"},
        x_session_token="")
    assert sent["status"] == "started"
    await _idle()
    assert db2.get_subagent(started["agent_id"])["status"] == "done"
    assert any("kaldığım yerden bitti" in c for c in _results(db2, parent))


# ── cards, errors, the result text ───────────────────────────────────────────
async def test_a_childs_cards_carry_the_parent_chat_and_the_agent_label(env):
    parent = _main(env)
    started = await _start(env, parent, name="yazıcı")
    child = started["chat_id"]
    _Runner.scripts[child] = ["hang"]
    await asyncio.sleep(0.02)
    since = turn_events.since(child, 0)["last_seq"]
    cards.open_card("c-1", conversation_id=child, kind="command", tool="Bash", summary="ls")
    cards.open_card("p-1", conversation_id=parent, kind="command", tool="Bash", summary="ls")
    label = f"yazıcı ({CODEX})"
    by_id = {c["card_id"]: c for c in cards.list_pending()}
    assert by_id["c-1"]["parent_conversation_id"] == parent and by_id["c-1"]["agent_label"] == label
    assert by_id["c-1"]["conversation_id"] == child
    assert "parent_conversation_id" not in by_id["p-1"]
    opened = [e for e in turn_events.since(child, since)["events"] if e["kind"] == "card_opened"]
    assert opened[-1]["parent_conversation_id"] == parent and opened[-1]["agent_label"] == label
    # The MCP card list the renderer polls carries the same tag.
    pending_ep = _ep(env.router, "/mcp-pending")
    cells = dict(zip(pending_ep.__code__.co_freevars,
                     (c.cell_contents for c in pending_ep.__closure__)))
    cells["_mcp_pending"]["m-1"] = {"conversation_id": child, "tool": "save_file",
                                   "created": __import__("time").time()}
    entry = (await pending_ep(x_session_token=""))["pending"]["m-1"]
    assert entry["parent_conversation_id"] == parent and entry["agent_label"] == label
    cells["_mcp_pending"].pop("m-1", None)
    await env.router.stop_chat(parent)
    await _idle()


async def test_a_quota_error_comes_back_as_the_result(env):
    parent = _main(env)
    started = await _start(env, parent)
    child = started["chat_id"]
    _Runner.scripts[child] = [("error", {"message": "You've hit your usage limit (429)"})]
    await _idle()
    (result,) = _results(env.db, parent)
    assert ": error\n" in result and "quota exhausted: You've hit your usage limit (429)" in result
    assert env.db.get_subagent(started["agent_id"])["status"] == "error"
    assert _Runner.of(parent) == [result]


async def test_a_long_result_is_cut_and_agent_result_has_the_full_text(env):
    parent = _main(env)
    started = await _start(env, parent)
    long_text = "x" * (subagents.RESULT_MAX_CHARS + 500)
    _Runner.replies[started["chat_id"]] = long_text
    await _idle()
    (result,) = _results(env.db, parent)
    assert long_text not in result and f"agent_result({started['agent_id']})" in result
    full = await _ep(env.srouter, "/subagents/result")(
        conversation_id=parent, agent_id=started["agent_id"], x_session_token="")
    assert full["text"].endswith(long_text)


async def test_the_result_is_the_text_after_the_last_tool_call(env):
    parent = _main(env)
    started = await _start(env, parent)
    _Runner.scripts[started["chat_id"]] = [
        ("text", {"content": "Önce dosyaya bakayım."}), ("tool_call", {"name": "read_file"}),
        ("text", {"content": "SON RAPOR"}),
        ("response", {"content": "Önce dosyaya bakayım.SON RAPOR"}), DONE]
    await _idle()
    (result,) = _results(env.db, parent)
    assert result.endswith("\n\nSON RAPOR")


async def test_results_are_kept_in_a_cli_handoff_transcript(env):
    msgs = [{"role": "user", "content": "başla"},
            {"role": "system", "content": f"{subagents.RESULT_MARKER} #1 \"a\" (x): done\n\nR"},
            {"role": "system", "content": "arka plan bildirimi"}]
    text = cr._build_handoff_context("", msgs)
    assert "SUB-AGENT RESULT (your sub-agent, not the user)" in text
    assert "arka plan bildirimi" not in text


# ── turn 2: audit blockers ───────────────────────────────────────────────────
async def test_B1_a_child_runs_in_its_parents_folder_not_the_users_latest(env, tmp_path):
    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    for folder in (project_a, project_b):
        folder.mkdir()
    env.db.save_workspace(1, str(project_a))
    parent = env.db.create_conversation(1, "Ana", workspace=str(project_a))
    env.db.set_conversation_model(parent, "subscription", MAIN)
    env.in_flight.add(parent)
    env.db.save_workspace(1, str(project_b))   # the user opens another project
    child = (await _start(env, parent))["chat_id"]
    await _idle()
    child_kw = next(k for k in _Runner.kwargs if k["conversation_id"] == child)
    assert child_kw["workspace_path"] == str(project_a)


async def test_B2_a_held_result_survives_compaction_and_rides_the_users_turn(env):
    parent = _main(env)
    for i in range(subagents.FUSE_LIMIT):
        await _start(env, parent, name=f"a{i}")
        await _idle()
    held = await _start(env, parent, name="tutulan")
    _Runner.replies[held["chat_id"]] = "TUTULAN SONUC"
    await _idle()
    assert env.db.compact_conversation(parent, "OZET", through_id=subagents.max_message_id(parent))
    assert any("TUTULAN SONUC" in c for c in _results(env.db, parent))
    await _http_turn(env, parent, "devam")
    await _idle()
    assert "TUTULAN SONUC" in _Runner.of(parent)[-1]


async def test_B3_a_child_whose_turn_never_opens_ends_in_error_with_one_result(env, monkeypatch):
    parent = _main(env)
    real = cr.chat_model.turn_model
    child_ids = set()

    def turn_model(db, user_id, conv):
        if conv in child_ids:
            raise RuntimeError("config read failed")
        return real(db, user_id, conv)

    monkeypatch.setattr(cr.chat_model, "turn_model", turn_model)
    row = env.db.create_subagent(parent, 1, "kırık", "subscription", CODEX, "auto", "running")
    child_ids.add(row["child_conv"])
    turn_runner.start_background_turn(row["child_conv"], "görev", "user")
    await _idle()
    assert env.db.get_subagent(row["id"])["status"] == "error"
    (result,) = _results(env.db, parent)
    assert ": error\n" in result and "could not start" in result
    assert len(_Runner.of(parent)) == 1


async def test_B4_after_the_users_stop_a_queued_result_starts_no_turn(env):
    parent = _main(env)
    child = (await _start(env, parent))["chat_id"]
    _Runner.scripts[child] = ["hold", ("response", {"content": "GEC SONUC"}), DONE]
    _Runner.scripts[parent] = ["hold", ("response", {"content": "ana"}), DONE]
    await asyncio.sleep(0.02)
    user = asyncio.create_task(_http_turn(env, parent, "kullanıcı turu"))
    await asyncio.sleep(0.02)
    _Runner.holds[child].set()
    await _until(lambda: len(_results(env.db, parent)) == 1)
    assert env.db.count_turn_inputs(parent) == 1          # queued behind the user's turn
    await env.router.stop_chat(parent)
    _Runner.holds[parent].set()
    await user
    await _idle()
    assert _Runner.of(parent) == ["kullanıcı turu"]        # nothing started on its own
    _Runner.scripts[parent] = None
    await _http_turn(env, parent, "şimdi devam")
    await _idle()
    assert "GEC SONUC" in _Runner.of(parent)[-1]


async def test_B5_the_phone_neither_lists_nor_opens_a_child_chat(env):
    from remote import chats
    parent = _main(env)
    child = (await _start(env, parent))["chat_id"]
    _Runner.scripts[child] = ["hang"]
    await asyncio.sleep(0.02)
    listed = {int(c["chat_id"]) for c in chats.list_chats(env.db)}
    assert parent in listed and child not in listed
    assert chats.chat_row(env.db, child) is None and chats.chat_row(env.db, parent) is not None
    await env.router.stop_chat(parent)
    await _idle()
