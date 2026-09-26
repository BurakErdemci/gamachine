"""The fixed Unity file rule (unity_file_guard) and every backend path it guards.

The rule refuses, in every approval mode, raw writes/deletes/moves of .meta
files and raw writes of Unity YAML assets inside a Unity project. Each call-site
test runs in AUTO mode (or with approval stubbed to "yes"), because a refusal
that only step mode's card produces is no rule.
"""
import argparse
import asyncio
import json
import os
import sys

import pytest

import unity_file_guard as guard


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    root = tmp_path / "Unity Projeler" / "ai proje"
    (root / "Assets" / "Prefabs").mkdir(parents=True)
    (root / "ProjectSettings").mkdir()
    (root / "Library").mkdir()
    return str(root)


@pytest.fixture
def plain_dir(tmp_path):
    d = tmp_path / "not_unity"
    (d / "Assets").mkdir(parents=True)  # Assets/ alone does not make a Unity project
    return str(d)


def _msg(refusal):
    return refusal.message if refusal else None


# ── Verdict table ────────────────────────────────────────────────────────────

WRITE_REFUSED = [
    ("Assets/x.meta", "meta"),
    ("Assets/Prefabs/Foo.prefab.meta", "meta"),
    ("Assets/Foo.PREFAB", "yaml"),
    ("Assets/Main.unity", "yaml"),
    ("Assets/M.mat", "yaml"),
    ("Assets/A.overrideController", "yaml"),
    ("Assets/A.OVERRIDECONTROLLER", "yaml"),
    ("ProjectSettings/TagManager.asset", "yaml"),
    ("Assets\\Prefabs\\Foo.prefab", "yaml"),
    # Windows drops trailing dots/spaces and writes ::$DATA to the file itself
    ("Assets/a.prefab. ", "yaml"),
    ("Assets/a.meta::$DATA", "meta"),
]


@pytest.mark.parametrize("rel,kind", WRITE_REFUSED)
def test_write_refused_relative(project, rel, kind):
    refusal = guard.check_write(rel, project)
    assert refusal is not None
    assert (".meta file" in refusal.message) == (kind == "meta")
    assert "manage_asset" in refusal.message


@pytest.mark.parametrize("rel,kind", WRITE_REFUSED)
def test_write_refused_absolute_both_separators(project, rel, kind):
    for sep_root in (project, project.replace("\\", "/")):
        assert guard.check_write(os.path.join(sep_root, rel), "") is not None


def test_quoted_path_with_spaces(project):
    quoted = '"' + os.path.join(project, "Assets", "x.meta").replace("\\", "/") + '"'
    assert guard.check_write(quoted, "") is not None
    assert guard.check_shell(f"rm {quoted}", "") is not None


@pytest.mark.parametrize("rel", [
    "Assets/Foo.cs", "Assets/Shader.shader", "Assets/data.json", "Assets/Readme.txt",
    "Library/x.meta", "Library/Foo.prefab", "Temp/a.unity", "x.meta", "Packages/manifest.json",
    "ProjectSettings/ProjectVersion.txt",
])
def test_write_allowed(project, rel):
    assert guard.check_write(rel, project) is None


def test_outside_any_unity_project_is_allowed(plain_dir):
    assert guard.check_write("Assets/x.meta", plain_dir) is None
    assert guard.check_delete("Assets/Foo.prefab", plain_dir) is None
    assert guard.check_shell("rm Assets/x.meta", plain_dir) is None


def test_delete_messages(project):
    assert "manage_asset" in _msg(guard.check_delete("Assets/x.meta", project))
    remove = guard.check_delete("Assets/Foo.prefab", project)
    assert "orphans its .meta" in remove.message and "manage_asset" in remove.message
    assert guard.check_delete("Assets/Foo.cs", project) is None


def test_move(project):
    assert guard.check_move("Assets/a.meta", "Assets/b.txt", project) is not None
    assert guard.check_move("Assets/a.txt", "Assets/b.meta", project) is not None
    assert guard.check_move("Assets/a.prefab", "Assets/b.txt", project) is not None
    assert guard.check_move("Assets/a.cs", "Assets/b.cs", project) is None


