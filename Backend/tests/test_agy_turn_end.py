"""How an agy turn that does not finish normally ends in the chat.

Owner's findings, 12 Nov 2026 (Gamachine 4.0.0): a turn cut at 1800 s left
only "agy turn timed out after 1800 seconds." while it had been streaming
steps the whole time; a quota failure printed the same text three times plus
raw AGY_ERROR JSON; and after switching model, a SUCCESSFUL turn showed the
old quota line again under its answer. The fake child below replays agy's
stream-json shape; no agy runs.
"""
import asyncio
import copy
import json
import os
import subprocess
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from providers import agy_session
from providers.cli_base import BaseCLIProvider
from tests.test_agy_stream_session import SESSION_ID, TURNS, FakeProcess
from tests import test_agy_stream_session as base

QUOTA_TEXT = ("Individual quota reached for Claude Sonnet 5.5. Resets in 119h46m43s.\n"
              'AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429) quota",'
              '"retryable":true,"error_id":"e-1"}')


def step(index, **fields):
    return {"event": "step_update", "step_update": {
        "conversation_id": SESSION_ID, "step_index": index, "state": "DONE", **fields}}


def tool(index, name, **info):
    return step(index, step_type="tool", tool_info={"name": name, "parameters": {}, **info})


def failed_result(error, num_turns=1):
    result = copy.deepcopy(TURNS[0][-1])
    result["result"].update(status="ERROR", error=error, num_turns=num_turns)
    result["result"].pop("response", None)
    return result


class _Harness(base.TestAgyStreamSession):
    """The stream-session fixture without re-running its tests here."""

    stderr_by_spawn = None

    async def spawn(self, *argv, **kwargs):
        process = await super().spawn(*argv, **kwargs)
        if self.stderr_by_spawn:
            process.stderr.feed_data(self.stderr_by_spawn.pop(0))
        return process


for _name in [n for n in dir(base.TestAgyStreamSession) if n.startswith("test_")]:
    setattr(_Harness, _name, None)


class TestTimeLimit(_Harness):
    async def test_silent_child_is_cut_with_steps_files_and_continue_hint(self):
        repo = self.make_repo()
        # Pre-existing dirty file the turn never touched: not this turn's.
        old = os.path.join(repo, "old_notes.txt")
        with open(old, "w") as handle:
            handle.write("x")
        past = time.time() - 3600
        os.utime(old, (past, past))
        with open(os.path.join(repo, "BossBot.cs"), "w") as handle:
            handle.write("class BossBot {}")
        silent_turn = [TURNS[0][0], tool(1, "view_file"), tool(2, "view_file"),
                       tool(3, "run_command", error="exit 1")]
        self.plans = [{"turns": [silent_turn]}]
        with patch.object(BaseCLIProvider, "_AGY_IDLE_LIMIT", 0.2, create=True):
            events = await asyncio.wait_for(
                self.collect(cwd=repo, language="en"), 5)
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1, events)
        error = errors[0]
        self.assertEqual(error["code"], "agy_turn_limit")
        self.assertEqual(error["limit"], "idle")
        message = error["message"]
        self.assertIn("produced no output", message)
        self.assertIn("3 steps", message)
        self.assertIn("1 failed", message)
        self.assertIn("`view_file` ×2", message)
        self.assertIn("BossBot.cs", message)
        self.assertNotIn("old_notes.txt", message)
        self.assertEqual(error["changed_files"], ["BossBot.cs"])
        self.assertIn('send "continue"', message)
        self.assertTrue(self.processes[0].killed)

    async def test_ceiling_measures_silence_not_length(self):
        # Steps keep coming for longer than the idle limit in total: the turn
        # must finish, as the two measured 1800 s turns would have.
        self.plans = [{"turns": [[TURNS[0][0]]]}]
        with patch.object(BaseCLIProvider, "_AGY_IDLE_LIMIT", 1.0, create=True):
            task = asyncio.create_task(self.collect())
            while not self.processes:
                await asyncio.sleep(0)
            process = self.processes[0]
            await process.written.wait()
            # 10x margin between a gap and the limit: a loaded machine once
            # stretched a 0.1 s gap past a 0.3 s limit.
            for index in range(1, 26):
                await asyncio.sleep(0.1)
                process.stdout.feed_data((json.dumps(tool(index, "play_step")) + "\n").encode())
            process.stdout.feed_data((json.dumps(TURNS[0][-1]) + "\n").encode())
            events = await asyncio.wait_for(task, 5)
        self.assertEqual(events[-1]["type"], "done", events[-3:])
        # The flat 30-minute cut is gone; the total cap is only a runaway guard.
        self.assertGreater(BaseCLIProvider._AGY_MAX_TOTAL, 1800)

    async def test_limit_message_is_localized(self):
        self.plans = [{"turns": [[TURNS[0][0], tool(1, "view_file")]]},
                      {"turns": [[TURNS[0][0], tool(1, "view_file")]]}]
        with patch.object(BaseCLIProvider, "_AGY_IDLE_LIMIT", 0.1, create=True):
            tr = await asyncio.wait_for(self.collect(language="tr"), 5)
            en = await asyncio.wait_for(self.collect(language="en"), 5)
        self.assertIn("devam et", tr[-1]["message"])
        self.assertIn("continue", en[-1]["message"])
        self.assertNotEqual(tr[-1]["message"], en[-1]["message"])

    def make_repo(self):
        import tempfile
        import shutil
        repo = tempfile.mkdtemp(prefix="agy-turn-end-")
        self.addCleanup(shutil.rmtree, repo, True)
        subprocess.run(["git", "init", "-q", repo], check=True)
        return repo


