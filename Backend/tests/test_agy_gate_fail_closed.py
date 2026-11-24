"""agy's gate is an allow-list in step and balanced mode (4.0.3).

Measured 24 Nov 2026 with agy 1.5.15 on v4.0.2: step mode wrote two files with
no card, because agy 1.5.x writes with write_file / edit_file and the hook
was only asked about the 1.2.x names. The payload below has the hook stdin
keys and the write_file arguments of that measurement. The approval server
is the one faked part (it is the backend, outside the hook).
"""
import io
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

import agy_native_approval as native
import agy_step_gate as gate
from providers import agy_provider
from providers.agy_provider import AgyProvider, NATIVE_WRITE_TIMEOUT_SECONDS, STEP_GATE_KEY


def payload(name, args=None, cwd="."):
    """Hook stdin as agy 1.5.15 sends it (keys measured 24 Nov 2026)."""
    call = {"name": name} if args is None else {"name": name, "args": args}
    return {"artifactDirectoryPath": cwd, "conversationId": "c1", "cwd": cwd,
            "hookEventName": "PreToolUse", "lastUserInput": "make a file", "modelName": "m",
            "stepIdx": 3, "toolCall": call, "transcriptPath": "t.jsonl",
            "workspacePaths": [cwd]}


@pytest.fixture
def hooked(tmp_path, monkeypatch):
    """A workspace whose .agents/hooks.json holds Gamachine's entry, a state
    file, and an approval endpoint that records each card it is asked for."""
    ws = tmp_path / "ws"
    (ws / ".agents").mkdir(parents=True)
    (ws / ".agents" / "hooks.json").write_text(json.dumps({gate.STEP_GATE_KEY: {}}),
                                               encoding="utf-8")
    state = tmp_path / "state.json"
    monkeypatch.setenv("LOCAL_APP_TOKEN", "t")
    monkeypatch.setenv("UNITYAI_URL", "http://approval.invalid")
    cards = []

    def urlopen(request, timeout):
        cards.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"status": "resolved", "approved": True}).encode())

    monkeypatch.setattr(native.urllib.request, "urlopen", urlopen)

    def decide(mode, name, args=None):
        gate.write_state(str(state), mode, "unityai", workspace=str(ws))
        raw = json.dumps(payload(name, args, str(ws))).encode()
        return gate.decide(raw, str(state), cwd=str(ws))

    return ws, decide, cards


# (b) balanced: agy 1.5's writers reach the card, and unparseable ones are denied.

def test_balanced_critical_write_file_and_edit_file_ask_for_the_card(hooked, tmp_path):
    ws, decide, cards = hooked
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "old.txt").write_bytes(b"keep\nold\n")
    created = decide("balanced", "write_file",
                     {"Path": str(outside / "new.txt"), "Content": "hello", "Overwrite": False})
    edited = decide("balanced", "edit_file",
                    {"Path": str(outside / "old.txt"), "Target": "old", "Replacement": "new"})
    assert created == {"decision": "allow"} and edited == {"decision": "allow"}
    assert [(c["params"]["path"], c["params"]["content"]) for c in cards] == [
        (os.path.realpath(outside / "new.txt"), "hello\n"),
        (os.path.realpath(outside / "old.txt"), "keep\nnew\n")]
    assert not (outside / "new.txt").exists()


def test_balanced_routine_write_file_in_the_workspace_needs_no_card(hooked):
    _, decide, cards = hooked
    out = decide("balanced", "write_file", {"Path": "src/a.txt", "Content": "x", "Overwrite": False})
    assert out == {"decision": "allow"} and cards == []


@pytest.mark.parametrize("name,args", [
    ("write_file", {"Path": "../x.txt", "Content": "x", "Append": True}),  # unmeasured flag
    ("write_file", {"Path": "../x.txt"}),                                    # no content
    ("write_file", "not an object"),
    ("edit_file", {"Target": "a", "Replacement": "b"}),                      # no path key
    ("edit_file", {"Path": "../x.txt", "TargetFile": "../y.txt", "Target": "a",
                   "Replacement": "b"}),                                     # two paths
    ("edit_file", {"Path": "src/a.txt", "Target": "a", "Replacement": "b",
                   "Destination": "../x.txt"}),                  # routine path, unmeasured key
])
def test_balanced_unparseable_native_writes_are_denied_without_a_card(hooked, name, args):
    _, decide, cards = hooked
    assert decide("balanced", name, args)["decision"] == "deny"
    assert cards == []


# (c) a tool the gate does not know is denied in step and balanced.

@pytest.mark.parametrize("mode", ["step", "balanced"])
@pytest.mark.parametrize("name", ["some_future_tool", "delete_file", "move", "generate_image",
                                  "invoke_subagent", "schedule", "execute_browser_javascript"])
def test_unknown_tools_are_denied(hooked, mode, name):
    _, decide, cards = hooked
    out = decide(mode, name, {"Path": "a.txt"})
    assert out["decision"] == "deny"
    assert repr(name) in out["reason"]
    assert cards == []