def test_summary_is_turkish(project):
    assert "Unity koruması" in guard.check_write("Assets/x.meta", project).summary


SHELL_REFUSED = [
    "rm Assets/x.meta",
    "rm -f Assets/Prefabs/Foo.prefab.meta",
    "del Assets\\x.meta",
    "Remove-Item Assets/x.meta",
    "Remove-Item -LiteralPath 'Assets/x.meta' -Force",
    "git rm Assets/x.meta",
    "git mv Assets/a.meta Assets/b.meta",
    "mv Assets/a.prefab Assets/b.prefab",
    "Move-Item Assets/a.meta Assets/Old/a.meta",
    "ren Assets\\a.meta b.meta",
    "rm Assets/*.meta",
    "rm Assets/Foo.prefab",
    "echo hi > Assets/a.prefab",
    "echo hi >> Assets/x.meta",
    "printf 'x' >Assets/Main.unity",
    "cat a.txt | tee Assets/M.mat",
    "sed -i 's/a/b/' Assets/Main.unity",
    "sed -i.bak s/a/b/ Assets/x.meta",
    "Set-Content -Path Assets/New.mat -Value x",
    "Get-Content a.txt | Out-File Assets/Main.unity",
    "Add-Content ProjectSettings/TagManager.asset x",
    "dd if=/dev/zero of=Assets/a.mat",
    "cp /tmp/a.prefab Assets/b.prefab",
    "Copy-Item Assets/a.meta Assets/b.meta",
    "touch Assets/New.prefab",
    'cmd /c "del Assets\\x.meta"',
    "powershell -Command \"Remove-Item 'Assets/x.meta'\"",
    "git status && rm Assets/x.meta",
]


@pytest.mark.parametrize("command", SHELL_REFUSED)
def test_shell_refused(project, command):
    assert guard.check_shell(command, project) is not None, command


@pytest.mark.parametrize("command", [
    "git status", "cat Assets/x.meta", "type Assets\\x.meta", "grep -r guid Assets/x.meta",
    "Get-Content Assets/Main.unity", "git diff Assets/Foo.prefab", "rm Assets/Foo.cs",
    "echo hi > Assets/Notes.txt", "cp Assets/a.prefab ../backup.prefab", "sed -n 1p Assets/Main.unity",
    "ls Assets", "",
])
def test_shell_allowed(project, command):
    assert guard.check_shell(command, project) is None, command


def test_shell_absolute_token_from_outside_project(project, tmp_path):
    target = os.path.join(project, "Assets", "x.meta")
    assert guard.check_shell(f'rm "{target}"', str(tmp_path)) is not None
    assert guard.check_shell("rm x.meta", str(tmp_path)) is None


def test_shell_relative_token_from_project_subdir(project):
    assert guard.check_shell("rm x.meta", os.path.join(project, "Assets", "Prefabs")) is not None


def test_non_string_inputs_are_ignored(project):
    assert guard.check_write(None, project) is None
    assert guard.check_delete(123, project) is None
    assert guard.check_shell(None, project) is None


def test_module_is_stdlib_only():
    """agy_step_gate imports it on every gated agy call (3.3 s if a provider SDK came along)."""
    from tests.test_workspace_fingerprint_is_stdlib_only import find_non_stdlib_imports
    app = os.path.join(os.path.dirname(__file__), "..", "app")
    with open(os.path.join(app, "unity_file_guard.py"), encoding="utf-8") as f:
        assert find_non_stdlib_imports(f.read()) == []
    with open(os.path.join(app, "agy_step_gate.py"), encoding="utf-8") as f:
        offenders = find_non_stdlib_imports(f.read())
    assert len(offenders) == 1 and "'unity_file_guard'" in offenders[0]


# ── NTFS 8.3 aliases: judged by the long name of the file they open ──────────


def _short(path):
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
    return buf.value if n else ""


