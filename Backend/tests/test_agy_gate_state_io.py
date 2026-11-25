"""Tests for F1 (gate state/claim IO retries) and F3 (mode-bound twin replay)."""
import json
import os
import sys
import threading
import time
from unittest.mock import patch

import pytest

import agy_native_approval as native
import agy_step_gate as gate
from providers import agy_provider, agy_session


def _write_hooks(ws):
    (ws / ".agents").mkdir(parents=True, exist_ok=True)
    (ws / ".agents" / "hooks.json").write_text(json.dumps({gate.STEP_GATE_KEY: {}}), encoding="utf-8")


def _tool_payload(ws, name="view_file", args=None, step=1, conversation="conv-io"):
    return json.dumps({
        "conversationId": conversation,
        "stepIdx": step,
        "workspacePaths": [str(ws)],
        "toolCall": {"name": name, "args": args or {}},
    }).encode("utf-8")


def test_replace_with_retry_succeeds_after_transient_permission_errors(tmp_path, monkeypatch):
    src = tmp_path / "src.tmp"
    dst = tmp_path / "dst.json"
    src.write_text('{"mode":"auto"}', encoding="utf-8")

    real_replace = os.replace
    attempts = [0]

    def flaky_replace(a, b):
        attempts[0] += 1
        if attempts[0] <= 3:
            raise PermissionError("sharing violation")
        return real_replace(a, b)

    monkeypatch.setattr(gate.os, "replace", flaky_replace)
    monkeypatch.setattr(gate.time, "sleep", lambda _: None)
    gate._replace_with_retry(str(src), str(dst), budget_s=1.0)
    assert attempts[0] == 4
    assert json.loads(dst.read_text(encoding="utf-8")) == {"mode": "auto"}


