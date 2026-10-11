"""The stored agy answer keeps the paragraph breaks the user saw while streaming.

The CLI's own `result.response` glues separate text blocks ("A.B."); the live
text deltas get block_break separators, and the stored answer must match them.
"""
import asyncio
import unittest
from unittest.mock import patch

from providers import agy_session
from providers.agy_provider import AgyProvider
from providers.cli_base import BaseCLIProvider
from tests.test_agy_stream_session import SESSION_ID, FakeProcess


def _text(index, delta):
    return {"event": "step_update", "step_update": {
        "conversation_id": SESSION_ID, "step_index": index, "state": "DONE",
        "step_type": "agent_response", "text_delta": delta}}


def _tool(index):
    return {"event": "step_update", "step_update": {
        "conversation_id": SESSION_ID, "step_index": index, "state": "DONE",
        "step_type": "tool", "tool_info": {"name": "view_file", "parameters": {"path": "a.cs"},
                                           "output": "file contents"}}}


def _result(response):
    return {"event": "result", "result": {
        "conversation_id": SESSION_ID, "status": "SUCCESS", "response": response,
        "num_turns": 1, "usage": {}}}


class TestAgyStoredAnswer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        agy_session._SESSIONS.clear()
        agy_session._RESUME_IDS.clear()
        self.plans = []
        self.patches = [
            patch.object(BaseCLIProvider, "_AGY_LOCK", asyncio.Lock()),
            patch.object(AgyProvider, "_agy_binary", return_value="fake-agy"),
            patch.object(AgyProvider, "_resolve_exec", side_effect=lambda command: command),
            patch.object(AgyProvider, "_write_mcp_config", return_value=""),
            patch.object(AgyProvider, "_register_agy_workspace"),
            patch.object(AgyProvider, "_write_step_gate", return_value=True),
            patch("providers.agy_provider.write_gate_state"),
            patch.object(AgyProvider, "_stream_instructions", return_value=""),
            patch.object(agy_session, "_global_auto_mode", return_value=True),
            patch.object(agy_session.asyncio, "create_subprocess_exec", side_effect=self.spawn),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        await agy_session.close_all_sessions()
        for item in reversed(self.patches):
            item.stop()

    async def spawn(self, *argv, **kwargs):
        return FakeProcess(**self.plans.pop(0))

    async def collect(self):
        session = agy_session.get_session(11)
        return [event async for event in session.stream("hello")]

    async def test_blocks_around_a_tool_are_stored_with_the_paragraph_break(self):
        turn = [_text(1, "Reading the doc."), _tool(2), _text(3, "The repo is open."),
                _result("Reading the doc.The repo is open.")]
        self.plans = [{"turns": [turn]}]
        events = await self.collect()
        streamed = "".join(e["content"] for e in events if e["type"] == "text")
        response = next(e for e in events if e["type"] == "response")
        self.assertEqual(response["content"], "Reading the doc.\n\nThe repo is open.")
        self.assertEqual(response["content"], streamed)

    async def test_an_empty_stream_falls_back_to_the_result_response(self):
        self.plans = [{"turns": [[_result("Only the result.")]]}]
        events = await self.collect()
        self.assertFalse([e for e in events if e["type"] == "text"])
        response = next(e for e in events if e["type"] == "response")
        self.assertEqual(response["content"], "Only the result.")

    async def test_a_whitespace_only_stream_falls_back_to_the_result_response(self):
        self.plans = [{"turns": [[_text(1, "\n"), _result("Real answer.")]]}]
        events = await self.collect()
        response = next(e for e in events if e["type"] == "response")
        self.assertEqual(response["content"], "Real answer.")


if __name__ == "__main__":
    unittest.main()