@pytest.fixture
def aliases(project):
    """Real files under tmp_path and the 8.3 names Windows gives them."""
    if sys.platform != "win32":
        pytest.skip("NTFS 8.3 aliases exist only on Windows")
    files = {
        "prefab": os.path.join(project, "Assets", "Prefabs", "LongAssetName.prefab"),
        "meta": os.path.join(project, "Assets", "Prefabs", "LongAssetName.prefab.meta"),
        "scene": os.path.join(project, "Assets", "LongMainScene.unity"),
        "config": os.path.join(project, "Assets", "LongScriptName.config"),
        "settings": os.path.join(project, "ProjectSettings", "TagManager.asset"),
    }
    for path in files.values():
        with open(path, "w", encoding="utf-8") as f:
            f.write("fixture")
    short = {key: _short(path) for key, path in files.items()}
    short["root"] = _short(project)
    if any(os.path.basename(short[k]).lower() == os.path.basename(files[k]).lower()
           for k in ("prefab", "meta", "scene", "config")):
        pytest.skip("8.3 name generation is disabled on this volume (fsutil 8dot3name)")
    for key in ("prefab", "meta", "scene", "config"):
        assert os.path.samefile(files[key], short[key])
    return short


def test_alias_names_hide_the_protected_extension(aliases):
    assert aliases["prefab"].upper().endswith(".PRE")
    assert aliases["meta"].upper().endswith(".MET")
    assert guard.file_kind(aliases["prefab"]) is None  # the name alone does not tell


def test_write_to_alias_of_prefab_scene_or_meta_is_refused(aliases):
    assert "YAML asset" in _msg(guard.check_write(aliases["prefab"]))
    assert "YAML asset" in _msg(guard.check_write(aliases["scene"]))
    assert ".meta file" in _msg(guard.check_write(aliases["meta"]))


def test_delete_and_move_of_aliases_are_refused(aliases, project):
    assert ".meta file" in _msg(guard.check_delete(aliases["meta"]))
    assert "orphans its .meta" in _msg(guard.check_delete(aliases["prefab"]))
    assert guard.check_move(aliases["prefab"], os.path.join(project, "Assets", "b.txt")) is not None
    assert guard.check_move(os.path.join(project, "Assets", "a.txt"), aliases["meta"]) is not None


def test_relative_alias_resolves_against_base(aliases, project):
    rel = os.path.join("Assets", "Prefabs", os.path.basename(aliases["prefab"]))
    assert guard.check_write(rel, project) is not None
    assert guard.check_delete(os.path.basename(aliases["meta"]),
                              os.path.join(project, "Assets", "Prefabs")) is not None


def test_project_found_through_aliased_ancestors(aliases):
    settings_dir = _short(os.path.dirname(aliases["settings"]))
    assert os.path.basename(settings_dir).lower() != "projectsettings", settings_dir
    path = os.path.join(settings_dir, "TagManager.asset")
    assert guard.in_unity_project(path)
    assert guard.check_write(path) is not None
    assert guard.check_write(os.path.join(aliases["root"], "Assets", "Prefabs",
                                          os.path.basename(aliases["prefab"]))) is not None


def test_alias_of_an_unprotected_file_is_judged_by_its_long_name(aliases):
    # LongScriptName.config -> LONGSC~1.CON: .CON is also how a .controller shortens
    assert os.path.basename(aliases["config"]).upper().endswith(".CON")
    assert guard.check_write(aliases["config"]) is None


def test_shell_commands_on_aliases_are_refused(aliases, project):
    assert guard.check_shell(f'del "{aliases["prefab"]}"', project) is not None
    assert guard.check_shell(f'echo x > "{aliases["meta"]}"', project) is not None
    prefabs = os.path.join(project, "Assets", "Prefabs")
    assert guard.check_shell(f"rm {os.path.basename(aliases['prefab'])}", prefabs) is not None


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 aliases exist only on Windows")
@pytest.mark.parametrize("command", [
    "cd Assets/Prefabs && rm LONGAS~1.PRE",
    "cd Assets && del ABCDEF~1.MET",
    "cd Assets && Set-Content FOOBAR~12.UNI x",
])
def test_shell_alias_shaped_name_after_cd_is_refused(project, command):
    """After a `cd` the name cannot be resolved against the working directory,
    so its alias shape decides (volume 8.3 support not needed)."""
    assert guard.check_shell(command, project) is not None, command


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 aliases exist only on Windows")
@pytest.mark.parametrize("command", ["cd Assets && rm FOOBAR~1.CS", "cd Assets && rm backup~1"])
def test_shell_names_that_are_not_protected_aliases_pass(project, command):
    assert guard.check_shell(command, project) is None, command


