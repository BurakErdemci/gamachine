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
import shutil
import subprocess
import tempfile
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


def write(path, text):
    with open(path, "w") as handle:
        handle.write(text)


def make_repo(test):
    repo = tempfile.mkdtemp(prefix="agy-turn-end-")
    test.addCleanup(shutil.rmtree, repo, True)
    subprocess.run(["git", "init", "-q", repo], check=True)
    return repo


class TestTimeLimit(_Harness):
    async def collect_while_writing(self, write_files, **kwargs):
        """Run a turn and write files once the child has the prompt: the
        writes the turn itself makes."""
        task = asyncio.create_task(self.collect(**kwargs))
        while not self.processes:
            await asyncio.sleep(0)
        await self.processes[-1].written.wait()
        write_files()
        return await asyncio.wait_for(task, 5)

    async def test_silent_child_is_cut_with_steps_files_and_continue_hint(self):
        repo = make_repo(self)
        # Pre-existing dirty files: one the turn never touches, one it rewrites.
        old = os.path.join(repo, "old_notes.txt")
        rewritten = os.path.join(repo, "Combo.cs")
        past = time.time() - 3600
        for path in (old, rewritten):
            write(path, "x")
            os.utime(path, (past, past))

        def turn_writes():
            write(os.path.join(repo, "BossBot.cs"), "class BossBot {}")
            write(rewritten, "class Combo {}")

        silent_turn = [TURNS[0][0], tool(1, "view_file"), tool(2, "view_file"),
                       tool(3, "run_command", error="exit 1")]
        self.plans = [{"turns": [silent_turn]}]
        with patch.object(BaseCLIProvider, "_AGY_IDLE_LIMIT", 0.3, create=True):
            events = await self.collect_while_writing(turn_writes, cwd=repo, language="en")
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
        self.assertEqual(sorted(error["changed_files"]), ["BossBot.cs", "Combo.cs"])
        self.assertIn('send "continue"', message)
        self.assertTrue(self.processes[0].killed)

    async def test_changes_made_before_the_turn_are_not_listed(self):
        # Audit 9ee428c1: a deletion and a fresh user edit that predate the
        # turn were listed as "Files this turn changed" for a turn that wrote
        # nothing.
        repo = make_repo(self)
        deleted = os.path.join(repo, "deleted-before-turn.cs")
        write(deleted, "staged")
        subprocess.run(["git", "-C", repo, "add", "deleted-before-turn.cs"], check=True)
        os.remove(deleted)
        write(os.path.join(repo, "edited-before-turn.cs"), "user edit")
        self.plans = [{"turns": [[TURNS[0][0], tool(1, "view_file")]]}]
        with patch.object(BaseCLIProvider, "_AGY_IDLE_LIMIT", 0.2, create=True):
            events = await asyncio.wait_for(self.collect(cwd=repo, language="en"), 5)
        error = events[-1]
        self.assertEqual(error["code"], "agy_turn_limit")
        self.assertNotIn("changed_files", error)
        self.assertNotIn("before-turn", error["message"])
        self.assertIn("no new uncommitted changes", error["message"])

    async def test_changed_files_never_block_the_event_loop(self):
        # A slow `git status` (big Unity project) runs in a worker thread.
        repo = make_repo(self)
        real_git = agy_session._git

        def slow_git(*args):
            time.sleep(0.3)
            return real_git(*args)

        ticks = []

        async def ticker():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        self.plans = [{"turns": [[TURNS[0][0], tool(1, "view_file")]]}]
        heartbeat = asyncio.create_task(ticker())
        try:
            with patch.object(BaseCLIProvider, "_AGY_IDLE_LIMIT", 0.2, create=True), \
                    patch.object(agy_session, "_git", side_effect=slow_git):
                events = await asyncio.wait_for(self.collect(cwd=repo), 10)
            # One more beat, so a stall at the very end of the turn shows too.
            await asyncio.sleep(0.05)
        finally:
            heartbeat.cancel()
        self.assertEqual(events[-1]["code"], "agy_turn_limit")
        self.assertLess(max(b - a for a, b in zip(ticks, ticks[1:])), 0.25)

    async def test_missing_git_or_no_repository_only_omits_the_file_list(self):
        outside = tempfile.mkdtemp(prefix="agy-no-repo-")
        self.addCleanup(shutil.rmtree, outside, True)
        repo = make_repo(self)
        self.plans = [{"turns": [[TURNS[0][0], tool(1, "view_file")]]},
                      {"turns": [[TURNS[0][0], tool(1, "view_file")]]}]
        with patch.object(BaseCLIProvider, "_AGY_IDLE_LIMIT", 0.2, create=True):
            no_repo = await asyncio.wait_for(self.collect(cwd=outside, language="en"), 5)
            with patch.object(agy_session.subprocess, "run",
                              side_effect=FileNotFoundError("git")):
                no_git = await asyncio.wait_for(
                    self.collect(agy_session.get_session(12), cwd=repo, language="en"), 5)
        for events in (no_repo, no_git):
            error = events[-1]
            self.assertEqual(error["code"], "agy_turn_limit")
            self.assertNotIn("changed_files", error)
            self.assertNotIn("uncommitted", error["message"])
            self.assertIn("`view_file` ×1", error["message"])

    async def test_conversation_is_kept_after_a_time_limit(self):
        self.plans = [{"turns": [[TURNS[0][0], tool(1, "view_file")]]}, {}]
        with patch.object(BaseCLIProvider, "_AGY_IDLE_LIMIT", 0.2, create=True):
            cut = await asyncio.wait_for(self.collect(), 5)
        self.assertEqual(cut[-1]["code"], "agy_turn_limit")
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())
        resumed = await asyncio.wait_for(self.collect(agy_session.get_session(11)), 5)
        self.assertEqual(resumed[-1]["type"], "done")
        argv = list(self.spawns[1][0])
        self.assertIn("--conversation", argv)
        self.assertEqual(argv[argv.index("--conversation") + 1], SESSION_ID)

    async def test_child_that_stops_reading_the_prompt_hits_the_idle_limit(self):
        # Audit 9ee428c1: a blocked stdin drain waited for the total cap.
        self.plans = [{"release": asyncio.Event(), "turns": []}]
        with patch.object(BaseCLIProvider, "_AGY_IDLE_LIMIT", 0.1, create=True), \
                patch.object(BaseCLIProvider, "_AGY_MAX_TOTAL", 2.0):
            events = await asyncio.wait_for(self.collect(language="en"), 5)
        self.assertTrue(self.processes[0].written.is_set())
        self.assertEqual([e["type"] for e in events], ["error"], events)
        self.assertEqual(events[0]["limit"], "idle")

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

    async def test_stderr_of_an_earlier_turn_never_makes_a_later_failure_quota(self):
        # Audit 9ee428c1: a 429 retry warning from turn 1 stayed in the kept
        # child's stderr tail, and an unrelated turn-2 failure read as quota.
        self.stderr_by_spawn = [b"warn: request failed (code 429), retrying. Resets in 1h.\n"]
        failure = copy.deepcopy(TURNS[1][-1])
        failure["result"].update(status="ERROR", error="ENOSPC: workspace disk is full")
        self.plans = [{"turns": [TURNS[0], [failure]]}]
        session = agy_session.get_session(11)
        first = await asyncio.wait_for(self.collect(session), 5)
        self.assertEqual(first[-1]["type"], "done")
        second = await asyncio.wait_for(self.collect(session, language="en"), 5)
        self.assertEqual(len(self.processes), 1)
        error = second[-1]
        self.assertEqual(error["type"], "error")
        self.assertNotIn("code", error)
        self.assertIn("ENOSPC: workspace disk is full", error["message"])
        self.assertNotIn("429", error["message"])

    async def test_quota_text_in_the_answer_alone_does_not_make_a_failure_quota(self):
        self.plans = [{"turns": [[TURNS[0][0], step(1, step_type="agent_response",
                                                      text_delta=QUOTA_TEXT),
                                  failed_result("ENOSPC: workspace disk is full")]]}]
        events = await asyncio.wait_for(self.collect(language="en"), 5)
        self.assertEqual([e["type"] for e in events], ["error"], events)
        self.assertNotIn("code", events[0])
        self.assertIn("ENOSPC", events[0]["message"])

    async def test_side_turn_errors_are_as_before(self):
        # Side turns keep their 83a7c6b6 error: the raw failure, no quota code,
        # no main-chat hint, no step summary, no git status.
        self.plans = [{"turns": [[TURNS[0][0], tool(1, "view_file"),
                                  failed_result("Individual quota reached. Resets in 1h.")]]}]
        with patch.object(agy_session, "workspace_state", create=True,
                          side_effect=AssertionError("git status on a side turn")), \
                patch.object(agy_session, "changed_files",
                             side_effect=AssertionError("git status on a side turn")):
            events = await asyncio.wait_for(self.collect(
                agy_session.get_session(-5), side=True, language="en"), 5)
        self.assertEqual(events[-1], {"type": "error",
                                      "message": "Individual quota reached. Resets in 1h."})


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

    async def test_replay_never_returns_through_the_result_response(self):
        # Audit 9ee428c1: with every text delta filtered, the stored answer
        # fell back to result.response and brought the quota text back.
        replayed = step(1, step_type="agent_response", text_delta=QUOTA_TEXT)
        tool_only = copy.deepcopy(TURNS[0][-1])
        tool_only["result"]["response"] = QUOTA_TEXT + "\nWrote BossBot.cs.\n"
        answer_only = copy.deepcopy(TURNS[1][-1])
        answer_only["result"]["response"] = QUOTA_TEXT + "\nFixed.\n"
        self.plans = [{"turns": [[TURNS[0][0], replayed, tool(2, "write_to_file"), tool_only],
                                 [answer_only]]}]
        session = agy_session.get_session(11)
        for expected in ("Wrote BossBot.cs.\n", "Fixed.\n"):
            events = await asyncio.wait_for(self.collect(session), 5)
            self.assertEqual(events[-1]["type"], "done")
            self.assertNotIn("quota", json.dumps(events).lower())
            self.assertFalse([e for e in events if e["type"] == "text"])
            response = next(e for e in events if e["type"] == "response")
            self.assertEqual(response["content"], expected)

    async def test_replay_split_across_text_deltas_never_reaches_the_answer(self):
        fragments = ["Individual quota rea",
                     'ched. Resets in 1h. AGY_ERROR: {"short_error":"limit"}']
        success = copy.deepcopy(TURNS[0][-1])
        success["result"]["response"] = "".join(fragments) + "Fresh answer."
        self.plans = [{"turns": [[TURNS[0][0],
                                  step(1, step_type="agent_response", text_delta=fragments[0]),
                                  step(1, step_type="agent_response", text_delta=fragments[1]),
                                  step(2, step_type="agent_response", text_delta="Fresh answer."),
                                  success]]}]
        events = await asyncio.wait_for(self.collect(), 5)
        self.assertEqual(events[-1]["type"], "done")
        streamed = "".join(e["content"] for e in events if e["type"] == "text")
        stored = next(e["content"] for e in events if e["type"] == "response")
        self.assertEqual(streamed, "Fresh answer.")
        self.assertEqual(stored, "Fresh answer.")

    async def test_an_answer_about_quota_without_agy_markers_still_shows(self):
        # The model may explain a 429 or start its answer with "Quota": only
        # agy's own failure text (reset time or AGY_ERROR) is held back.
        deltas = ["Quota", " errors (code 429) from the store API are retried now.\n",
                  "Individual quota reached errors need a new key."]
        success = copy.deepcopy(TURNS[0][-1])
        success["result"]["response"] = "".join(deltas)
        self.plans = [{"turns": [[TURNS[0][0]]
                                 + [step(1, step_type="agent_response", text_delta=deltas[0]),
                                    step(1, step_type="agent_response", text_delta=deltas[1]),
                                    step(2, step_type="agent_response", text_delta=deltas[2]),
                                    success]]}]
        events = await asyncio.wait_for(self.collect(), 5)
        streamed = "".join(e["content"] for e in events if e["type"] == "text")
        self.assertEqual(streamed, "".join(deltas))
        self.assertEqual(next(e["content"] for e in events if e["type"] == "response"),
                         "".join(deltas))


if __name__ == "__main__":
    unittest.main()
