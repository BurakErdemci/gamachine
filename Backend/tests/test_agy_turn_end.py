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
import threading
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


def answer(index, text):
    return step(index, step_type="agent_response", text_delta=text)


def success(response, num_turns=1):
    result = copy.deepcopy(TURNS[0][-1])
    result["result"].update(response=response, num_turns=num_turns)
    return result


class _Harness(base.TestAgyStreamSession):
    """The stream-session fixture without re-running its tests here."""

    stderr_by_spawn = None

    async def asyncSetUp(self):
        await super().asyncSetUp()
        getattr(agy_session, "_QUOTA_FAILURES", {}).clear()

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
        # The owner's three copies: the result error and twice in stderr.
        # Quota text streamed as answer text before the failure would show
        # (nothing is recorded yet; see TestReplayOfRecordedFailure).
        self.stderr_by_spawn = [(QUOTA_TEXT + "\n" + QUOTA_TEXT + "\n").encode()]
        self.plans = [{"turns": [[TURNS[0][0], failed_result(QUOTA_TEXT)]]}]
        with patch.object(agy_session, "datetime", create=True) as clock:
            clock.now.return_value = datetime(2026, 11, 12, 20, 0)
            return await asyncio.wait_for(
                self.collect(model="agy-claude-sonnet-5-5", language=language), 5)

    async def test_one_clean_line_with_reset_and_hint(self):
        events = await self.run_quota_turn("en")
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1, events)
        self.assertNotIn("quota", json.dumps(events[:-1]).lower())
        message = errors[0]["message"]
        self.assertEqual(errors[0]["code"], "agy_quota")
        self.assertNotIn("AGY_ERROR", message)
        self.assertNotIn("{", message)
        self.assertNotIn("RESOURCE_EXHAUSTED", message)
        self.assertEqual(message.count("quota"), 1, message)
        self.assertIn("individual Antigravity quota for agy-claude-sonnet-5-5", message)
        self.assertIn("resets in 4 d 23 h", message)
        self.assertIn("Nov 17, 19:46", message)
        self.assertIn("another model", message)

    async def test_quota_line_is_localized(self):
        message = (await self.run_quota_turn("tr"))[-1]["message"]
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
        # No quota failure was recorded: the answer text streams as it came.
        self.assertEqual([e["type"] for e in events], ["text", "error"], events)
        self.assertEqual(events[0]["content"], QUOTA_TEXT)
        self.assertNotIn("code", events[1])
        self.assertIn("ENOSPC", events[1]["message"])

    async def test_side_turn_errors_are_as_before(self):
        # Side turns get the coded quota message (raw agy text showed before
        # 4.0.1), still without a step summary or git status.
        self.plans = [{"turns": [[TURNS[0][0], tool(1, "view_file"),
                                  failed_result("Individual quota reached. Resets in 1h.")]]}]
        with patch.object(agy_session, "workspace_state", create=True,
                          side_effect=AssertionError("git status on a side turn")), \
                patch.object(agy_session, "changed_files",
                             side_effect=AssertionError("git status on a side turn")):
            events = await asyncio.wait_for(self.collect(
                agy_session.get_session(-5), side=True, language="en"), 5)
        self.assertEqual(events[-1]["code"], "agy_quota")
        self.assertNotIn("Individual quota reached", events[-1]["message"])
        self.assertNotIn("changed_files", events[-1])


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
        self.plans = [{"turns": [[TURNS[0][0], failed_result(QUOTA_TEXT)]]},
                      {"turns": [[TURNS[0][0], replayed, tool(2, "write_to_file"), tool_only],
                                 [answer_only]]}]
        failed = await asyncio.wait_for(self.collect(), 5)
        self.assertEqual(failed[-1]["code"], "agy_quota")
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
        self.plans = [{"turns": [[TURNS[0][0], failed_result("".join(fragments))]]},
                      {"turns": [[TURNS[0][0],
                                  step(1, step_type="agent_response", text_delta=fragments[0]),
                                  step(1, step_type="agent_response", text_delta=fragments[1]),
                                  step(2, step_type="agent_response", text_delta="Fresh answer."),
                                  success]]}]
        failed = await asyncio.wait_for(self.collect(), 5)
        self.assertEqual(failed[-1]["code"], "agy_quota")
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