def test_alias_rule_is_off_outside_windows(aliases, monkeypatch):
    monkeypatch.setattr(guard.os, "name", "posix")
    assert guard.check_write(aliases["prefab"]) is None
    assert guard.check_shell("cd Assets && rm LONGAS~1.PRE", guard.os.path.dirname(aliases["prefab"])) is None


async def test_claude_sdk_refuses_an_alias_write_in_auto(aliases, project):
    from providers.claude_sdk_session import ClaudeSDKSession
    from claude_agent_sdk import PermissionResultDeny
    s = ClaudeSDKSession(conversation_id=1, cwd=project, auto_approve=True)
    s._out_q = asyncio.Queue()
    res = await s._can_use_tool("Write", {"file_path": aliases["prefab"], "content": "x"}, None)
    assert isinstance(res, PermissionResultDeny)
    assert s._out_q.get_nowait()["success"] is False
    assert s._out_q.empty()


# ── Call sites, auto mode ────────────────────────────────────────────────────


async def test_claude_sdk_refuses_in_auto_without_card(project):
    from providers.claude_sdk_session import ClaudeSDKSession
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    s = ClaudeSDKSession(conversation_id=1, cwd=project, auto_approve=True)
    s._out_q = asyncio.Queue()

    for tool, inp in [
        ("Write", {"file_path": os.path.join(project, "Assets", "x.meta"), "content": "x"}),
        ("Edit", {"file_path": "Assets/Main.unity", "old_string": "a", "new_string": "b"}),
        ("MultiEdit", {"file_path": "Assets/Foo.prefab", "edits": []}),
        ("Bash", {"command": "rm Assets/x.meta"}),
        ("PowerShell", {"command": "Remove-Item Assets/x.meta"}),
    ]:
        res = await s._can_use_tool(tool, inp, None)
        assert isinstance(res, PermissionResultDeny), tool
        assert "Refused" in res.message
        ev = s._out_q.get_nowait()
        assert ev["type"] == "tool_result" and ev["success"] is False
        assert s._out_q.empty()  # no approval card

    res = await s._can_use_tool("Write", {"file_path": "Assets/Foo.cs", "content": "x"}, None)
    assert isinstance(res, PermissionResultAllow)
    res = await s._can_use_tool("Bash", {"command": "cat Assets/x.meta"}, None)
    assert isinstance(res, PermissionResultAllow)


def _register(register, workspace):
    tools = {}

    class _FakeMCP:
        def tool(self, *args, **kwargs):
            def deco(fn):
                tools[kwargs.get("name") or fn.__name__] = fn
                return fn
            return deco

    register(_FakeMCP(), lambda: workspace)
    return tools


@pytest.fixture
def approvals(monkeypatch):
    """request_approval stubbed to auto's answer; records whether a card was asked."""
    asked = []

    async def _auto(**kwargs):
        asked.append(kwargs)
        return {"approved": True}

    import unity_ai_mcp.tools.bash_tool as bash_tool
    import unity_ai_mcp.tools.file_tools as mcp_file_tools
    import unityai_cli
    for module in (mcp_file_tools, bash_tool, unityai_cli):
        monkeypatch.setattr(module, "request_approval", _auto)
    return asked


