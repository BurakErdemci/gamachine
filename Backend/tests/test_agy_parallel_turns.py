"""Different agy chats run their turns at the same time (24 Nov 2026).

Before, one machine-wide lock held every agy turn for its whole stream, so a
second chat sat on "Sırada" until the first one's turn ended. The model goes
per process (--model/--effort), so only the files written at spawn are shared;
the fake child below stands in for agy.
"""
import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import agy_native_approval
from providers import agy_session
from tests import test_agy_stream_session as base


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
