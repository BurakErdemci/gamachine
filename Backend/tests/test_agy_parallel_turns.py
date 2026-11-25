"""Different agy chats run their turns at the same time (24 Nov 2026).

Before, one machine-wide lock held every agy turn for its whole stream, so a
second chat sat on "Sırada" until the first one's turn ended. The model goes
per process (--model/--effort), so only the files written at spawn are shared;
the fake child below stands in for agy.
"""
import asyncio
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import agy_native_approval
import error_i18n
from providers import agy_session
from providers.agy_provider import AgyProvider
from providers.cli_base import BaseCLIProvider
from tests import test_agy_stream_session as base

_TURKISH_LETTERS = set("çğıöşüÇĞİÖŞÜ")


class _Harness(base.TestAgyStreamSession):
    """The stream-session fixture without re-running its tests here."""


for _name in [n for n in dir(base.TestAgyStreamSession) if n.startswith("test_")]:
    setattr(_Harness, _name, None)


class _ApprovalBackend:
    """A loopback stand-in for the backend's approval endpoint: records each
    card request and resolves it at once."""

    def __init__(self):
        self.bodies = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                owner.bodies.append(json.loads(self.rfile.read(length).decode("utf-8")))
                payload = json.dumps({"status": "resolved", "approved": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


class TestParallelTurns(_Harness):
    async def start_blocked_turn(self, conversation_id):
        release = asyncio.Event()
        self.plans.append({"release": release})
        task = asyncio.create_task(self.collect(agy_session.get_session(conversation_id)))
        return task, release

    async def test_two_chats_turns_overlap_in_time(self):
        first, release_first = await self.start_blocked_turn(11)
        await asyncio.wait_for(self._written(0), 1)
        second, release_second = await self.start_blocked_turn(12)
        # Chat 12's child gets its prompt while chat 11's turn is still
        # mid-stream: the turns overlap instead of queueing.
        await asyncio.wait_for(self._written(1), 1)
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        release_second.set()
        second_events = await asyncio.wait_for(second, 1)
        self.assertFalse(first.done())  # 12 finished while 11 still runs
        release_first.set()
        first_events = await asyncio.wait_for(first, 1)
        for events in (first_events, second_events):
            self.assertEqual(events[-1]["type"], "done")
            self.assertNotIn("agy_queued", [e.get("code") for e in events])
        self.assertEqual(len(self.spawns), 2)
        self.assertFalse(agy_session.agy_turn_busy())

    async def _written(self, index):
        while len(self.processes) <= index:
            await asyncio.sleep(0)
        await self.processes[index].written.wait()

    async def test_approval_cards_of_two_concurrent_gated_turns_reach_their_own_chat(self):
        # The step gate's hook is a child of the agy process, and agy hands
        # its env to its children (measured), so the hook's card request names
        # the chat whose child asked. Two gated turns now run side by side.
        self.auto = False
        backend = _ApprovalBackend()
        try:
            first, release_first = await self.start_blocked_turn(11)
            await asyncio.wait_for(self._written(0), 1)
            second, release_second = await self.start_blocked_turn(12)
            await asyncio.wait_for(self._written(1), 1)

            # Both turns are mid-stream; each child's hook asks for a write card.
            for index in (1, 0):
                child_env = self.spawns[index][1]["env"]
                hook_env = {"GAMACHINE_CONVERSATION_ID": child_env["GAMACHINE_CONVERSATION_ID"],
                            "UNITYAI_URL": backend.url, "LOCAL_APP_TOKEN": "test-token"}
                with patch.dict("os.environ", hook_env):
                    answer = await asyncio.to_thread(
                        agy_native_approval._approval, {"path": "a.txt"}, "C:\\ws",
                        time.monotonic() + 10)
                self.assertEqual(answer["status"], "resolved")
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            release_first.set()
            release_second.set()
            await asyncio.wait_for(asyncio.gather(first, second), 1)
        finally:
            backend.close()
        self.assertEqual([body["conversation_id"] for body in backend.bodies], [12, 11])

    async def test_a_child_starts_with_the_shared_files_written_for_it(self):
        # .mcp.json, the agy registration, the gate state and hooks.json are
        # shared by every agy child. While one chat's child is being created,
        # another chat's spawn must not rewrite them underneath it.
        log = []
        first_starting = asyncio.Event()
        second_wrote = asyncio.Event()
        original_spawn = self.spawn

        def wrote(cwd, *_args, **_kwargs):
            log.append(("files", os.path.abspath(cwd)))
            if first_starting.is_set():
                second_wrote.set()
            return True

        async def slow_spawn(*argv, **kwargs):
            if not first_starting.is_set():
                first_starting.set()
                try:
                    # Long enough for the second chat to reach its writes
                    # if nothing holds it back.
                    await asyncio.wait_for(second_wrote.wait(), 0.3)
                except asyncio.TimeoutError:
                    pass
            process = await original_spawn(*argv, **kwargs)
            log.append(("start", os.path.abspath(kwargs["cwd"])))
            return process

        with (tempfile.TemporaryDirectory() as first_dir,
              tempfile.TemporaryDirectory() as second_dir,
              patch.object(AgyProvider, "_write_mcp_config", side_effect=wrote),
              patch.object(AgyProvider, "_write_step_gate", side_effect=wrote),
              patch.object(agy_session.asyncio, "create_subprocess_exec", side_effect=slow_spawn)):
            first = asyncio.create_task(self.collect(
                agy_session.get_session(11, cwd=first_dir), cwd=first_dir))
            await asyncio.wait_for(first_starting.wait(), 1)
            second = asyncio.create_task(self.collect(
                agy_session.get_session(12, cwd=second_dir), cwd=second_dir))
            results = await asyncio.wait_for(asyncio.gather(first, second), 2)
        for events in results:
            self.assertEqual(events[-1]["type"], "done")
        starts = [i for i, (kind, _) in enumerate(log) if kind == "start"]
        self.assertEqual(len(starts), 2, log)
        for index in starts:
            last_write = next(entry for entry in reversed(log[:index]) if entry[0] == "files")
            self.assertEqual(last_write[1], log[index][1], log)

    async def test_a_timed_out_turn_does_not_claim_another_chats_files(self):
        # Audit, 24 Nov 2026: chat 11 timed out while chat 12 wrote a file in
        # the same workspace, and chat 11's message listed that file under
        # "Files this turn changed".
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workspace:
            subprocess.run(["git", "init", "-q", workspace], check=True, capture_output=True)
            self.assertEqual(agy_session.workspace_state(workspace), {})
            written = os.path.join(workspace, "written-by-chat-12.txt")
            original_spawn = self.spawn

            async def spawn(*argv, **kwargs):
                child = await original_spawn(*argv, **kwargs)
                if kwargs["env"]["GAMACHINE_CONVERSATION_ID"] == "12":
                    drain = child.stdin.drain

                    async def drain_and_write():
                        with open(written, "w", encoding="utf-8") as f:
                            f.write("chat 12\n")
                        await drain()

                    child.stdin.drain = drain_and_write
                return child

            release = asyncio.Event()
            self.plans.append({"release": release})
            try:
                with (patch.object(BaseCLIProvider, "_AGY_MAX_TOTAL", 1.0),
                      patch.object(agy_session.asyncio, "create_subprocess_exec", side_effect=spawn)):
                    stuck = asyncio.create_task(self.collect(
                        agy_session.get_session(11, cwd=workspace), cwd=workspace, language="en"))
                    await asyncio.wait_for(self._written(0), 1)
                    writer = await asyncio.wait_for(self.collect(
                        agy_session.get_session(12, cwd=workspace), cwd=workspace, language="en"), 1)
                    self.assertEqual(writer[-1]["type"], "done")
                    self.assertFalse(stuck.done())
                    self.assertTrue(os.path.isfile(written))
                    error = (await asyncio.wait_for(stuck, 3))[-1]
            finally:
                release.set()
        self.assertEqual((error.get("code"), error.get("limit")), ("agy_turn_limit", "total"))
        self.assertNotIn("written-by-chat-12.txt", error.get("changed_files") or [])
        self.assertNotIn("Files this turn changed", error["message"])

    async def test_a_timed_out_turn_alone_in_its_workspace_claims_its_files(self):
        # The other half of the audit fix (T-02): with no other chat in the
        # workspace, the files are this turn's, under the plain label and in
        # the changed_files field.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workspace:
            subprocess.run(["git", "init", "-q", workspace], check=True, capture_output=True)
            self.assertEqual(agy_session.workspace_state(workspace), {})
            written = os.path.join(workspace, "written-by-chat-11.txt")
            original_spawn = self.spawn

            async def spawn(*argv, **kwargs):
                child = await original_spawn(*argv, **kwargs)
                drain = child.stdin.drain

                async def drain_and_write():
                    with open(written, "w", encoding="utf-8") as f:
                        f.write("chat 11\n")
                    await drain()

                child.stdin.drain = drain_and_write
                return child

            release = asyncio.Event()
            self.plans.append({"release": release})
            try:
                with (patch.object(BaseCLIProvider, "_AGY_MAX_TOTAL", 1.0),
                      patch.object(agy_session.asyncio, "create_subprocess_exec", side_effect=spawn)):
                    events = await asyncio.wait_for(self.collect(
                        agy_session.get_session(11, cwd=workspace), cwd=workspace, language="en"), 5)
            finally:
                release.set()
        error = events[-1]
        self.assertEqual((error.get("code"), error.get("limit")), ("agy_turn_limit", "total"))
        self.assertEqual(error.get("changed_files"), ["written-by-chat-11.txt"])
        self.assertIn("Files this turn changed in the workspace", error["message"])
        self.assertNotIn("another agy chat", error["message"])


class _Unkillable:
    """A closed child whose kill fails: it stays alive (returncode None)."""

    def __init__(self):
        self.pid = None
        self.returncode = None


class TestSyncLeavesARetiredChildsDenyingFile(unittest.TestCase):
    """T-01 (audit, 24 Nov 2026): while a closed child that survived its kill
    still runs, _sync_gate_state must not rewrite the state file: "closed" or
    "side" there is all that denies that child its tools, and the published
    mode (auto here) would hand them back."""

    def setUp(self):
        from agentic import approval_mode
        from providers import agy_provider
        self.tmp = tempfile.TemporaryDirectory()
        home = self.tmp.name
        real_expand = os.path.expanduser
        self.home_patch = patch.object(
            agy_provider.os.path, "expanduser",
            side_effect=lambda p: p.replace("~", home, 1) if p.startswith("~") else real_expand(p))
        self.home_patch.start()
        agy_session._GATED_CHILDREN.clear()
        agy_session._RETIRED.clear()
        agy_session._SESSIONS.clear()
        approval_mode.set_ui_secret("t01-secret")
        approval_mode.set_mode("auto", source="test")  # no child yet: nothing written
        self.child = _Unkillable()
        self.token = object()

        def end_tree(process, strict=False):
            if process is self.child and process.returncode is None:
                raise PermissionError("access denied")
            process.returncode = -9

        self.kill_patch = patch.object(agy_session.process_tree, "end_tree_sync", side_effect=end_tree)
        self.kill_patch.start()

    def tearDown(self):
        self.kill_patch.stop()
        agy_session._GATED_CHILDREN.clear()
        agy_session._RETIRED.clear()
        agy_session._SESSIONS.clear()
        self.home_patch.stop()
        self.tmp.cleanup()

    def file_mode(self):
        from providers import agy_provider
        with open(agy_provider.gate_state_path(), encoding="utf-8") as f:
            return json.load(f)["mode"]

    def retire(self, state_mode):
        from providers import agy_provider
        if state_mode == "closed":
            agy_provider.write_gate_state(closed=True)
        else:
            agy_provider.write_gate_state(mode=state_mode)
        agy_session._GATED_CHILDREN[self.token] = self.child
        agy_session._RETIRED.add(self.token)

    def test_a_surviving_closed_child_keeps_its_file_through_a_resync(self):
        for state_mode in ("closed", agy_session.SIDE_MODE):
            with self.subTest(state_mode):
                self.retire(state_mode)
                self.assertTrue(agy_session._sync_gate_state())
                self.assertEqual(self.file_mode(), state_mode)
                agy_session._RETIRED.clear()
                agy_session._GATED_CHILDREN.clear()

    def test_once_the_child_is_stopped_the_file_follows_the_published_mode(self):
        self.retire("closed")
        self.assertTrue(agy_session._sync_gate_state())
        self.assertEqual(self.file_mode(), "closed")
        self.child.returncode = 0  # it exited after all
        self.assertTrue(agy_session._sync_gate_state())
        self.assertEqual(self.file_mode(), "auto")
        self.assertEqual(agy_session._RETIRED, set())

    def test_a_flip_to_balanced_does_not_loosen_it_either(self):
        from agentic import approval_mode
        self.retire("closed")
        approval_mode.set_mode("balanced", source="test")
        self.assertEqual(approval_mode.current_mode(), "balanced")
        self.assertEqual(self.file_mode(), "closed")


class TestRewordedTextsReachEnglishUsers(unittest.TestCase):
    def test_every_text_the_parallel_change_reworded_is_english_for_english_users(self):
        # The backend sends these in Turkish; error_i18n (or the text's own
        # English branch) is what an English UI shows, the 409 detail too.
        shown = {
            "side busy refusal": error_i18n.translate_detail(agy_session.SIDE_BUSY_MESSAGE, "en"),
            "queued status": error_i18n.localize_event(agy_session._QUEUED_EVENT, "en")["detail"],
            "queued too long": agy_session.limit_message(
                "total", language="en", summary="", started=False),
        }
        for name, text in shown.items():
            with self.subTest(name):
                self.assertFalse(_TURKISH_LETTERS & set(text), text)
