"""shell_exec: the single approved-shell-command path and its three entry points."""
import asyncio
import subprocess

import pytest

from agentic import shell_exec


class _Done:
    returncode = 0
    stdout = "out"
    stderr = ""


@pytest.fixture
def ws(tmp_path):
    root = tmp_path.resolve()
    (root / "Assets").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.0f1\n")
    (root / "Assets" / "a.cs.meta").write_text("guid: 1\n")
    (root / "notes.txt").write_text("x\n")
    return str(root)


@pytest.fixture
def spawned(monkeypatch):
    calls = []

    def fake_run(target, **kw):
        calls.append({"target": target, **kw})
        return _Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_plan_gives_argv_for_auto_safe_and_none_for_raw(ws):
    assert shell_exec.plan("cat notes.txt", ws) == ["cat", "notes.txt"]
    assert shell_exec.plan("npm install && npm test", ws) is None


def test_shell_refusal_names_protected_files(ws):
    assert shell_exec.shell_refusal("rm Assets/a.cs.meta", ws)
    assert shell_exec.shell_refusal("cat notes.txt", ws) is None


def test_run_argv_never_uses_the_shell(ws, spawned):
    shell_exec.run("cat notes.txt", ["cat", "notes.txt"], ws)
    call = spawned[0]
    assert call["target"] == ["cat", "notes.txt"]
    assert call["shell"] is False
    assert call["cwd"] == ws
    assert call["timeout"] == 300
    assert call["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_run_raw_string_uses_the_shell(ws, spawned):
    shell_exec.run("npm install && npm test", None, ws)
    assert spawned[0]["target"] == "npm install && npm test"
    assert spawned[0]["shell"] is True


def test_prepare_file_write_shapes(ws):
    assert shell_exec.prepare_file_write("cat notes.txt", ws) is None

    fw = shell_exec.prepare_file_write("echo 'hi' > notes.txt", ws)
    assert (fw.path, fw.content, fw.original, fw.refusal) == ("notes.txt", "hi", "x\n", None)
    assert fw.abs_path.endswith("notes.txt")

    empty = shell_exec.prepare_file_write("dd if=/dev/zero of=notes.txt", ws)
    assert empty.content == "" and empty.abs_path is None

    refused = shell_exec.prepare_file_write("echo 'hi' > Assets/a.cs.meta", ws)
    assert refused.refusal

    with pytest.raises(PermissionError, match="workspace dışında"):
        shell_exec.prepare_file_write("echo 'hi' > /etc/evil", ws)


class _FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[kwargs.get("name") or fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def bash(ws, monkeypatch):
    from unity_ai_mcp.tools import bash_tool

    asked = []
    answer = {"approved": True}

    async def approve(tool_name, params, workspace_path):
        asked.append((tool_name, params))
        return {"approved": answer["approved"]}

    monkeypatch.setattr(bash_tool, "request_approval", approve)
    mcp = _FakeMcp()
    bash_tool.register_bash_tool(mcp, lambda: ws)
    return mcp.tools["bash"], asked, answer


def test_bash_auto_safe_runs_argv_without_asking(bash, spawned):
    fn, asked, _ = bash
    assert asyncio.run(fn("cat notes.txt")) == "out"
    assert asked == []
    assert (spawned[0]["target"], spawned[0]["shell"]) == (["cat", "notes.txt"], False)


def test_bash_raw_command_runs_through_shell_once_approved(bash, spawned):
    fn, asked, _ = bash
    asyncio.run(fn("npm install && npm test"))
    assert asked == [("bash", {"command": "npm install && npm test"})]
    assert (spawned[0]["target"], spawned[0]["shell"]) == ("npm install && npm test", True)


def test_bash_denied_spawns_nothing(bash, spawned):
    fn, asked, answer = bash
    answer["approved"] = False
    out = asyncio.run(fn("npm install && npm test"))
    assert out == "❌ Komut reddedildi: npm install && npm test"
    assert spawned == []


def test_bash_protected_file_spawns_nothing_and_asks_nothing(bash, spawned):
    fn, asked, _ = bash
    out = asyncio.run(fn("rm Assets/a.cs.meta"))
    assert out.startswith("❌ ")
    assert spawned == [] and asked == []


def test_bash_file_write_goes_through_write_file_approval(bash, spawned, ws):
    fn, asked, _ = bash
    out = asyncio.run(fn("echo 'hi' > notes.txt"))
    assert out == "✅ Yazıldı: notes.txt"
    assert asked == [("write_file", {"path": "notes.txt", "content": "hi", "original": "x\n"})]
    assert spawned == []
    with open(ws + "/notes.txt", encoding="utf-8") as f:
        assert f.read() == "hi"
