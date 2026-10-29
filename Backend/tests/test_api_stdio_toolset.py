"""API loop's per-run computer/browser stdio toolset, against a fake stdio MCP server."""
import asyncio
import os
import subprocess
import sys
import time
from unittest import mock

import pytest

import agent_guide
import agentic.agent_runner as ar
from agentic import stdio_toolset as st
from browser_use import registration as browser_reg
from computer_use import registration as computer_reg
from tools import tool_registry

FAKE = os.path.join(os.path.dirname(__file__), "fixtures", "fake_stdio_mcp_server.py")
CHAT = 4242


def _alive(pid):
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


def _pids(pidbase):
    found = []
    for label in ("computer", "browser"):
        path = f"{pidbase}.{label}.pid"
        if os.path.exists(path):
            found.append(int(open(path).read().strip()))
    return found


def _all_dead(pids, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def servers(tmp_path):
    """Both switches behind `state["on"]`; the real servers replaced by the fake one."""
    state = {"on": True}
    pidbase = str(tmp_path / "pid")
    patches = [
        mock.patch("computer_use.settings.enabled_now", lambda: state["on"]),
        mock.patch("browser_use.settings.enabled_now", lambda: state["on"]),
        mock.patch.object(computer_reg, "server_argv", lambda: [sys.executable, FAKE, "computer", pidbase]),
        mock.patch.object(browser_reg, "server_argv", lambda: [sys.executable, FAKE, "browser", pidbase]),
    ]
    for p in patches:
        p.start()
    yield state, pidbase
    for p in reversed(patches):
        p.stop()
    for pid in _pids(pidbase):
        if _alive(pid):
            os.kill(pid, 9)


def test_exposed_name_is_prefixed_and_api_safe():
    assert st.exposed_name("gm_computer__", "screenshot") == "gm_computer__screenshot"
    assert st.exposed_name("gm_browser__", "open page.v2") == "gm_browser__open_page_v2"
    long = st.exposed_name("gm_computer__", "x" * 100)
    assert len(long) == 64 and long.startswith("gm_computer__")


async def test_sync_lists_routes_and_maps_images(servers):
    state, pidbase = servers
    toolset = st.StdioToolset(CHAT)
    try:
        await toolset.sync()
        names = [d["name"] for d in toolset.definitions()]
        assert sorted(names) == ["gm_browser__screenshot", "gm_browser__whoami",
                                 "gm_computer__screenshot", "gm_computer__whoami"]
        assert not set(names) & {t["name"] for t in tool_registry.TOOL_DEFINITIONS}
        assert all(d["parameters"].get("type") == "object" for d in toolset.definitions())

        who = await toolset.call("gm_browser__whoami", {})
        assert who["success"] is True and who["result"] == f"browser chat={CHAT}"
        shot = await toolset.call("gm_computer__screenshot", {})
        assert shot["image_base64"].startswith("data:image/png;base64,")
        assert toolset.owns("gm_computer__whoami") and not toolset.owns("read_file")
        assert len(_pids(pidbase)) == 2 and all(_alive(p) for p in _pids(pidbase))
    finally:
        await toolset.aclose()
    assert _all_dead(_pids(pidbase))


async def test_sync_follows_the_switches(servers):
    state, pidbase = servers
    state["on"] = False
    toolset = st.StdioToolset(CHAT)
    try:
        await toolset.sync()
        assert toolset.definitions() == [] and _pids(pidbase) == []

        state["on"] = True
        await toolset.sync()
        assert len(toolset.definitions()) == 4
        started = _pids(pidbase)

        state["on"] = False
        await toolset.sync()
        assert toolset.definitions() == []
        assert _all_dead(started, timeout=0.5)
        off = await toolset.call("gm_computer__whoami", {})
        assert off["success"] is False and toolset.owns("gm_computer__whoami")
    finally:
        await toolset.aclose()


def test_gemini_schema_of_run_tools_is_sanitized():
    definition = {"name": "gm_computer__click", "description": "d", "parameters": {
        "type": "object", "title": "Args", "additionalProperties": False,
        "properties": {"x": {"anyOf": [{"type": "integer"}, {"type": "null"}], "title": "X"}}}}
    with tool_registry.run_tool_definitions([definition]):
        declared = {d["name"]: d for d in
                    tool_registry.get_gemini_tool_declarations()[0]["function_declarations"]}
    assert declared["gm_computer__click"]["parameters"] == {
        "type": "object", "properties": {"x": {"type": "integer", "nullable": True}}}
    assert "gm_computer__click" not in {t["name"] for t in tool_registry._all_tool_definitions()}


async def test_guide_lines_follow_the_open_servers(servers):
    state, _ = servers
    runner = ar.AgentRunner("anthropic", "k", "m", None, conversation_id=CHAT)
    runner._agent_guide = agent_guide.DESKTOP_LINE_EN + "\n\n[/GAMACHINE AGENT GUIDE]"
    toolset = st.StdioToolset(CHAT)
    try:
        await toolset.sync()
        system = runner._api_system_instruction(toolset)
        assert agent_guide.COMPUTER_USE_LINE_EN in system
        assert agent_guide.BROWSER_GUIDE_EN in system
    finally:
        await toolset.aclose()
    assert agent_guide.COMPUTER_USE_LINE_EN not in runner._api_system_instruction(toolset)


class _Adapter:
    """Asks for whoami and a screenshot once, then finishes; records the offered tools."""
    label = "fake"
    error_prefix = "fake:"
    iptal_edilebilir = True

    def __init__(self):
        self.offered = []
        self.results = {}
        self.screenshots = {}
        self.turns = 0

    def preflight(self, runner):
        return None

    def prepare(self, runner, system, user_message, images):
        self.system = system

    def refresh_tools(self, runner):
        self.offered.append([d["function"]["name"] for d in ar.get_openai_tool_declarations()])

    async def request(self, runner):
        return object()

    def view(self, response):
        self.turns += 1
        if self.turns == 1:
            return ar._APITurn([], [], [("1", "gm_computer__whoami", {}, None),
                                        ("2", "gm_computer__screenshot", {}, None)], False, 1, 1)
        return ar._APITurn(["done"], [], [], False, 1, 1)

    def append_assistant(self, turn):
        pass

    def append_result(self, call, result_str, screenshot):
        self.results[call[1]] = result_str
        if screenshot:
            self.screenshots[call[1]] = screenshot

    def finish_results(self):
        pass


def _runner(read_only=False):
    return ar.AgentRunner("anthropic", "k", "m", None, conversation_id=CHAT, read_only=read_only)


async def test_api_loop_routes_calls_and_closes_on_normal_end(servers):
    _, pidbase = servers
    adapter = _Adapter()
    events = [e async for e in _runner()._api_loop(adapter, "hi")]
    assert events[-1].type == "done"
    assert "gm_computer__whoami" in adapter.offered[0]
    assert f"chat={CHAT}" in adapter.results["gm_computer__whoami"]
    assert adapter.screenshots["gm_computer__screenshot"].startswith("data:image/png;base64,")
    assert "image_base64" not in adapter.results["gm_computer__screenshot"]
    assert len(_pids(pidbase)) == 2 and _all_dead(_pids(pidbase))


async def test_api_loop_closes_servers_when_the_generator_is_closed(servers):
    _, pidbase = servers
    adapter = _Adapter()
    gen = _runner()._api_loop(adapter, "hi")
    async for event in gen:
        if event.type == "tool_result":
            break
    started = _pids(pidbase)
    assert len(started) == 2 and all(_alive(p) for p in started)
    await gen.aclose()
    assert _all_dead(started)


async def test_read_only_run_gets_no_toolset(servers):
    _, pidbase = servers
    adapter = _Adapter()
    with mock.patch.object(st, "StdioToolset", side_effect=AssertionError("toolset built")):
        events = [e async for e in _runner(read_only=True)._api_loop(adapter, "hi")]
    assert events
    assert not any(n.startswith("gm_") for names in adapter.offered for n in names)
    assert _pids(pidbase) == []