class TestQuota(_Harness):
    async def run_quota_turn(self, language):
        self.stderr_by_spawn = [(QUOTA_TEXT + "\n" + QUOTA_TEXT + "\n").encode()]
        self.plans = [{"turns": [[TURNS[0][0], step(1, step_type="agent_response",
                                                      text_delta=QUOTA_TEXT),
                                  failed_result(QUOTA_TEXT)]]}]
        with patch.object(agy_session, "datetime", create=True) as clock:
            clock.now.return_value = datetime(2026, 11, 12, 20, 0)
            return await asyncio.wait_for(
                self.collect(model="agy-claude-sonnet-5-5", language=language), 5)

    async def test_one_clean_line_with_reset_and_hint(self):
        events = await self.run_quota_turn("en")
        self.assertEqual([e["type"] for e in events], ["error"], events)
        message = events[0]["message"]
        self.assertEqual(events[0]["code"], "agy_quota")
        self.assertNotIn("AGY_ERROR", message)
        self.assertNotIn("{", message)
        self.assertNotIn("RESOURCE_EXHAUSTED", message)
        self.assertEqual(message.count("quota"), 1, message)
        self.assertIn("individual Antigravity quota for agy-claude-sonnet-5-5", message)
        self.assertIn("resets in 4 d 23 h", message)
        self.assertIn("Nov 17, 19:46", message)
        self.assertIn("another model", message)

    async def test_quota_line_is_localized(self):
        message = (await self.run_quota_turn("tr"))[0]["message"]
        self.assertIn("bireysel Antigravity kotası doldu", message)
        self.assertIn("4 gün 23 sa sonra sıfırlanıyor (17.11 19:46 civarı)", message)
        self.assertIn("başka bir model", message)


class TestNoStaleQuotaLine(_Harness):
    async def test_quota_text_replayed_on_resume_never_reaches_a_successful_answer(self):
        replayed = step(1, step_type="agent_response", text_delta=QUOTA_TEXT)
        answer = step(3, step_type="agent_response", text_delta="Combo fixed.\n")
        success = copy.deepcopy(TURNS[0][-1])
        success["result"]["response"] = QUOTA_TEXT + "\nCombo fixed.\n"
        self.plans = [{"turns": [[TURNS[0][0], replayed, failed_result(QUOTA_TEXT)]]},
                      {"turns": [[TURNS[0][0], replayed, answer, success]]}]
        session = agy_session.get_session(11)
        first = await asyncio.wait_for(self.collect(session, model="agy-claude-sonnet-5-5"), 5)
        self.assertEqual(first[-1]["type"], "error")
        second = await asyncio.wait_for(
            self.collect(agy_session.get_session(11), model="gemini-4-argon-medium"), 5)
        self.assertEqual(second[-1]["type"], "done")
        shown = json.dumps(second)
        self.assertNotIn("quota", shown.lower())
        self.assertNotIn("Resets in", shown)
        response = next(e for e in second if e["type"] == "response")
        self.assertEqual(response["content"], "Combo fixed.\n")


if __name__ == "__main__":
    unittest.main()