async def test_unityai_mcp_tools_refuse(project, approvals):
    from unity_ai_mcp.tools.bash_tool import register_bash_tool
    from unity_ai_mcp.tools.file_tools import register_file_tools
    tools = _register(register_file_tools, project)
    tools.update(_register(register_bash_tool, project))
    meta = os.path.join(project, "Assets", "x.meta")
    prefab = os.path.join(project, "Assets", "Foo.prefab")
    for path in (meta, prefab):
        with open(path, "w") as f:
            f.write("orig")

    out = await tools["save_file"]("Assets/x.meta", "fileFormatVersion: 2")
    assert "Refused" in out
    out = await tools["save_file"]("Assets/Foo.prefab", "%YAML 1.1")
    assert "Refused" in out
    out = await tools["delete_file"]("Assets/Foo.prefab")
    assert "Refused" in out and "manage_asset" in out
    out = await tools["bash"]("rm Assets/x.meta")
    assert "Refused" in out
    out = await tools["bash"]("echo 'x' > Assets/Foo.prefab")
    assert "Refused" in out
    assert approvals == []
    with open(meta) as f:
        assert f.read() == "orig"
    assert os.path.exists(prefab)

    out = await tools["save_file"]("Assets/Foo.cs", "class Foo {}")
    assert out.startswith("✅")


def test_unityai_cli_refuses(project, approvals, monkeypatch, capsys):
    import unityai_cli
    monkeypatch.setenv("WORKSPACE", project)
    meta = os.path.join(project, "Assets", "x.meta")
    with open(meta, "w") as f:
        f.write("orig")

    ns = argparse.Namespace
    assert unityai_cli.cmd_save_file(ns(path="Assets/x.meta", content="x", content_stdin=False)) == 1
    assert unityai_cli.cmd_save_file(ns(path="Assets/Main.unity", content="x", content_stdin=False)) == 1
    assert unityai_cli.cmd_delete_file(ns(path="Assets/x.meta")) == 1
    assert unityai_cli.cmd_bash(ns(command="Remove-Item Assets/x.meta")) == 1
    assert "Refused" in capsys.readouterr().out
    assert approvals == []
    assert os.path.exists(meta)
    assert not os.path.exists(os.path.join(project, "Assets", "Main.unity"))


def test_api_provider_tools_refuse(project, monkeypatch):
    from agentic import approval_mode
    approval_mode.set_mode("auto")
    import tools.file_tools as ft
    ran = []
    monkeypatch.setattr(ft.subprocess, "run", lambda *a, **k: ran.append(a))
    meta = os.path.join(project, "Assets", "x.meta")
    with open(meta, "w") as f:
        f.write("orig")

    res = ft.write_file("Assets/x.meta", "x", project)
    assert res["success"] is False and "Refused" in res["error"]
    res = ft.write_file("Assets/Main.unity", "x", project)
    assert res["success"] is False and "Refused" in res["error"]
    res = ft.delete_file("Assets/x.meta", project)
    assert res["success"] is False and "Refused" in res["error"]
    res = ft.run_command("rm Assets/x.meta", project)
    assert res["success"] is False and "Refused" in res["error"]
    assert ran == []
    with open(meta) as f:
        assert f.read() == "orig"
    assert ft.write_file("Assets/Foo.cs", "x", project)["success"] is True


def _agy_state(tmp_path, mode):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"mode": mode, "launcher": r"C:\x\unityai.cmd"}), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("mode", ["auto", "step"])
def test_agy_hook_refuses_in_every_mode(project, tmp_path, mode):
    from agy_step_gate import decide
    state = _agy_state(tmp_path, mode)

    def call(name, args):
        return decide(json.dumps({"toolCall": {"name": name, "args": args}}).encode(), state)

    meta = os.path.join(project, "Assets", "x.meta")
    for name, args in [
        ("write_to_file", {"TargetFile": meta, "CodeContent": "x"}),
        ("replace_file_content", {"TargetFile": os.path.join(project, "Assets", "Main.unity")}),
        ("multi_replace_file_content", {"target_file": os.path.join(project, "Assets", "a.prefab")}),
        ("sed_file", {"TargetFile": meta}),
        ("run_command", {"CommandLine": "Remove-Item Assets/x.meta", "Cwd": project}),
    ]:
        out = call(name, args)
        assert out["decision"] == "deny", name
        assert "Refused" in out["reason"], name


