"""Harness notes: every agent tool name has a note, search finds the right note, status is live and secret-free."""
import asyncio
import json
from pathlib import Path

import pytest

import agent_guide as guide
import harness_notes
from browser_use import server as browser_server
from computer_use import policy as computer_policy
from tools import tool_registry
from unity_ai_mcp.server import create_server as unityai_server
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

ROOT = Path(__file__).resolve().parents[2]
SECRET_WORDS = ("key", "token", "secret", "password", "credential")


def _tool_names() -> set:
    names = {tool["name"] for tool in tool_registry.TOOL_DEFINITIONS}
    names |= {tool.name for tool in asyncio.run(unityai_server(".").list_tools())}
    names |= {tool.name for tool in asyncio.run(browser_server.create_server().list_tools())}
    names |= set(computer_policy.TOOLS)
    manifest = json.loads((ROOT / "unity-mcp" / "manifest.json").read_text(encoding="utf-8"))
    names |= {tool["name"] for tool in manifest["tools"]}
    return names


def _covered() -> set:
    return {name for note in harness_notes.notes().values() for name in note.covers}


def test_every_tool_name_is_covered_by_a_note():
    missing = sorted(_tool_names() - _covered())
    assert not missing, f"{len(missing)} tool names have no note: {missing}"


def test_every_covered_name_is_a_real_tool():
    stale = sorted(_covered() - _tool_names())
    assert not stale, f"covers names that are not tools: {stale}"


def test_harness_tools_are_registered_on_both_surfaces():
    api = {tool["name"] for tool in tool_registry.TOOL_DEFINITIONS}
    stdio = {tool.name for tool in asyncio.run(unityai_server(".").list_tools())}
    assert set(harness_notes.HARNESS_TOOLS) <= api
    assert set(harness_notes.HARNESS_TOOLS) <= stdio


def test_note_files_have_valid_front_matter_and_are_short():
    files = sorted(harness_notes.NOTES_DIR.glob("*.md"))
    assert 15 <= len(files) <= 40
    assert {path.stem for path in files} == set(harness_notes.notes()), "a note file did not parse"
    for path in files:
        text = path.read_text(encoding="utf-8")
        note = harness_notes.notes()[path.stem]
        assert note.title.endswith("?"), path.name
        assert note.keywords, path.name
        assert note.summary, path.name
        assert len(text.splitlines()) < 60, path.name
        assert text.rstrip().splitlines()[-1].startswith("Sources:"), path.name


@pytest.mark.parametrize("query,expected", [
    ("tarayıcı sekme", "browser"),
    ("browser tab", "browser"),
    ("TARAYICI SEKME", "browser"),
    ("onay modu", "approval-modes"),
    ("approval mode", "approval-modes"),
    ("manage_gameobject", "unity-scene-object-tools"),
    ("computer_hotkey", "computer-use-input-tools"),
])
def test_search_finds_the_right_note(query, expected):
    ids = [item["id"] for item in harness_notes.search(query, limit=3)]
    assert any(note_id.startswith(expected) for note_id in ids), (query, ids)


def test_search_folds_turkish_letters_and_returns_summaries():
    assert harness_notes.fold("İĞÜŞÖÇ ıi") == "igusoc ii"
    results = harness_notes.search("tarayici sekme")
    assert results and all(item["summary"] and item["title"] for item in results)
    assert harness_notes.search("") == []
    assert harness_notes.search("zzzqqqxxx") == []
    assert len(harness_notes.search("unity", limit=2)) == 2


def test_read_returns_the_note_and_unknown_id_is_a_clean_error():
    ok = harness_notes.read("approval-modes")
    assert ok["success"] is True and "balanced" in ok["content"]
    bad = harness_notes.read("no-such-note")
    assert bad["success"] is False and "no-such-note" in bad["error"] and "approval-modes" in bad["known_ids"]
    assert harness_notes.read(None)["success"] is False


@pytest.fixture
def unity_stub(monkeypatch):
    async def fake_status():
        return {"status": "connected", "port": 8080}
    monkeypatch.setattr(unity_mcp_manager, "get_status", fake_status)


def test_status_has_live_fields_and_no_secrets(unity_stub):
    context = {"provider_type": "anthropic", "model_name": "m", "language": "tr", "read_only": False,
               "api_key": "sk-secret-value", "token": "tok-secret"}
    state = asyncio.run(harness_notes.status(context, tools=["read_file"]))
    assert state["provider"] == "anthropic" and state["model"] == "m"
    assert state["language"] == "tr" and state["read_only"] is False
    assert state["approval_mode"] in ("auto", "balanced", "step")
    assert state["unity_mcp"] == "connected"
    assert state["enabled_tools"] == ["read_file"]
    dumped = json.dumps(state)
    assert "sk-secret-value" not in dumped and "tok-secret" not in dumped
    assert not [key for key in state if any(word in key for word in SECRET_WORDS)]