class TestReplayOfRecordedFailure(_Harness):
    """Audit 170ccc19: pattern rules dropped real answers. Only an exact
    replay of a quota failure this conversation received is removed."""

    def text_of(self, events):
        streamed = "".join(e["content"] for e in events if e["type"] == "text")
        stored = next(e["content"] for e in events if e["type"] == "response")
        return streamed, stored

    async def turn(self, *events, response=None):
        """One successful turn on conversation 11 with these child events."""
        self.plans.append({"turns": [[TURNS[0][0], *events, success(
            "".join(e["step_update"]["text_delta"] for e in events
                    if e["step_update"].get("step_type") == "agent_response")
            if response is None else response)]]})
        done = await asyncio.wait_for(self.collect(agy_session.get_session(11)), 5)
        self.assertEqual(done[-1]["type"], "done", done)
        # A turn per process, so the next turn's plan is a fresh spawn.
        await agy_session.get_session(11).close(preserve_resume=True)
        return done

    async def fail_with_quota(self, failure=QUOTA_TEXT):
        self.plans.append({"turns": [[TURNS[0][0], failed_result(failure)]]})
        failed = await asyncio.wait_for(
            self.collect(agy_session.get_session(11), language="en"), 5)
        self.assertEqual(failed[-1].get("code"), "agy_quota", failed)

    async def test_answer_about_quota_with_a_reset_time_is_kept(self):
        # Blind audit test_A: the whole answer streamed '' and stored ''.
        text = ("RESOURCE_EXHAUSTED (code 429) means the API quota is gone; "
                "it resets in 1h. Back off and retry.")
        events = await self.turn(answer(1, text))
        self.assertEqual(self.text_of(events), (text, text))

    async def test_quota_text_streams_unchanged_when_no_failure_was_recorded(self):
        streamed_turn = await self.turn(answer(1, QUOTA_TEXT[:30]), answer(1, QUOTA_TEXT[30:]))
        self.assertEqual(self.text_of(streamed_turn), (QUOTA_TEXT, QUOTA_TEXT))
        response_only = await self.turn(tool(1, "view_file"), response=QUOTA_TEXT)
        self.assertEqual(self.text_of(response_only), ("", QUOTA_TEXT))

    async def test_real_text_before_a_replay_in_one_delta_is_kept(self):
        # Blind audit C2: "Sure, here is the fix." was lost with the replay.
        await self.fail_with_quota()
        events = await self.turn(answer(1, "Sure, here is the fix.\n" + QUOTA_TEXT))
        expected = "Sure, here is the fix.\n"
        self.assertEqual(self.text_of(events), (expected, expected))
        self.assertNotIn("quota", json.dumps(events).lower())

    async def test_real_text_after_a_replay_on_the_same_line_is_kept(self):
        # Codex audit replay_answer_loss: streamed '' and stored ''.
        await self.fail_with_quota()
        events = await self.turn(answer(1, QUOTA_TEXT + "Fixed the scene."))
        self.assertEqual(self.text_of(events), ("Fixed the scene.", "Fixed the scene."))
        tool_only = await self.turn(tool(1, "write_to_file"),
                                    response=QUOTA_TEXT + "Fixed the scene.")
        self.assertEqual(self.text_of(tool_only), ("", "Fixed the scene."))

    async def test_replay_split_at_every_boundary_keeps_the_text_around_it(self):
        # Codex replay_after_answer, blind replay_fuzz: 155 of 155 splits lost text.
        await self.fail_with_quota()
        whole = "Done.\n" + QUOTA_TEXT + "\nFixed."
        events = []
        for cut in range(1, len(whole)):
            events += [answer(cut, whole[:cut]), answer(cut, whole[cut:])]
        result = await self.turn(*events, response=whole)
        expected = "Done.\nFixed." * (len(whole) - 1)
        self.assertEqual(self.text_of(result), (expected, expected))

    async def test_a_different_quota_text_is_not_removed(self):
        await self.fail_with_quota()
        text = "Individual quota reached for the image API. Resets in 2h. Use the other key."
        events = await self.turn(answer(1, text))
        self.assertEqual(self.text_of(events), (text, text))

    async def test_record_ends_after_a_turn_without_the_replay(self):
        await self.fail_with_quota()
        await self.turn(answer(1, "All good."))
        events = await self.turn(answer(1, QUOTA_TEXT))
        self.assertEqual(self.text_of(events), (QUOTA_TEXT, QUOTA_TEXT))

    async def test_record_ends_after_two_turns(self):
        await self.fail_with_quota()
        for _ in range(2):
            replayed = await self.turn(answer(1, QUOTA_TEXT), answer(2, "Next."))
            self.assertEqual(self.text_of(replayed), ("Next.", "Next."))
        third = await self.turn(answer(1, QUOTA_TEXT), answer(2, "Next."))
        self.assertEqual(self.text_of(third), (QUOTA_TEXT + "Next.",) * 2)


