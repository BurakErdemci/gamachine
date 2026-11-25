"""The two HIGH findings of the fail-closed agy gate review (25 Nov 2026).

H1: a main child's hooks.json held only ".*"; on an agy build that does not
read a matcher as a regex no hook fired and the gate was fully open. Now the
exact name of every known writer and run_command stands beside it, and one
tool call gets one decision (and at most one card) however many entries match.

H2: a built-in writer's relative path was resolved against the hook's own
working directory; agy writes it into its workspace (workspacePaths[0]), so a
".." path could leave the workspace while the hook judged it routine.
"""
import io
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

import agy_native_approval as native
import agy_step_gate as gate
from providers import agy_provider
from providers.agy_provider import (AgyProvider, HOOK_TIMEOUT_SECONDS,
                                    NATIVE_WRITE_TIMEOUT_SECONDS, STEP_GATE_KEY)

EXACT_NAMES = gate.WRITE_TOOLS + (gate.RUN_TOOL,)


def payload(name, args, *, workspace=None, cwd=".", step=3, conversation="conv-1"):
    """Hook stdin as agy 1.5.15 sends it (keys measured 24 Nov 2026)."""
    body = {"conversationId": conversation, "stepIdx": step, "cwd": cwd,
            "hookEventName": "PreToolUse", "toolCall": {"name": name, "args": args}}
    if workspace is not None:
        body["workspacePaths"] = [workspace]
    return json.dumps(body).encode()


# ── H1: the matchers ────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["auto", "balanced"])
def test_a_main_child_matches_every_writer_and_run_command_by_exact_name(mode):
    matchers = agy_provider.step_gate_matchers(mode)
    assert matchers[0] == ".*"
    for name in EXACT_NAMES:
        assert name in matchers, name
    for name in ("delete_file", "move", "shell_exec", "run_script"):
        assert name in matchers, name
    assert len(set(matchers)) == len(matchers)


def test_a_side_child_keeps_its_own_list():
    assert agy_provider.step_gate_matchers(gate.SIDE_MODE) == \
        agy_provider.STEP_GATE_TOOLS + gate.SIDE_HOOK_MATCHERS


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


@pytest.mark.parametrize("mode", ["auto", "balanced"])
def test_a_build_that_reads_matchers_as_plain_names_still_gates_every_writer(installed, mode):
    assert AgyProvider()._write_step_gate(str(installed), mode=mode)
    entries = _entries(installed)
    for name in EXACT_NAMES:
        exact = [e for e in entries if e["matcher"] == name]
        assert len(exact) == 1, name
        timeout = exact[0]["hooks"][0]["timeout"]
        # A native writer waits for its card inside the hook.
        assert timeout == (NATIVE_WRITE_TIMEOUT_SECONDS if name in gate.NATIVE_WRITERS
                           else HOOK_TIMEOUT_SECONDS), name
    # Every entry runs the same shim.
    assert len({e["hooks"][0]["command"] for e in entries}) == 1


# ── H1: one call, one decision ──────────────────────────────────────────────

@pytest.fixture
def hooked(tmp_path, monkeypatch):
    """A gated workspace, a balanced state file and a recording card endpoint."""
    ws = tmp_path / "ws"
    (ws / ".agents").mkdir(parents=True)
    (ws / ".agents" / "hooks.json").write_text(json.dumps({gate.STEP_GATE_KEY: {}}), encoding="utf-8")
    state = tmp_path / "gate" / "step-gate.json"
    gate.write_state(str(state), "balanced", "unityai", workspace=str(ws))
    monkeypatch.setenv("LOCAL_APP_TOKEN", "t")
    monkeypatch.setenv("UNITYAI_URL", "http://approval.invalid")
    cards = []

    def urlopen(request, timeout):
        cards.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"status": "resolved", "approved": True}).encode())

    monkeypatch.setattr(native.urllib.request, "urlopen", urlopen)
    return ws, str(state), cards


def _critical_write(ws, step=3, conversation="conv-1"):
    return payload("write_file", {"Path": ".git/notes.txt", "Content": "hi", "Overwrite": False},
                   workspace=str(ws), cwd=str(ws), step=step, conversation=conversation)


