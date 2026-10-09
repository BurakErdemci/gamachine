"""Where each provider finds the chat mail tools, and what a wrong call gets.

Measured 27 Sep 2026: an OpenCode chat without its unityai server told the
user the terminal was "disabled by policy"; an agy branch called
send_chat_message on unityMCP and got a Unity approval card for an unknown
tool; a one-line note woke an agy branch that then spent 23 steps re-checking
its earlier work.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, mailbox

_ROWS = [{"from_conv": 7, "from_title": "Gönderen", "body": "derlemeyi dener misin?"}]


# ── instruction texts ────────────────────────────────────────────────────────

def _opencode_payload(monkeypatch):
    from providers import opencode_provider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setattr(opencode_provider, "resolve_opencode_cmd", lambda: ["opencode"])
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    p = opencode_provider.OpenCodeProvider(binary_name="opencode:opencode-go/kimi-k3")
    p._build_cmd("merhaba", workspace="C:/ws")
    return p._stdin_payload


def test_opencode_hint_uses_opencode_tool_names_and_the_mail_tools(monkeypatch):
    text = _opencode_payload(monkeypatch)
    for name in ("unityai_save_file", "unityai_delete_file", "unityai_run_terminal_command",
                 "unityai_read_file", "unityai_list_directory",
                 "unityai_list_chats", "unityai_send_chat_message"):
        assert name in text, name
    # No bare "unityai save_file" left: OpenCode shows <server>_<tool>.
    assert "unityai save_file" not in text
    assert "could not connect" in text and "never say a policy disabled them" in text


def test_opencode_bash_shadow_points_at_the_opencode_tool_name():
    from providers.opencode_provider import _BASH_SHADOW_TS
    assert "unityai_run_terminal_command" in _BASH_SHADOW_TS


def test_agy_instructions_put_mail_on_unityai_via_call_mcp_tool():
    from providers.agy_provider import AgyProvider
    with patch.object(AgyProvider, "_ensure_exec"):
        text = AgyProvider()._stream_instructions()
    assert "list_chats" in text and "send_chat_message" in text
    assert "call_mcp_tool" in text and 'server "unityai"' in text
    assert 'NEVER server "unityMCP"' in text
    assert "could not connect" in text and "never say a policy disabled it" in text
    # The write path is unchanged: still the unityai CLI through run_command.
    assert "Use write_to_file and replace_file_content to create or edit files" in text
    assert "mode they show an approval card and run only after explicit approval" in text
    assert "For deletes and shell commands use run_command calling 'unityai'" in text


# ── the mail note, per receiving provider ────────────────────────────────────

@pytest.mark.parametrize("provider_type,model,expected", [
    ("subscription", "claude-opus-5", "`mcp__gamachineMail__send_chat_message`"),
    ("subscription", "", "`mcp__gamachineMail__send_chat_message`"),
    ("subscription", "opencode:opencode-go/kimi-k3", "`unityai_send_chat_message`"),
    ("subscription", "gemini-3-pro", "`call_mcp_tool` ile (sunucu `unityai`, araç `send_chat_message`"),
    ("subscription", "agy-flash", "`call_mcp_tool` ile (sunucu `unityai`"),
    ("subscription", "gpt-5.4", "`unityai` MCP sunucusundaki `send_chat_message`"),
    ("subscription", "cursor-auto", "`unityai` MCP sunucusundaki `send_chat_message`"),
    ("subscription", "copilot-claude-sonnet-5", "`unityai` MCP sunucusundaki `send_chat_message`"),
    ("subscription", "kimi-k3", "`unityai` MCP sunucusundaki `send_chat_message`"),
    ("anthropic", "claude-sonnet-4-6", "cevabını `send_chat_message` aracıyla"),
    (None, None, "`unityai` MCP sunucusundaki `send_chat_message`"),
])
def test_turn_text_names_the_send_tool_as_the_receiver_sees_it(provider_type, model, expected):
    text = mailbox.turn_text(_ROWS, (), provider_type, model)
    assert expected in text
    assert "derlemeyi dener misin?" in text


def test_agy_note_says_not_unitymcp():
    text = mailbox.turn_text(_ROWS, (), "subscription", "gemini-3-pro")
    assert "`unityMCP` DEĞİL" in text and "not `unityMCP`" in text


def test_turn_text_is_framed_as_a_note_to_act_on_not_a_resume():
    text = mailbox.turn_text(_ROWS + [{"from_conv": 9, "from_title": "B", "body": "x"},
                                      {"from_conv": 7, "from_title": "Gönderen", "body": "y"}])
    assert "#7, #9 sohbetinden gelen bir not" in text
    assert "Önce notun istediğini yap" in text
    assert "önceki işine devam etme" in text and "yeniden doğrulama" in text
    assert "ilgisiz dosya okuma" in text
    assert "gönder ve dur" in text
    assert "do not resume or re-verify earlier work" in text
    assert "gerekirse işine devam et" not in text
    assert "kullanıcıdan DEĞİL" in text


def test_the_mail_wake_history_header_does_not_say_continue():
    from routes.conversation_routes import _build_handoff_context
    history = [{"role": "user", "content": "önce"}, {"role": "assistant", "content": "tamam"},
               {"role": "user", "content": "son"}]
    woke = _build_handoff_context("", history, history_header=mailbox.MAIL_WAKE_HISTORY_HEADER)
    assert "kaldığın yerden devam et" not in woke
    assert "yalnız bağlam için" in woke
    # A normal turn keeps its header.
    assert "kaldığın yerden devam et" in _build_handoff_context("", history)


# ── a mail tool asked through the Unity MCP gate ─────────────────────────────

def _client():
    from routes.conversation_routes import create_conversation_router
    app = FastAPI()
    app.include_router(create_conversation_router(MagicMock(), {}))
    return TestClient(app)


def _request(client, gate_id, tool, params):
    return client.post("/mcp-approval-request", json={
        "gate_id": gate_id, "tool": tool, "params": params, "workspace_path": ""}).json()


@pytest.mark.parametrize("mode", ["auto", "balanced", "step"])
@pytest.mark.parametrize("tool", ["send_chat_message", "list_chats"])
def test_a_mail_tool_on_the_unity_gate_is_refused_without_a_card(mode, tool):
    approval_mode.set_mode(mode, source="test")
    with _client() as client:
        result = _request(client, "m-1", tool, {"to_chat_id": 3, "message": "selam"})
        assert result["status"] == "resolved" and result["approved"] is False, result
        assert "`unityai`" in result["error"] and "call_mcp_tool" in result["error"]
        assert f"unityai_{tool}" in result["error"]
        assert client.get("/mcp-pending").json()["pending"] == {}


def test_the_refusal_reaches_the_model_through_the_unity_gate_wording():
    # approval_gate.kapiyi_gec raises ApprovalDenied(f"... onaylanmadı: {error}")
    # and the middleware returns it as the tool result; the reason must stand
    # on its own at the end of that sentence.
    reason = mailbox.wrong_server_refusal("send_chat_message")
    assert reason.startswith("`send_chat_message` Unity (unityMCP) sunucusunda yok")


def test_other_unknown_unity_tools_still_raise_a_critical_card_in_balanced():
    approval_mode.set_mode("balanced", source="test")
    with _client() as client:
        assert _request(client, "u-1", "no_such_tool", {}) == {"status": "ok", "gate_id": "u-1"}
        pending = client.get("/mcp-pending").json()["pending"]
        assert pending["u-1"]["risk_reason"] == "unity_unknown_tool"
        client.post("/mcp-approval-respond/u-1", json={"approved": False})


# ── the other CLIs: Copilot, Cursor, Codex, Kimi, one-shot Claude ─────────────

def _unity_off():
    import sys
    m = MagicMock()
    m.unity_mcp_manager.is_running.return_value = False
    m.unity_mcp_manager.mcp_port = 8080
    return patch.dict(sys.modules, {
        "unity_ai_mcp": MagicMock(unity_mcp_manager=m.unity_mcp_manager),
        "unity_ai_mcp.unity_mcp_manager": m,
    })


def _prompt_text(provider, cmd):
    return "\n".join(str(x) for x in cmd) + "\n" + str(getattr(provider, "_stdin_payload", "") or "")


def _copilot(tmp_path):
    from providers.copilot_provider import CopilotProvider
    p = CopilotProvider(binary_name="copilot-claude-sonnet-5")
    with _unity_off(), patch("providers.copilot_provider.resolve_copilot_cmd",
                             return_value=["copilot"]):
        return _prompt_text(p, p._build_cmd("merhaba", workspace=str(tmp_path)))


def _cursor(tmp_path):
    from providers.cursor_provider import CursorProvider
    p = CursorProvider(binary_name="cursor-auto")
    with _unity_off(), patch("providers.cursor_provider.resolve_cursor_cmd",
                             return_value=["agent"]):
        return _prompt_text(p, p._build_cmd("merhaba", workspace=str(tmp_path)))


def _codex(tmp_path):
    from providers.codex_provider import CodexProvider
    p = CodexProvider("gpt-5.6-sol")
    with _unity_off():
        return _prompt_text(p, p._build_cmd("merhaba", workspace=str(tmp_path)))


def _kimi(tmp_path, monkeypatch):
    from providers.kimi_provider import KimiProvider
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    p = KimiProvider(binary_name="kimi-k3")
    with _unity_off(), patch.object(KimiProvider, "_ensure_exec"):
        return _prompt_text(p, p._build_cmd("merhaba", workspace=str(tmp_path)))


def _claude_cli(tmp_path):
    from providers.claude_provider import ClaudeCodeProvider
    p = ClaudeCodeProvider("claude-opus-5")
    with _unity_off(), patch.object(ClaudeCodeProvider, "_product_mcp_servers",
                                    return_value={}):
        return _prompt_text(p, p._build_cmd("merhaba", workspace=str(tmp_path)))


_ALL_TOOLS = ("save_file", "delete_file", "run_terminal_command", "read_file",
              "list_directory", "list_chats", "send_chat_message")


@pytest.mark.parametrize("build", ["copilot", "cursor"])
def test_cli_without_a_measured_naming_names_the_unityai_server(build, tmp_path):
    text = {"copilot": _copilot, "cursor": _cursor}[build](tmp_path)
    for tool in _ALL_TOOLS:
        assert f"{tool} (unityai MCP server)" in text or f"{tool}  |" in text, tool
    assert "list_chats (unityai MCP server), send_chat_message (unityai MCP server)" in text
    assert "unityai save_file" not in text
    assert "could not connect" in text and "never say a policy disabled them" in text


@pytest.mark.parametrize("build", ["codex", "kimi", "claude"])
def test_cli_with_mcp_double_underscore_names_every_tool_that_way(build, tmp_path, monkeypatch):
    text = {"codex": lambda: _codex(tmp_path), "kimi": lambda: _kimi(tmp_path, monkeypatch),
            "claude": lambda: _claude_cli(tmp_path)}[build]()
    for tool in _ALL_TOOLS:
        assert f"mcp__unityai__{tool}" in text, tool
    assert "could not connect" in text and "never say a policy disabled them" in text


def test_opencode_uses_the_shared_fallback_sentence(monkeypatch):
    from providers.unityai_tool_text import TOOLS_MISSING_HINT
    assert TOOLS_MISSING_HINT in _opencode_payload(monkeypatch)