class TestLongestReplayAndRecords(_Harness):
    """Blind audit 1a139d3d: nested recorded texts, a held prefix at a quota
    end, and the record's key, scope and lifetime."""

    text_of = TestReplayOfRecordedFailure.text_of
    turn = TestReplayOfRecordedFailure.turn
    fail_with_quota = TestReplayOfRecordedFailure.fail_with_quota
    SHORT = "Individual quota reached for Claude Sonnet 5.5. Resets in 119h46m43s."

    def streamed(self, events):
        return "".join(e["content"] for e in events if e["type"] == "text")

    def feed_all(self, texts, deltas):
        replay = agy_session._ReplayFilter(texts)
        out = [replay.feed(1, delta) for delta in deltas]
        return "".join(out) + replay.end()

    async def failing_turn(self, *events):
        self.plans.append({"turns": [[TURNS[0][0], *events]]})
        done = await asyncio.wait_for(
            self.collect(agy_session.get_session(11), language="en"), 5)
        await agy_session.get_session(11).close(preserve_resume=True)
        return done

    def test_nested_recorded_texts_are_removed_whole_at_every_split(self):
        # Fuzz C1: the shorter text was removed as soon as it completed and
        # the rest of the longer one (the AGY_ERROR json) streamed.
        longer = QUOTA_TEXT
        for texts in ([self.SHORT, longer], [longer, self.SHORT]):
            for cut in range(1, len(longer)):
                self.assertEqual(self.feed_all(texts, [longer[:cut], longer[cut:]]), "", cut)
            for first in range(1, len(longer) - 1, 7):
                for second in range(first + 1, len(longer), 11):
                    deltas = [longer[:first], longer[first:second], longer[second:]]
                    self.assertEqual(self.feed_all(texts, deltas), "", (first, second))
            # The shorter text alone, then a diverging continuation, falls back.
            self.assertEqual(self.feed_all(texts, [self.SHORT, " Next."]), "Next.")
            self.assertEqual(self.feed_all(texts, [self.SHORT]), "")
            self.assertEqual(self.feed_all(texts, [self.SHORT + "\nSo it goes."]), "So it goes.")

    async def test_nested_step_text_longer_than_the_error_text(self):
        await self.failing_turn(answer(1, QUOTA_TEXT), failed_result(self.SHORT))
        events = await self.turn(answer(1, self.SHORT), answer(1, QUOTA_TEXT[len(self.SHORT):]))
        self.assertEqual(self.text_of(events)[0], "")

    async def test_held_prefix_is_dropped_when_the_turn_ends_in_a_quota_result(self):
        await self.fail_with_quota()
        events = await self.failing_turn(answer(1, QUOTA_TEXT[:25]), failed_result(QUOTA_TEXT))
        self.assertEqual(self.streamed(events), "")
        self.assertEqual(events[-1].get("code"), "agy_quota", events)

    async def test_held_prefix_is_dropped_when_the_turn_ends_in_a_quota_error_event(self):
        await self.fail_with_quota()
        events = await self.failing_turn(answer(1, QUOTA_TEXT[:25]),
                                         {"event": "error", "error": QUOTA_TEXT})
        self.assertEqual(self.streamed(events), "")
        self.assertEqual(events[-1].get("code"), "agy_quota", events)

    async def test_held_prefix_streams_when_the_turn_fails_otherwise(self):
        await self.fail_with_quota()
        events = await self.failing_turn(answer(1, QUOTA_TEXT[:25]), failed_result("ENOSPC full"))
        self.assertEqual(self.streamed(events), QUOTA_TEXT[:25])

    async def test_record_is_keyed_by_conversation_and_cwd(self):
        await self.fail_with_quota()
        key = (11, os.path.abspath(agy_session.get_session(11).cwd))
        self.assertEqual(list(agy_session._QUOTA_FAILURES), [key])
        other = make_repo(self)
        self.plans.append({"turns": [[TURNS[0][0], answer(1, QUOTA_TEXT), success(QUOTA_TEXT)]]})
        events = await asyncio.wait_for(self.collect(
            agy_session.get_session(11), cwd=other, language="en"), 5)
        self.assertEqual(self.streamed(events), QUOTA_TEXT)
        self.assertEqual(list(agy_session._QUOTA_FAILURES), [key])

    async def test_a_side_turn_is_never_filtered(self):
        await self.fail_with_quota()
        self.plans.append({"turns": [[TURNS[0][0], answer(1, QUOTA_TEXT), success(QUOTA_TEXT)]]})
        events = await asyncio.wait_for(self.collect(
            agy_session.get_session(11), side=True, language="en"), 5)
        self.assertEqual(self.streamed(events), QUOTA_TEXT)

    async def test_a_one_shot_session_never_records(self):
        self.plans.append({"turns": [[TURNS[0][0], failed_result(QUOTA_TEXT)]]})
        events = await asyncio.wait_for(self.collect(
            agy_session.get_session(-7), language="en"), 5)
        self.assertEqual(events[-1].get("code"), "agy_quota", events)
        self.assertEqual(agy_session._QUOTA_FAILURES, {})

    async def test_close_session_clears_the_record(self):
        await self.fail_with_quota()
        self.assertTrue(agy_session._QUOTA_FAILURES)
        await agy_session.close_session(11)
        self.assertEqual(agy_session._QUOTA_FAILURES, {})

    async def test_a_failure_text_under_the_minimum_is_not_recorded(self):
        short = "quota reached"
        self.assertLess(len(short), agy_session._REPLAY_MIN_CHARS)
        await self.fail_with_quota(short)
        self.assertEqual(agy_session._QUOTA_FAILURES, {})
        events = await self.turn(answer(1, "The " + short + " page."))
        self.assertEqual(self.text_of(events), ("The " + short + " page.",) * 2)