def test_two_runs_of_one_call_share_one_card_and_one_decision(hooked):
    ws, state, cards = hooked
    first = gate.decide_once(_critical_write(ws), state, cwd=str(ws))
    second = gate.decide_once(_critical_write(ws), state, cwd=str(ws))
    assert first == second == {"decision": "allow"}
    assert len(cards) == 1


def test_a_different_step_or_conversation_is_a_new_call(hooked):
    ws, state, cards = hooked
    for step, conversation in ((3, "conv-1"), (4, "conv-1"), (3, "conv-2")):
        assert gate.decide_once(_critical_write(ws, step, conversation), state,
                                cwd=str(ws))["decision"] == "allow"
    assert len(cards) == 3


def test_a_payload_that_does_not_name_its_step_is_never_deduplicated(hooked):
    ws, state, cards = hooked
    body = json.loads(_critical_write(ws))
    del body["stepIdx"]
    raw = json.dumps(body).encode()
    gate.decide_once(raw, state, cwd=str(ws))
    gate.decide_once(raw, state, cwd=str(ws))
    assert len(cards) == 2


def test_the_same_step_with_other_arguments_is_not_replayed(hooked):
    ws, state, cards = hooked
    gate.decide_once(_critical_write(ws), state, cwd=str(ws))
    other = json.loads(_critical_write(ws))
    other["toolCall"]["args"]["Content"] = "something else"
    gate.decide_once(json.dumps(other).encode(), state, cwd=str(ws))
    assert len(cards) == 2


def test_a_twin_running_at_the_same_time_waits_for_the_first_decision(hooked):
    ws, state, _ = hooked
    asked, release = threading.Event(), threading.Event()
    calls, results = [], {}

    def slow_card(*args, **kwargs):
        calls.append(args)
        asked.set()
        assert release.wait(10)
        return True, "approved"

    with patch.object(native, "request_native_write", side_effect=slow_card):
        first = threading.Thread(target=lambda: results.__setitem__(
            "first", gate.decide_once(_critical_write(ws), state, cwd=str(ws))))
        twin = threading.Thread(target=lambda: results.__setitem__(
            "twin", gate.decide_once(_critical_write(ws), state, cwd=str(ws))))
        first.start()
        assert asked.wait(10)
        twin.start()
        time.sleep(0.3)
        assert "twin" not in results  # it waits; it did not decide alone
        release.set()
        first.join(10)
        twin.join(10)
    assert results == {"first": {"decision": "allow"}, "twin": {"decision": "allow"}}
    assert len(calls) == 1


def test_a_recorded_allow_is_not_replayed_into_a_closed_gate(hooked):
    ws, state, cards = hooked
    assert gate.decide_once(_critical_write(ws), state, cwd=str(ws)) == {"decision": "allow"}
    gate.write_state(state, gate.CLOSED_MODE, "")
    assert gate.decide_once(_critical_write(ws), state, cwd=str(ws))["decision"] == "deny"
    assert len(cards) == 1


def test_a_recorded_deny_stays_a_deny(hooked):
    ws, state, cards = hooked
    denied = payload("sed_file", {"TargetFile": "../x.txt"}, workspace=str(ws), cwd=str(ws))
    first = gate.decide_once(denied, state, cwd=str(ws))
    assert first["decision"] == "deny"
    assert gate.decide_once(denied, state, cwd=str(ws)) == first


def test_an_old_record_is_pruned_and_the_call_decided_again(hooked):
    ws, state, cards = hooked
    gate.decide_once(_critical_write(ws), state, cwd=str(ws))
    folder = os.path.join(os.path.dirname(state), gate.CALLS_DIR)
    old = time.time() - gate.CALL_RECORD_SECONDS - 5
    for name in os.listdir(folder):
        os.utime(os.path.join(folder, name), (old, old))
    gate.decide_once(_critical_write(ws), state, cwd=str(ws))
    assert len(cards) == 2


def test_a_garbled_record_denies_the_twin(hooked):
    ws, state, cards = hooked
    gate.decide_once(_critical_write(ws), state, cwd=str(ws))
    folder = os.path.join(os.path.dirname(state), gate.CALLS_DIR)
    [name] = os.listdir(folder)
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        f.write('{"decision": "maybe"}')
    assert gate.decide_once(_critical_write(ws), state, cwd=str(ws))["decision"] == "deny"
    assert len(cards) == 1


