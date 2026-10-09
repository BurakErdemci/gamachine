"""Native file previews and approval decisions, without network access."""
import io
import json
from unittest.mock import patch

import pytest

import agy_native_approval as native
import agy_step_gate as gate


@pytest.fixture
def approval(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    gate.write_state(str(state), "step", "unityai", workspace=str(tmp_path))
    token = native.state_path.set(str(state))
    monkeypatch.setenv("LOCAL_APP_TOKEN", "session-secret")
    monkeypatch.setenv("UNITYAI_APPROVAL_TURN_TOKEN", "turn-secret")
    monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", "42")
    monkeypatch.setenv("UNITYAI_URL", "http://approval.invalid")
    calls = []
    answers = [{"status": "resolved", "approved": True}]

    def urlopen(request, timeout):
        calls.append(request)
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if callable(answer):
            answer = answer()
        return io.BytesIO(json.dumps(answer).encode())

    monkeypatch.setattr(native.urllib.request, "urlopen", urlopen)
    yield tmp_path, state, calls, answers
    native.state_path.reset(token)


def write(approval, **overrides):
    workspace, _, _, _ = approval
    args = {"TargetFile": "a.txt", "CodeContent": "new", "Overwrite": True}
    args.update(overrides)
    return native.request_native_write("write_to_file", args, str(workspace))


def replace(approval, **overrides):
    workspace, _, _, _ = approval
    args = {"TargetFile": "a.txt", "TargetContent": "old", "ReplacementContent": "new",
            "StartLine": 1, "EndLine": 3, "AllowMultiple": False}
    args.update(overrides)
    return native.request_native_write("replace_file_content", args, str(workspace))


@pytest.mark.parametrize("content,expected", [("hi", "hi\n"), ("hi\n", "hi\n"),
                                               ("hi\r\nthere\r\n", "hi\nthere\n"), ("", "\n")])
def test_write_preview_and_request_identity(approval, content, expected):
    workspace, _, calls, _ = approval
    (workspace / "a.txt").write_bytes(b"old\r\n")
    assert write(approval, CodeContent=content)[0] is True
    assert len(calls) == 1
    request = calls[0]
    body = json.loads(request.data)
    assert body == {"gate_id": body["gate_id"], "tool": "write_file",
                    "params": {"path": str(workspace / "a.txt"), "content": expected,
                               "original": "old\r\n"},
                    "workspace_path": str(workspace), "approval_turn_token": "turn-secret",
                    "conversation_id": 42}
    assert request.get_header("X-session-token") == "session-secret"
    assert request.get_header("Content-type") == "application/json"
    assert (workspace / "a.txt").read_bytes() == b"old\r\n"


def test_replace_preserves_crlf(approval):
    workspace, _, calls, _ = approval
    (workspace / "a.txt").write_bytes(b"one\r\nold\r\nthree\r\n")
    assert replace(approval, TargetContent="old\r\n", ReplacementContent="new\n")[0]
    params = json.loads(calls[0].data)["params"]
    assert params["content"] == "one\r\nnew\r\nthree\r\n"


@pytest.mark.parametrize("text,overrides", [("absent", {}), ("old old", {}),
    ("one\nold\n", {"StartLine": 1, "EndLine": 1}),
    ("old\nend", {"StartLine": 2, "EndLine": 3}),
    ("old", {"StartLine": 0, "EndLine": 1}),
    ("aaa", {"TargetContent": "aa"}),
    ("old\nend", {"TargetContent": "old\nend", "EndLine": 1}),
    ("old", {"AllowMultiple": True}), ("old", {"StartLine": True}),
    ("old", {"TargetContent": ""}), ("old", {"ReplacementContent": None})])
def test_unverified_replacements_deny_without_request(approval, text, overrides):
    workspace, _, calls, _ = approval
    (workspace / "a.txt").write_text(text, encoding="utf-8")
    allowed, reason = replace(approval, **overrides)
    assert not allowed and "write_to_file" in reason
    assert calls == []


@pytest.mark.parametrize("overrides", [{"Overwrite": False}, {"Overwrite": 1},
    {"CodeContent": None}, {"TargetFile": None}])
def test_invalid_write_denied(approval, overrides):
    workspace, _, calls, _ = approval
    (workspace / "a.txt").write_text("old", encoding="utf-8")
    assert not write(approval, **overrides)[0]
    assert calls == []


def test_existing_file_without_overwrite(approval):
    workspace, _, calls, _ = approval
    (workspace / "a.txt").write_text("old", encoding="utf-8")
    allowed, reason = native.request_native_write("write_to_file",
        {"TargetFile": "a.txt", "CodeContent": "x"}, str(workspace))
    assert not allowed and "unverified agy behaviour" in reason
    assert calls == []


@pytest.mark.parametrize("path", ["../outside.txt", "Assets/A.cs.meta", "Assets/Main.unity"])
def test_outside_and_unity_paths_never_request(approval, path):
    workspace, _, calls, _ = approval
    (workspace / "Assets").mkdir()
    (workspace / "ProjectSettings").mkdir()
    assert not write(approval, TargetFile=path)[0]
    assert calls == []


def test_backslash_path(approval):
    workspace, _, calls, _ = approval
    (workspace / "sub").mkdir()
    assert write(approval, TargetFile="sub\\a.txt")[0]
    assert json.loads(calls[0].data)["params"]["path"] == str(workspace / "sub" / "a.txt")


@pytest.mark.parametrize("approved", ["true", 1, False, None])
def test_only_boolean_true_allows(approval, approved):
    approval[3][:] = [{"status": "resolved", "approved": approved}]
    assert not write(approval)[0]


def test_missing_approval_denies(approval):
    approval[3][:] = [{"status": "resolved"}]
    assert not write(approval)[0]


def test_poll_resolution(approval, monkeypatch):
    approval[3][:] = [{"status": "ok"}, {"status": "resolved", "approved": True}]
    monkeypatch.setattr(native.time, "sleep", lambda _: None)
    assert write(approval)[0]
    assert approval[2][1].get_method() == "GET"
    body = json.loads(approval[2][0].data)
    assert approval[2][1].full_url.endswith("/mcp-approval-result/" + body["gate_id"])


def test_poll_timeout_uses_wall_clock(approval, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(native.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(native.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    approval[3][:] = [{"status": "pending"}]
    allowed, reason = write(approval)
    assert not allowed and "timed out" in reason
    assert now[0] <= 160


@pytest.mark.parametrize("mode", ["closed", "side", "auto"])
def test_mode_changed_before_allow(approval, mode):
    workspace, state, _, answers = approval
    def flip():
        gate.write_state(str(state), mode, "unityai", workspace=str(workspace))
        return {"status": "resolved", "approved": True}
    answers[:] = [flip]
    assert not write(approval)[0]


def test_file_changed_before_allow(approval):
    workspace, _, _, answers = approval
    def change():
        (workspace / "a.txt").write_text("changed", encoding="utf-8")
        return {"status": "resolved", "approved": True}
    answers[:] = [change]
    allowed, reason = write(approval)
    assert not allowed and "file changed since the card was shown" in reason


def test_missing_workspace_in_state(approval):
    workspace, state, calls, _ = approval
    gate.write_state(str(state), "step", "unityai")
    payload = {"toolCall": {"name": "write_to_file", "args": {
        "TargetFile": "a.txt", "CodeContent": "new"}}}
    assert gate.decide(json.dumps(payload).encode(), str(state))["decision"] == "deny"
    assert calls == []


@pytest.mark.parametrize("owner", ["0", "-1", " 42", "+42", "٤٢", str(2**63), ""])
def test_invalid_conversation_owner_omitted(approval, monkeypatch, owner):
    monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", owner)
    assert write(approval)[0]
    assert "conversation_id" not in json.loads(approval[2][0].data)


def test_unsupported_tool_and_missing_target_deny(approval):
    workspace, _, calls, _ = approval
    assert not native.request_native_write("multi_replace_file_content", {}, str(workspace))[0]
    assert not replace(approval)[0]
    assert calls == []


def test_malformed_response_denies(approval, monkeypatch):
    monkeypatch.setattr(native.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"not json"))
    assert not write(approval)[0]


def test_post_retries_keep_one_gate_and_request_body(approval, monkeypatch):
    calls = []
    def flaky(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise native.urllib.error.URLError("not ready")
        return io.BytesIO(b'{"status":"resolved","approved":true}')
    monkeypatch.setattr(native.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(native.time, "sleep", lambda _: None)
    assert write(approval)[0]
    assert len(calls) == 2 and calls[0].data == calls[1].data


def test_http_error_post_budget(approval, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(native.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(native.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    def unavailable(request, timeout):
        raise native.urllib.error.HTTPError(request.full_url, 503, "unavailable", None, None)
    monkeypatch.setattr(native.urllib.request, "urlopen", unavailable)
    assert not write(approval)[0]
    assert now[0] <= 10


def test_poll_http_error_denies(approval, monkeypatch):
    calls = []
    def unavailable(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            return io.BytesIO(b'{"status":"ok"}')
        raise native.urllib.error.HTTPError(request.full_url, 500, "failed", None, None)
    monkeypatch.setattr(native.urllib.request, "urlopen", unavailable)
    monkeypatch.setattr(native.time, "sleep", lambda _: None)
    assert not write(approval)[0]
    assert len(calls) == 2


def test_missing_state_before_return_denies(approval):
    _, state, _, answers = approval
    def remove_state():
        state.unlink()
        return {"status": "resolved", "approved": True}
    answers[:] = [remove_state]
    assert not write(approval)[0]


def test_symlink_outside_workspace_denies(approval, tmp_path_factory):
    workspace, _, calls, _ = approval
    outside = tmp_path_factory.mktemp("outside")
    try:
        (workspace / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this machine")
    assert not write(approval, TargetFile="link/a.txt")[0]
    assert calls == []


def test_hook_exception_always_prints_deny_json(approval, monkeypatch, capsys):
    _, state, _, _ = approval
    payload = {"toolCall": {"name": "write_to_file", "args": {"TargetFile": "a.txt"}}}
    monkeypatch.setattr(gate.sys, "stdin", type("Input", (), {
        "buffer": io.BytesIO(json.dumps(payload).encode())})())
    with patch.object(native, "request_native_write", side_effect=RuntimeError("unexpected")):
        assert gate.main(["--state", str(state)]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "deny"