def test_agy_hook_auto_still_allows_everything_else(project, tmp_path):
    from agy_step_gate import decide
    state = _agy_state(tmp_path, "auto")
    for payload in [
        {"toolCall": {"name": "write_to_file",
                      "args": {"TargetFile": os.path.join(project, "Assets", "Foo.cs")}}},
        {"toolCall": {"name": "run_command", "args": {"CommandLine": "git status", "Cwd": project}}},
        {"toolCall": {"name": "write_to_file", "args": {}}},
        {"toolCall": {"name": "view_file", "args": {"AbsolutePath": "x.meta"}}},
    ]:
        assert decide(json.dumps(payload).encode(), state)["decision"] == "allow"
    assert decide(b"not json", state)["decision"] == "allow"


def test_agy_hook_closed_mode_is_unchanged(tmp_path):
    from agy_step_gate import CLOSED_REASON, decide
    state = _agy_state(tmp_path, "closed")
    out = decide(json.dumps({"toolCall": {"name": "write_to_file", "args": {}}}).encode(), state)
    assert out == {"decision": "deny", "reason": CLOSED_REASON}


def _codex(project):
    from providers.codex_session import CodexSession
    s = CodexSession(conversation_id=1, cwd=project, auto_approve=True)
    s._out_q = asyncio.Queue()
    return s


def _file_change_item(item_id, changes, method="item/started"):
    return {"method": method, "params": {"threadId": "t", "turnId": "u",
                                        "item": {"type": "fileChange", "id": item_id,
                                                 "status": "inProgress", "changes": changes}}}


async def test_codex_declines_file_change_in_auto(project):
    s = _codex(project)
    cases = {
        "i1": [{"path": os.path.join(project, "Assets", "x.meta"), "kind": {"type": "delete"}, "diff": ""}],
        "i2": [{"path": "Assets/Main.unity", "kind": {"type": "update", "move_path": None}, "diff": "@@"}],
        "i3": [{"path": "Assets/a.cs", "kind": {"type": "update", "move_path": "Assets/a.meta"}, "diff": ""}],
        "i4": [{"path": "Assets/New.prefab", "kind": {"type": "add"}, "diff": "x"}],
    }
    for item_id, changes in cases.items():
        await s._dispatch(_file_change_item(item_id, changes))
        decision = await s._resolve_approval(
            "item/fileChange/requestApproval",
            {"itemId": item_id, "threadId": "t", "turnId": "u", "startedAtMs": 0})
        assert decision == "decline", item_id

    await s._dispatch(_file_change_item("ok", [{"path": "Assets/a.cs", "kind": {"type": "add"}, "diff": ""}]))
    assert await s._resolve_approval("item/fileChange/requestApproval", {"itemId": "ok"}) == "accept"


async def test_codex_patch_updated_and_completed(project):
    s = _codex(project)
    await s._dispatch(_file_change_item("p", [{"path": "Assets/a.cs", "kind": {"type": "add"}, "diff": ""}]))
    await s._dispatch({"method": "item/fileChange/patchUpdated",
                       "params": {"itemId": "p", "threadId": "t", "turnId": "u",
                                  "changes": [{"path": "Assets/x.meta", "kind": {"type": "add"}, "diff": ""}]}})
    assert await s._resolve_approval("item/fileChange/requestApproval", {"itemId": "p"}) == "decline"
    await s._dispatch(_file_change_item("p", [], method="item/completed"))
    assert "p" not in s._file_changes


async def test_codex_declines_commands_and_v1_shapes_in_auto(project):
    s = _codex(project)
    assert await s._resolve_approval("item/commandExecution/requestApproval",
                                     {"itemId": "c", "command": "rm Assets/x.meta", "cwd": project}) == "decline"
    assert await s._resolve_approval("execCommandApproval",
                                     {"callId": "c", "command": ["pwsh", "-Command", "Remove-Item Assets/x.meta"],
                                      "cwd": project}) == "decline"
    assert await s._resolve_approval("applyPatchApproval",
                                     {"callId": "c", "fileChanges": {
                                         os.path.join(project, "Assets", "M.mat"): {"type": "add", "content": "x"}}}) == "decline"
    ev = s._out_q.get_nowait()
    assert ev["type"] == "tool_result" and ev["success"] is False
    assert await s._resolve_approval("item/commandExecution/requestApproval",
                                     {"itemId": "c", "command": "git status", "cwd": project}) == "accept"