class TestSnapshotAndFiles(_Harness):
    async def test_prompt_waits_for_a_slow_snapshot_only_up_to_the_bound(self):
        # Blind audit T1: with no bound a slow `git status` held the prompt.
        release = threading.Event()
        self.addCleanup(release.set)
        self.plans = [{"turns": [TURNS[0]]}]
        with patch.object(agy_session, "_SNAPSHOT_WAIT_S", 0.2), \
                patch.object(agy_session, "workspace_state",
                             side_effect=lambda cwd: release.wait(10) and {}):
            started = time.monotonic()
            task = asyncio.create_task(self.collect())
            while not self.processes:
                await asyncio.sleep(0.01)
            await asyncio.wait_for(self.processes[0].written.wait(), 3)
            waited = time.monotonic() - started
            events = await asyncio.wait_for(task, 3)
        release.set()
        self.assertLess(waited, 2.0)
        self.assertEqual(events[-1]["type"], "done")

    async def test_files_the_turn_reverted_committed_or_deleted_are_listed(self):
        # Blind audit R2 and Codex deleted_dirty_file: only paths still dirty
        # at the end were compared, so these read as "no new changes".
        repo = make_repo(self)

        def git(*args):
            subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)

        git("config", "user.email", "test@example.invalid")
        git("config", "user.name", "test")
        for name in ("reverted.cs", "committed.cs"):
            write(os.path.join(repo, name), "v1")
        git("add", ".")
        git("commit", "-qm", "base")
        for name in ("reverted.cs", "committed.cs", "untracked.cs"):
            write(os.path.join(repo, name), "user edit")

        def turn_writes():
            git("checkout", "--", "reverted.cs")
            git("commit", "-qm", "turn", "committed.cs")
            os.remove(os.path.join(repo, "untracked.cs"))

        self.plans = [{"turns": [[TURNS[0][0], tool(1, "run_command")]]}]
        with patch.object(BaseCLIProvider, "_AGY_IDLE_LIMIT", 0.3, create=True):
            task = asyncio.create_task(self.collect(cwd=repo, language="en"))
            while not self.processes:
                await asyncio.sleep(0)
            await self.processes[-1].written.wait()
            turn_writes()
            events = await asyncio.wait_for(task, 5)
        error = events[-1]
        self.assertEqual(error["code"], "agy_turn_limit")
        self.assertEqual(sorted(error.get("changed_files", [])),
                         ["committed.cs", "reverted.cs", "untracked.cs"])
        self.assertNotIn("no new uncommitted changes", error["message"])


if __name__ == "__main__":
    unittest.main()
