"""OpenCode context reading: last step_finish tokens plus the catalog window."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import providers.oneshot_cli as oneshot
from providers.copilot_provider import CopilotProvider
from providers.opencode_provider import OpenCodeProvider
from routes.conversation_routes import _context_usage_payload, _live_context_reading

FIXTURES = Path(__file__).parent / "fixtures" / "opencode"
MODEL = "opencode/big-pickle"


def _fixture_lines():
    return (FIXTURES / "step_finish.jsonl").read_text(encoding="utf-8").splitlines()


def _unity_mcp():
    m = MagicMock()
    m.unity_mcp_manager.is_running.return_value = False
    return patch.dict(sys.modules, {"unity_ai_mcp": MagicMock(unity_mcp_manager=m.unity_mcp_manager),
                                    "unity_ai_mcp.unity_mcp_manager": m})


def _run(provider, lines):
    script = "import sys\n" + "".join(
        f"sys.stdout.write({json.dumps(line + chr(10))})\n" for line in lines)
    fd, path = tempfile.mkstemp(prefix="fake_cli_", suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(script)
        with _unity_mcp(), \
             patch.object(type(provider), "_build_cmd", lambda self, *a, **k: [sys.executable, path]), \
             patch.object(type(provider), "_write_mcp_config", lambda self, ws: ""):
            async def collect():
                return [ev async for ev in provider.analyze_code("test", cwd=os.getcwd())]
            return asyncio.run(collect())
    finally:
        os.unlink(path)


def _step_finish(tokens):
    return json.dumps({"type": "step_finish", "sessionID": "ses_x",
                       "part": {"reason": "stop", "tokens": tokens}})


def test_reading_is_set_from_a_real_shaped_step_finish():
    p = OpenCodeProvider(binary_name=f"opencode:{MODEL}")
    assert p.context_reading is None
    with patch("providers.opencode_provider.opencode_context_window", return_value=200_000):
        _run(p, _fixture_lines())
    assert p.context_reading == {
        "used": 17_325, "window": 200_000, "percent": 100 * 17_325 / 200_000, "model": MODEL,
    }


def test_reading_comes_from_the_last_model_call_not_the_turn_total():
    p = OpenCodeProvider(binary_name=f"opencode:{MODEL}")
    lines = [_step_finish({"total": 10_000}), _step_finish({"total": 12_500})]
    with patch("providers.opencode_provider.opencode_context_window", return_value=200_000):
        _run(p, lines)
    assert p.context_reading["used"] == 12_500


@pytest.mark.parametrize("tokens", [
    {}, {"input": 5}, {"total": "17325"}, {"total": True}, {"total": 0}, {"total": -4},
    {"total": 1.5}, None, "text",
])
def test_malformed_tokens_leave_the_reading_none(tokens):
    p = OpenCodeProvider(binary_name=f"opencode:{MODEL}")
    with patch("providers.opencode_provider.opencode_context_window", return_value=200_000):
        _run(p, [_step_finish(tokens)])
    assert p.context_reading is None


def test_event_without_part_leaves_the_reading_none():
    p = OpenCodeProvider(binary_name=f"opencode:{MODEL}")
    with patch("providers.opencode_provider.opencode_context_window", return_value=200_000):
        _run(p, [json.dumps({"type": "step_finish", "sessionID": "ses_x"})])
    assert p.context_reading is None


def test_unknown_window_leaves_the_reading_none():
    p = OpenCodeProvider(binary_name=f"opencode:{MODEL}")
    with patch("providers.opencode_provider.opencode_context_window", return_value=None):
        _run(p, _fixture_lines())
    assert p.context_reading is None


def test_model_without_provider_prefix_never_looks_up_a_window():
    p = OpenCodeProvider(binary_name="opencode:mystery")
    with patch("providers.opencode_provider.opencode_context_window") as lookup:
        _run(p, _fixture_lines())
    lookup.assert_not_called()
    assert p.context_reading is None


def test_catalog_parser_reads_window_from_verbose_output():
    text = (FIXTURES / "models_verbose.txt").read_text(encoding="utf-8")
    expected = {"opencode/big-pickle": 200_000, "opencode/exo-free": 1_048_576}
    assert oneshot.parse_opencode_windows(text) == expected
    assert oneshot.parse_opencode_windows(text.replace("\n", "\r\n")) == expected
    assert oneshot.parse_opencode_windows("not a catalog") == {}


def test_window_lookup_reuses_hits_and_never_guesses():
    text = (FIXTURES / "models_verbose.txt").read_bytes()
    oneshot._OPENCODE_WINDOWS.clear()
    done = MagicMock(stdout=text)
    with patch.object(oneshot, "resolve_opencode_cmd", return_value=["opencode"]), \
         patch.object(oneshot.subprocess, "run", return_value=done) as run:
        assert oneshot.opencode_context_window(MODEL) == 200_000
        assert oneshot.opencode_context_window(MODEL) == 200_000
        assert oneshot.opencode_context_window("opencode/missing") is None
        assert oneshot.opencode_context_window("noprovider") is None
    assert run.call_args_list[0].args[0] == ["opencode", "models", "opencode", "--verbose"]
    assert run.call_count == 2  # the hit is cached, the miss is looked up again
    oneshot._OPENCODE_WINDOWS.clear()
    with patch.object(oneshot, "resolve_opencode_cmd", return_value=["opencode"]), \
         patch.object(oneshot.subprocess, "run", side_effect=OSError("gone")):
        assert oneshot.opencode_context_window(MODEL) is None


def test_gauge_reads_only_the_current_oneshot_cli_session():
    conv_id = 987_001
    reading = {"used": 17_325, "window": 200_000, "percent": 8.66, "model": MODEL}
    sess = oneshot.get_session("opencode", conv_id)
    try:
        assert _live_context_reading(conv_id, "opencode") is None
        sess.context_reading, sess.context_reading_at = reading, 1.0
        assert _live_context_reading(conv_id, "opencode") == reading
        # The chat moved to another one-shot CLI: OpenCode's reading must not leak.
        for family in ("copilot", "cursor", "kimi", "agy", None):
            assert _live_context_reading(conv_id, family) is None
        assert oneshot.peek_session(conv_id, "copilot") is None
        db = MagicMock()
        db.get_conversation_messages.return_value = [{"content": "x"}]
        payload = _context_usage_payload(db, conv_id, reading=_live_context_reading(conv_id, "copilot"))
        assert payload["estimated"] is True
    finally:
        oneshot._SESSIONS.pop(("opencode", conv_id), None)


def test_estimate_is_unchanged_for_clis_that_report_no_reading():
    db = MagicMock()
    db.get_conversation_messages.return_value = [{"content": "x" * 1000}]
    # Copilot's real stream carries only outputTokens and premium-request counters.
    p = CopilotProvider(binary_name="copilot-auto")
    lines = [
        json.dumps({"type": "assistant.message", "data": {"messageId": "m", "content": "ok",
                                                         "outputTokens": 53}}),
        json.dumps({"type": "result", "sessionId": "s", "exitCode": 0,
                    "usage": {"premiumRequests": 0.33, "totalApiDurationMs": 1420}}),
    ]
    _run(p, lines)
    assert p.context_reading is None
    payload = _context_usage_payload(db, 5, reading=p.context_reading)
    assert payload["estimated"] is True and "real" not in payload
    assert payload["percent"] == _context_usage_payload(db, 5)["percent"]