def test_status_leaves_unknown_fields_null(unity_stub):
    state = asyncio.run(harness_notes.status({}))
    assert state["provider"] is None and state["read_only"] is None and state["enabled_tools"] is None


def test_run_context_feeds_status(unity_stub):
    token = harness_notes.set_run_context(provider_type="openai", model_name="x", read_only=True, api_key="no")
    try:
        state = asyncio.run(harness_notes.status())
    finally:
        harness_notes.reset_run_context(token)
    assert state["provider"] == "openai" and state["read_only"] is True
    assert "no" not in {value for value in state.values() if isinstance(value, str)}


def test_registry_tools_run_and_status_lists_only_offered_tools(unity_stub):
    found = tool_registry.execute_tool("harness_search", {"query": "tarayıcı sekme"}, ".")
    assert found["success"] is True and "browser-tabs" in found["content"]
    read = tool_registry.execute_tool("harness_read", {"note_id": "browser-tabs"}, ".")
    assert read["success"] is True and "agent tab" in read["content"]
    assert tool_registry.execute_tool("harness_read", {"note_id": "nope"}, ".")["success"] is False

    extra = [{"name": "gm_browser__only_this_run", "description": "x", "parameters": {"type": "object"}}]
    with tool_registry.run_tool_definitions(extra):
        offered = {t["name"] for t in tool_registry._all_tool_definitions()}
        result = asyncio.run(tool_registry.execute_tool_async("harness_status", {}, ".", 1))
    state = json.loads(result["content"])
    assert set(state["enabled_tools"]) == offered and "gm_browser__only_this_run" in offered
    after = json.loads(asyncio.run(tool_registry.execute_tool_async("harness_status", {}, ".", 1))["content"])
    assert "gm_browser__only_this_run" not in after["enabled_tools"]


@pytest.mark.parametrize("language,line", [("en", guide.HARNESS_LINE_EN), ("tr", guide.HARNESS_LINE_TR)])
def test_prompt_has_one_harness_line_within_the_size_cap(language, line):
    text = guide.compose(language, project_open=False, unity_state="off")
    assert text.count("harness_search") == 1 and line in text
    assert len(line) + 1 < 300
    assert guide.compose(language, project_open=True, unity_state="connected").count("harness_search") == 1


def test_read_only_runs_allow_the_harness_tools():
    from agentic import agent_runner
    for name in harness_notes.HARNESS_TOOLS:
        assert agent_runner._read_only_tool_declared(name)
        assert agent_runner._read_only_tool_allowed(name, {})


def test_status_in_a_read_only_run_lists_only_read_only_tools(unity_stub):
    from agentic import agent_runner
    token = harness_notes.set_run_context(read_only=True)
    try:
        result = asyncio.run(tool_registry.execute_tool_async("harness_status", {}, ".", 1))
    finally:
        harness_notes.reset_run_context(token)
    tools = json.loads(result["content"])["enabled_tools"]
    assert set(harness_notes.HARNESS_TOOLS) <= set(tools)
    assert "write_file" not in tools and "run_command" not in tools
    assert all(agent_runner._read_only_tool_declared(name) for name in tools)
    full = json.loads(asyncio.run(tool_registry.execute_tool_async("harness_status", {}, ".", 1))["content"])
    assert "write_file" in full["enabled_tools"]


def test_runner_sets_the_run_context_for_the_run_and_resets_it(monkeypatch):
    from agentic import agent_runner

    async def no_guide(self):
        return None

    seen = {}

    async def inner(self, message):
        seen.update(harness_notes.run_context())
        yield "event"

    monkeypatch.setattr(agent_runner.AgentRunner, "_prepare_agent_guide", no_guide)
    monkeypatch.setattr(agent_runner.AgentRunner, "_run_inner", inner)
    runner = agent_runner.AgentRunner("openai", "k", "gpt-x", ".", language="en", read_only=True)

    async def drain():
        return [event async for event in runner.run("hi")]

    assert asyncio.run(drain()) == ["event"]
    assert seen == {"provider_type": "openai", "model_name": "gpt-x", "language": "en", "read_only": True}
    assert harness_notes.run_context() == {}


def test_notes_resolve_from_source_and_from_a_packaged_layout(monkeypatch, tmp_path):
    assert harness_notes._notes_dir() == harness_notes.NOTES_DIR
    packaged = tmp_path / "harness_notes"
    packaged.mkdir()
    (packaged / "only.md").write_text("---\ntitle: T\nkeywords: a\ncovers: []\n---\nBody.\n", encoding="utf-8")
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    assert harness_notes._notes_dir() == packaged
    monkeypatch.setattr(harness_notes, "NOTES_DIR", harness_notes._notes_dir())
    monkeypatch.setattr(harness_notes, "_notes", None)
    assert list(harness_notes.notes()) == ["only"]