class _SlowApprovals(BaseHTTPRequestHandler):
    """Answers each card after a pause, so two hook runs overlap."""
    cards: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).cards.append(body)
        time.sleep(1.0)
        answer = json.dumps({"status": "resolved", "approved": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    def log_message(self, *args):
        pass


def test_the_real_hook_run_twice_at_once_for_one_call_asks_one_card(installed):
    """What agy would do if both ".*" and "write_file" match: two hook
    processes for one call, side by side, through the real shim."""
    AgyProvider()._write_step_gate(str(installed), mode="balanced")
    shim = _entries(installed)[0]["hooks"][0]["command"]
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowApprovals)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = dict(os.environ, UNITYAI_URL=f"http://127.0.0.1:{server.server_port}",
               LOCAL_APP_TOKEN="t", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    argv = ["cmd", "/c", shim] if sys.platform == "win32" else [shim]
    raw = _critical_write(installed)
    _SlowApprovals.cards = []
    try:
        runs = [subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=env, cwd=str(installed / ".agents"))
                for _ in range(2)]
        outs = [run.communicate(raw, timeout=60) for run in runs]
    finally:
        server.shutdown()
        server.server_close()
    decisions = [json.loads(out.decode("utf-8").strip()) for out, _ in outs]
    assert decisions == [{"decision": "allow"}, {"decision": "allow"}], outs
    assert len(_SlowApprovals.cards) == 1
    assert _SlowApprovals.cards[0]["params"]["path"] == os.path.realpath(
        installed / ".git" / "notes.txt")


# ── H2: relative paths resolve against the agy workspace ────────────────────

def test_a_dotdot_path_is_judged_against_the_agy_workspace_not_the_hook_cwd(hooked):
    """The finding's shape: the hook runs one level below the workspace, so
    "../x.txt" looked like ws/x.txt (routine) while agy writes it beside ws."""
    ws, state, cards = hooked
    sub = ws / "sub"
    sub.mkdir()
    raw = payload("write_file", {"Path": "../escape.txt", "Content": "x", "Overwrite": False},
                  workspace=str(ws), cwd=str(sub))
    out = gate.decide(raw, state, cwd=str(sub))
    # Critical (outside the workspace): a native write asks for the card,
    # and the card names the file agy would write.
    assert out == {"decision": "allow"}
    assert [c["params"]["path"] for c in cards] == [os.path.realpath(ws.parent / "escape.txt")]


@pytest.mark.parametrize("name", ["sed_file", "multi_replace_file_content"])
def test_a_dotdot_path_of_a_writer_without_a_card_is_denied(hooked, name):
    ws, state, cards = hooked
    sub = ws / "sub"
    sub.mkdir()
    raw = payload(name, {"TargetFile": "../escape.txt"}, workspace=str(ws), cwd=str(sub))
    out = gate.decide(raw, state, cwd=str(sub))
    assert out["decision"] == "deny"
    assert "file_outside_workspace" in out["reason"]
    assert cards == []


def test_a_relative_path_inside_the_agy_workspace_stays_routine(hooked):
    ws, state, cards = hooked
    sub = ws / "sub"
    sub.mkdir()
    raw = payload("write_file", {"Path": "src/a.txt", "Content": "x", "Overwrite": False},
                  workspace=str(ws), cwd=str(sub))
    assert gate.decide(raw, state, cwd=str(sub)) == {"decision": "allow"}
    assert cards == []


def test_an_agy_workspace_outside_the_gated_one_makes_a_relative_write_critical(hooked, tmp_path):
    ws, state, cards = hooked
    other = tmp_path / "other"
    other.mkdir()
    raw = payload("write_file", {"Path": "a.txt", "Content": "x", "Overwrite": False},
                  workspace=str(other), cwd=str(ws))
    assert gate.decide(raw, state, cwd=str(ws)) == {"decision": "allow"}
    assert [c["params"]["path"] for c in cards] == [os.path.realpath(other / "a.txt")]


@pytest.mark.parametrize("workspace_paths", [None, [], ["relative/ws"], [7], [""]])
@pytest.mark.parametrize("target", ["a.txt", "src/a.txt", "../a.txt"])
def test_a_relative_path_with_no_known_agy_workspace_is_critical(hooked, workspace_paths, target):
    ws, state, cards = hooked
    body = json.loads(payload("write_file", {"Path": target, "Content": "x", "Overwrite": False},
                              cwd=str(ws)))
    if workspace_paths is not None:
        body["workspacePaths"] = workspace_paths
    out = gate.decide(json.dumps(body).encode(), state, cwd=str(ws))
    # No card is possible: it could not name the file agy writes.
    assert out == gate._deny(gate.RELATIVE_WRITE_REASON)
    assert cards == []
    sed = json.loads(payload("sed_file", {"TargetFile": target}, cwd=str(ws)))
    if workspace_paths is not None:
        sed["workspacePaths"] = workspace_paths
    out = gate.decide(json.dumps(sed).encode(), state, cwd=str(ws))
    assert out["decision"] == "deny" and "file_outside_workspace" in out["reason"]


def test_an_absolute_path_needs_no_agy_workspace(hooked):
    ws, state, cards = hooked
    raw = payload("write_file", {"Path": str(ws / "a.txt"), "Content": "x", "Overwrite": False},
                  cwd=str(ws))
    assert gate.decide(raw, state, cwd=str(ws)) == {"decision": "allow"}
    assert cards == []


@pytest.mark.parametrize("key", ["TargetFile", "Path", "FilePath", "file_path", "File",
                                 "AbsolutePath", "TargetPath", "target_path"])
def test_every_edit_file_path_key_resolves_against_the_agy_workspace(hooked, key):
    ws, state, cards = hooked
    sub = ws / "sub"
    sub.mkdir()
    (ws.parent / "escape.txt").write_bytes(b"keep old\n")
    raw = payload("edit_file", {key: "../escape.txt", "Target": "old", "Replacement": "new"},
                  workspace=str(ws), cwd=str(sub))
    assert gate.decide(raw, state, cwd=str(sub)) == {"decision": "allow"}
    assert [(c["params"]["path"], c["params"]["content"]) for c in cards] == [
        (os.path.realpath(ws.parent / "escape.txt"), "keep new\n")]


@pytest.mark.parametrize("name,args", [
    ("write_file", {"Path": "../escape.txt", "Content": "x", "Overwrite": False}),
    ("write_to_file", {"TargetFile": "../escape.txt", "CodeContent": "x"}),
])
def test_every_native_writer_path_key_resolves_against_the_agy_workspace(hooked, name, args):
    ws, state, cards = hooked
    sub = ws / "sub"
    sub.mkdir()
    raw = payload(name, args, workspace=str(ws), cwd=str(sub))
    assert gate.decide(raw, state, cwd=str(sub)) == {"decision": "allow"}
    assert [c["params"]["path"] for c in cards] == [os.path.realpath(ws.parent / "escape.txt")]


def test_the_unity_file_rule_sees_a_relative_path_in_the_agy_workspace(hooked, tmp_path):
    ws, state, cards = hooked
    for folder in ("Assets", "ProjectSettings"):
        (ws / folder).mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    raw = payload("write_file", {"Path": "Assets/Player.cs.meta", "Content": "x"},
                  workspace=str(ws), cwd=str(elsewhere))
    for mode in ("auto", "balanced"):
        gate.write_state(state, mode, "unityai", workspace=str(ws))
        out = gate.decide(raw, state, cwd=str(elsewhere))
        assert out["decision"] == "deny" and ".meta" in out["reason"], (mode, out)
    assert cards == []


def test_the_secret_rule_sees_a_relative_path_in_the_agy_workspace(hooked, tmp_path, monkeypatch):
    ws, state, cards = hooked
    home = tmp_path / "home"
    (home / ".unity-mcp").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    raw = payload("write_file", {"Path": "../home/.unity-mcp/local-api-token", "Content": "x"},
                  workspace=str(ws), cwd=str(elsewhere / "deep"))
    for mode in ("auto", "balanced"):
        gate.write_state(state, mode, "unityai", workspace=str(ws))
        assert gate.decide(raw, state, cwd=str(elsewhere / "deep"))["decision"] == "deny", mode
    assert cards == []