@pytest.mark.parametrize("mode", ["step", "balanced"])
def test_a_call_without_a_tool_name_is_denied(hooked, mode):
    ws, _, _ = hooked
    state = ws.parent / "state.json"
    gate.write_state(str(state), mode, "unityai", workspace=str(ws))
    for body in (dict(payload("x"), toolCall={"args": {}}),
                 {k: v for k, v in payload("x").items() if k != "toolCall"}):
        assert gate.decide(json.dumps(body).encode(), str(state), cwd=str(ws))["decision"] == "deny"


# (d) known reads still pass, and auto still allows what its rules do not name.

@pytest.mark.parametrize("mode", ["step", "balanced", "auto"])
@pytest.mark.parametrize("name,args", [
    ("view_file", {"AbsolutePath": "a.txt"}), ("list_dir", {"DirectoryPath": "."}),
    ("grep_search", {"Query": "x"}), ("find_by_name", {"Pattern": "*.cs"}),
    ("search_web", {"query": "unity"}), ("command_status", {"CommandId": "1"}),
    ("call_mcp_tool", {"ServerName": "unityMCP", "ToolName": "manage_scene"}),
])
def test_known_reads_and_mcp_calls_pass(hooked, mode, name, args):
    assert hooked[1](mode, name, args) == {"decision": "allow"}


def test_auto_secret_rule_sees_write_file(hooked, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".unity-mcp").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    token = str(home / ".unity-mcp" / "local-api-token")
    for name, args in [("write_file", {"Path": token, "Content": "x", "Overwrite": True}),
                       ("edit_file", {"Path": token, "Target": "a", "Replacement": "b"})]:
        assert hooked[1]("auto", name, args)["decision"] == "deny", name


# (a) and (e): the real hook, through the shim and hooks.json agy is given.

class _Approvals(BaseHTTPRequestHandler):
    cards: list = []
    approved = False

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).cards.append(body)
        answer = json.dumps({"status": "resolved", "approved": type(self).approved}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    def log_message(self, *args):
        pass


@pytest.fixture
def installed(tmp_path):
    """_write_step_gate's real files, with ~ pointed at a temporary home."""
    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    real_expand = os.path.expanduser
    with patch.object(agy_provider.os.path, "expanduser",
                      side_effect=lambda p: p.replace("~", str(home), 1) if p.startswith("~")
                      else real_expand(p)):
        yield ws


def _entries(ws):
    with open(ws / ".agents" / "hooks.json", encoding="utf-8") as f:
        return json.load(f)[STEP_GATE_KEY]["PreToolUse"]


@pytest.mark.parametrize("step_mode,mode", [(True, None), (True, "balanced"), (False, None)])
def test_hooks_file_sends_every_tool_to_the_hook(installed, step_mode, mode):
    assert AgyProvider()._write_step_gate(str(installed), step_mode=step_mode, mode=mode)
    entries = _entries(installed)
    # agy's matcher is a regex; Python's re stands in for its engine here.
    for name in ("write_file", "edit_file", "write_to_file", "run_command", "view_file",
                 "call_mcp_tool", "a_tool_agy_adds_later"):
        hit = [e for e in entries if re.fullmatch(e["matcher"], name)]
        assert hit, name
        if name in ("write_file", "edit_file", "write_to_file"):
            assert all(e["hooks"][0]["timeout"] >= NATIVE_WRITE_TIMEOUT_SECONDS for e in hit), name


def test_step_write_file_through_the_real_hook_waits_for_the_card(installed):
    AgyProvider()._write_step_gate(str(installed), step_mode=True)
    shim = _entries(installed)[0]["hooks"][0]["command"]
    server = HTTPServer(("127.0.0.1", 0), _Approvals)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = dict(os.environ, UNITYAI_URL=f"http://127.0.0.1:{server.server_port}",
               LOCAL_APP_TOKEN="t", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    argv = ["cmd", "/c", shim] if sys.platform == "win32" else [shim]
    args = {"Content": "hello", "Overwrite": False, "Path": "made.txt"}

    def run():
        out = subprocess.run(argv, input=json.dumps(payload("write_file", args, str(installed))).encode(),
                             capture_output=True, timeout=60, env=env,
                             cwd=str(installed / ".agents"))
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout.decode("utf-8").strip())

    try:
        _Approvals.cards, _Approvals.approved = [], False
        refused = run()
        _Approvals.approved = True
        approved = run()
    finally:
        server.shutdown()
        server.server_close()
    assert refused["decision"] == "deny"
    assert approved == {"decision": "allow"}
    assert [c["params"]["content"] for c in _Approvals.cards] == ["hello\n", "hello\n"]
    assert {c["params"]["path"] for c in _Approvals.cards} == {
        os.path.realpath(installed / "made.txt")}
    assert not (installed / "made.txt").exists()