def test_replace_with_retry_reraises_when_budget_exhausted(tmp_path, monkeypatch):
    src = tmp_path / "src.tmp"
    dst = tmp_path / "dst.json"
    src.write_text("x", encoding="utf-8")
    now = [0.0]
    monkeypatch.setattr(gate.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(gate.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(gate.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(PermissionError):
        gate._replace_with_retry(str(src), str(dst), budget_s=0.05)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows enforces sharing violation on open handle")
def test_windows_open_handle_retries_and_succeeds_when_released(tmp_path):
    state = tmp_path / "gate.json"
    gate.write_state(str(state), "auto", "unityai")

    handle = open(state, encoding="utf-8")
    handle.read()
    released = threading.Event()

    def release_soon():
        time.sleep(0.08)
        handle.close()
        released.set()

    t = threading.Thread(target=release_soon)
    t.start()
    try:
        gate.write_state(str(state), "balanced", "unityai")
    finally:
        if not released.is_set():
            handle.close()
        t.join(5)
    assert json.loads(state.read_text(encoding="utf-8"))["mode"] == "balanced"


def test_remove_gate_state_retries_and_fails_closed(tmp_path, monkeypatch):
    state = tmp_path / "gate.json"
    state.write_text('{"mode":"auto"}', encoding="utf-8")
    monkeypatch.setattr(agy_provider, "gate_state_path", lambda: str(state))

    real_remove = os.remove
    calls = [0]

    def flaky_remove(path):
        calls[0] += 1
        if calls[0] <= 2:
            raise PermissionError("locked")
        return real_remove(path)

    monkeypatch.setattr(agy_session.os, "remove", flaky_remove)
    monkeypatch.setattr(agy_session.time, "sleep", lambda _: None)
    assert agy_session._remove_gate_state() is True
    assert not state.exists()

    state.write_text('{"mode":"auto"}', encoding="utf-8")
    now = [0.0]
    monkeypatch.setattr(agy_session.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(agy_session.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(agy_session.os, "remove", lambda p: (_ for _ in ()).throw(PermissionError("locked")))
    assert agy_session._remove_gate_state() is False
    assert state.exists()


def test_read_json_with_retry_handles_half_visible_and_permission_and_missing(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError):
        gate._read_json_with_retry(str(missing), budget_s=0.5)

    state = tmp_path / "state.json"
    state.write_text('{"mode":"auto","launcher":"unityai"}', encoding="utf-8")

    real_open = open
    attempts = [0]

    def flaky_open(path, *args, **kwargs):
        if str(path) == str(state):
            attempts[0] += 1
            if attempts[0] == 1:
                raise PermissionError("locked")
            if attempts[0] == 2:
                import io
                return io.StringIO('{"mode":')  # half-visible JSON
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", flaky_open)
    monkeypatch.setattr(gate.time, "sleep", lambda _: None)
    assert gate._read_json_with_retry(str(state), budget_s=0.5)["mode"] == "auto"
    assert attempts[0] == 3


def test_read_json_with_retry_exhausted_stays_fail_closed(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text("{broken", encoding="utf-8")
    now = [0.0]
    monkeypatch.setattr(gate.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(gate.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))

    assert gate.decide(_tool_payload(tmp_path), str(state))["decision"] == "deny"
    assert gate._state_mode(str(state)) is None

    # _read_decision contract: False for missing or garbled-final, None for still-empty.
    assert gate._read_decision(str(tmp_path / "absent.json")) is False
    assert gate._read_decision(str(state)) is False
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    assert gate._read_decision(str(empty)) is None

    # agy_native_approval._state_matches also retries and denies when exhausted.
    now_n = [0.0]
    monkeypatch.setattr(native.time, "monotonic", lambda: now_n[0])
    monkeypatch.setattr(native.time, "sleep", lambda s: now_n.__setitem__(0, now_n[0] + s))
    with pytest.raises(json.JSONDecodeError):
        native._state_matches(str(state))


def test_twin_denies_recorded_allow_when_mode_changed_and_allows_when_equal(tmp_path):
    ws = tmp_path / "ws"
    _write_hooks(ws)
    state = tmp_path / "gate" / "s.json"
    gate.write_state(str(state), "auto", "unityai", workspace=str(ws))
    raw = _tool_payload(ws, name="run_command", args={"CommandLine": "rm -rf x", "Cwd": str(ws)})

    assert gate.decide_once(raw, str(state), cwd=str(ws)) == {"decision": "allow"}
    folder = tmp_path / "gate" / gate.CALLS_DIR
    [claim_name] = os.listdir(folder)
    recorded = json.loads((folder / claim_name).read_text(encoding="utf-8"))
    assert recorded["mode"] == "auto"

    # Equal mode replays allow without leaking "mode".
    assert gate.decide_once(raw, str(state), cwd=str(ws)) == {"decision": "allow"}

    # Mode tightened to balanced or side: twin denies instead of replaying auto's allow.
    gate.write_state(str(state), "balanced", "unityai", workspace=str(ws))
    assert gate.decide_once(raw, str(state), cwd=str(ws))["decision"] == "deny"

    gate.write_state(str(state), "side", "unityai", workspace=str(ws))
    assert gate.decide_once(raw, str(state), cwd=str(ws))["decision"] == "deny"


def test_decide_once_reads_state_file_once_and_twin_returns_recorded_deny(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _write_hooks(ws)
    state = tmp_path / "gate" / "s.json"
    gate.write_state(str(state), "balanced", "unityai", workspace=str(ws))
    raw = _tool_payload(ws, name="run_command", args={"CommandLine": "rm -rf x", "Cwd": str(ws)})

    reads = [0]
    real_read = gate._read_json_with_retry

    def counting_read(path, *a, **k):
        if str(path) == str(state):
            reads[0] += 1
        return real_read(path, *a, **k)

    monkeypatch.setattr(gate, "_read_json_with_retry", counting_read)
    first = gate.decide_once(raw, str(state), cwd=str(ws))
    assert first["decision"] == "deny"
    assert reads[0] == 1

    # Twin still receives the recorded deny.
    assert gate.decide_once(raw, str(state), cwd=str(ws)) == first
