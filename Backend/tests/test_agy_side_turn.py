"""A read-only side question on agy (Burak, 27 Sep 2026).

agy runs one turn machine-wide. A side question now runs when no agy turn
does, in its own session keyed by the side chat, with the hook's state file
on "side" for exactly that turn; any other agy turn stops it. No real agy,
CLI or model is started: processes are fakes, the state file lives in a
temporary home.
"""
import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import agy_step_gate as gate
import unity_tool_policy
from agentic.side_prompt import SideTurn
from providers import agy_provider, agy_session
from providers.agy_provider import AgyProvider, STEP_GATE_KEY, STEP_GATE_TOOLS
from providers.cli_base import BaseCLIProvider
from tests.test_agy_stream_session import TURNS, FakeProcess

LAUNCHER = r"C:\x\unityai.cmd"
SIDE_ID = 77
MAIN_ID = 11


def call(name, args=None):
    return {"toolCall": {"name": name, "args": {} if args is None else args}}


def mcp(server, tool, arguments=None, **extra):
    args = {"ServerName": server, "ToolName": tool, **extra}
    if arguments is not None:
        args["Arguments"] = arguments
    return call("call_mcp_tool", args)


class HookCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "step-gate.json")
        gate.write_state(self.state, gate.SIDE_MODE, LAUNCHER)

    def tearDown(self):
        self.tmp.cleanup()

    def decide(self, payload, windows=True):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return gate.decide(raw, self.state, windows=windows, cwd=self.tmp.name)


class TestSideHook(HookCase):
    def assert_refused(self, payload):
        out = self.decide(payload)
        self.assertEqual(out["decision"], "deny", payload)
        self.assertIn("read-only side question", out["reason"])

    def test_every_listed_writer_command_and_agent_tool_is_denied(self):
        names = set(STEP_GATE_TOOLS) | {
            "send_command_input", "generate_image", "schedule", "invoke_subagent",
            "send_message", "browser_subagent", "open_browser_url", "execute_browser_javascript",
            "click_browser_pixel", "delete_file", "delete_directory", "run_script", "move",
        }
        names |= {n for n in gate.SIDE_HOOK_MATCHERS if n not in (".*", gate.MCP_CALL_TOOL)}
        for name in sorted(names):
            self.assert_refused(call(name, {"TargetFile": "a.txt", "CommandLine": "whoami"}))

    def test_the_unityai_bridge_is_denied_too(self):
        bridge = f'& "{LAUNCHER}" save-file --path "a.txt" --content "x"'
        self.assertEqual(gate.unityai_command_allowed(bridge, LAUNCHER, windows=True), True)
        self.assert_refused(call("run_command", {"CommandLine": bridge}))

    def test_a_tool_nobody_listed_is_denied(self):
        self.assert_refused(call("some_tool_agy_adds_next_month"))

    def test_reads_are_allowed(self):
        for name in sorted(gate.SIDE_READ_TOOLS):
            self.assertEqual(self.decide(call(name, {"AbsolutePath": "a.txt"}))["decision"],
                             "allow", name)

    def test_unreadable_payloads_are_denied(self):
        for raw in (b"not json", json.dumps({"toolCall": {"name": 7}}).encode(),
                    json.dumps({"toolCall": {}}).encode()):
            self.assertEqual(self.decide(raw)["decision"], "deny")

    def test_unity_mcp_read_is_allowed_and_write_denied(self):
        unity_tool_policy.reset_cache()
        self.assertEqual(self.decide(mcp("unityMCP", "read_console", {"action": "get"}))["decision"],
                         "allow")
        # The arguments as a JSON string, and other spellings of the fields.
        self.assertEqual(self.decide(mcp("unityMCP", "read_console", '{"action": "get"}'))["decision"],
                         "allow")
        other = call("call_mcp_tool", {"server_name": "unityMCP", "tool_name": "read_console",
                                        "arguments": {}})
        self.assertEqual(self.decide(other)["decision"], "allow")
        for payload in (mcp("unityMCP", "read_console", {"action": "clear"}),
                        mcp("unityMCP", "manage_gameobject", {"action": "create", "name": "x"}),
                        mcp("unityMCP", "manage_scene", {"action": "load", "path": "a.unity"}),
                        mcp("unityMCP", "no_such_tool", {})):
            out = self.decide(payload)
            self.assertEqual(out["decision"], "deny", payload)
            self.assertIn("read-only side question", out["reason"])

    def test_other_servers_meshy_and_unclear_calls_are_denied(self):
        meshy = self.decide(mcp("meshy", "meshy_check_balance", {}))
        self.assertEqual(meshy["decision"], "deny")
        self.assertIn("credits", meshy["reason"])
        for payload in (
                mcp("unityai", "send_chat_message", {"to": 1, "text": "hi"}),
                mcp("unityai", "list_chats", {}),
                mcp("UnityMCP", "read_console", {}),
                call("call_mcp_tool", {"ToolName": "read_console"}),
                call("call_mcp_tool", {"ServerName": "unityMCP"}),
                call("call_mcp_tool", "not a dict"),
                # Two spellings that disagree: which one agy runs is unknown.
                mcp("unityMCP", "read_console", {}, name="manage_gameobject"),
                mcp("unityMCP", "read_console", "{broken"),
                mcp("unityMCP", "read_console", [1, 2])):
            self.assertEqual(self.decide(payload)["decision"], "deny", payload)

    def test_without_the_ledger_every_mcp_call_is_denied(self):
        unity_tool_policy.reset_cache()
        try:
            with patch.object(unity_tool_policy, "_candidate_paths", return_value=[]):
                out = self.decide(mcp("unityMCP", "read_console", {"action": "get"}))
            self.assertEqual(out["decision"], "deny")
        finally:
            unity_tool_policy.reset_cache()

    def test_hook_process_decides_side_calls_without_loading_the_app(self):
        backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for payload, want in ((mcp("unityMCP", "read_console", {"action": "get"}), "allow"),
                              (call("write_to_file", {"TargetFile": "a.txt"}), "deny"),
                              (call("view_file", {"AbsolutePath": "a.txt"}), "allow")):
            started = time.monotonic()
            out = subprocess.run([sys.executable, os.path.join(backend, "app", "main.py"),
                                  "agy-hook", "--state", self.state],
                                 input=json.dumps(payload).encode(), capture_output=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout.decode())["decision"], want)
            self.assertLess(time.monotonic() - started, 2.5)


class TestOtherModesUnchanged(HookCase):
    """No behaviour change outside "side": the step tools keep their verdicts
    (a 410-call differential against the previous hook found none), and a
    tool only a side child's hooks.json names is allowed, as with no hook."""

    def test_tools_only_the_side_matcher_names_are_allowed_outside_side(self):
        names = [n for n in gate.SIDE_HOOK_MATCHERS if n != ".*"] + sorted(gate.SIDE_READ_TOOLS)
        for mode in ("auto", "step", gate.BALANCED_MODE):
            gate.write_state(self.state, mode, LAUNCHER)
            for name in names:
                self.assertEqual(self.decide(call(name, {"ServerName": "meshy"}))["decision"],
                                 "allow", (mode, name))

    def test_step_tools_keep_their_verdicts(self):
        bridge = f'& "{LAUNCHER}" delete-file --path "a.txt"'
        gate.write_state(self.state, "step", LAUNCHER)
        for name in gate.GATED_TOOLS:
            self.assertEqual(self.decide(call(name)), gate._deny(gate.WRITE_REASON))
        self.assertEqual(self.decide(call("run_command", {"CommandLine": "whoami"})),
                         gate._deny(gate.RUN_REASON))
        self.assertEqual(self.decide(call("run_command", {"CommandLine": bridge}))["decision"], "allow")
        gate.write_state(self.state, "auto", LAUNCHER)
        for name in STEP_GATE_TOOLS:
            self.assertEqual(self.decide(call(name, {"CommandLine": "whoami"}))["decision"], "allow")
        gate.write_state(self.state, gate.CLOSED_MODE, "")
        for name in list(STEP_GATE_TOOLS) + ["view_file", "call_mcp_tool"]:
            self.assertEqual(self.decide(call(name))["decision"], "deny")


class TestSideHooksFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.ws = os.path.join(self.tmp.name, "ws")
        os.makedirs(self.home)
        os.makedirs(self.ws)
        real_expand = os.path.expanduser
        self.expand = patch.object(
            agy_provider.os.path, "expanduser",
            side_effect=lambda p: p.replace("~", self.home, 1) if p.startswith("~") else real_expand(p))
        self.expand.start()

    def tearDown(self):
        self.expand.stop()
        self.tmp.cleanup()

    def matchers_and_mode(self):
        with open(os.path.join(self.ws, ".agents", "hooks.json"), encoding="utf-8") as f:
            entries = json.load(f)[STEP_GATE_KEY]["PreToolUse"]
        with open(agy_provider.gate_state_path(), encoding="utf-8") as f:
            return [e["matcher"] for e in entries], json.load(f)["mode"]

    def test_only_a_side_child_gets_the_wide_matcher(self):
        provider = AgyProvider()
        self.assertTrue(provider._write_step_gate(self.ws, step_mode=True, mode=gate.SIDE_MODE))
        self.assertEqual(self.matchers_and_mode(),
                         (list(STEP_GATE_TOOLS) + list(gate.SIDE_HOOK_MATCHERS), "side"))
        for listed in ("call_mcp_tool", "generate_image", "schedule", "invoke_subagent",
                       "send_message", "browser_click_element", ".*"):
            self.assertIn(listed, gate.SIDE_HOOK_MATCHERS)
        # The next main child's spawn writes today's list back.
        self.assertTrue(provider._write_step_gate(self.ws, step_mode=False))
        self.assertEqual(self.matchers_and_mode(), (list(STEP_GATE_TOOLS), "auto"))
        self.assertTrue(provider._write_step_gate(self.ws, step_mode=True, mode="balanced"))
        self.assertEqual(self.matchers_and_mode(), (list(STEP_GATE_TOOLS), "balanced"))

    def test_side_state_name(self):
        self.assertEqual(agy_provider.gate_mode_name(mode=gate.SIDE_MODE), "side")
        self.assertEqual(agy_provider.gate_mode_name(mode="whatever"), "step")


class TestSideTurn(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = self.tmp.name
        real_expand = os.path.expanduser
        self.mode = "auto"
        self.processes, self.spawn_modes, self.plans = [], [], []
        self.patches = [
            patch.object(agy_provider.os.path, "expanduser",
                         side_effect=lambda p: p.replace("~", home, 1) if p.startswith("~") else real_expand(p)),
            patch.object(BaseCLIProvider, "_AGY_LOCK", asyncio.Lock()),
            patch.object(AgyProvider, "_agy_binary", return_value="fake-agy"),
            patch.object(AgyProvider, "_resolve_exec", side_effect=lambda c: c),
            patch.object(AgyProvider, "_write_mcp_config", return_value=""),
            patch.object(AgyProvider, "_register_agy_workspace"),
            patch.object(AgyProvider, "_stream_instructions", return_value=""),
            patch.object(AgyProvider, "_step_gate_command", return_value=__file__),
            patch("providers.workspace_config.guvenli_config_yaz", return_value=True),
            patch.object(AgyProvider, "_step_gate_problem", return_value=None),
            patch.object(agy_session, "_global_gate_mode", side_effect=lambda: self.mode),
            patch.object(agy_session.asyncio, "create_subprocess_exec", side_effect=self.spawn),
        ]
        for p in self.patches:
            p.start()
        self.state = agy_provider.gate_state_path()
        self.reset_registry()

    async def asyncTearDown(self):
        await agy_session.close_all_sessions()
        self.reset_registry()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def reset_registry(self):
        agy_session._SESSIONS.clear()
        agy_session._RESUME_IDS.clear()
        agy_session._GATED_CHILDREN.clear()
        agy_session._RETIRED.clear()
        agy_session._SIDE_TURN = None
        agy_session._SIDE_DONE = None

    def file_mode(self):
        try:
            with open(self.state, encoding="utf-8") as f:
                return json.load(f)["mode"]
        except FileNotFoundError:
            return None

    async def spawn(self, *argv, **kwargs):
        self.spawn_modes.append(self.file_mode())
        process = FakeProcess(**(self.plans.pop(0) if self.plans else {}))
        self.processes.append(process)
        return process

    def sent(self, process):
        return [json.loads(line)["message"]["content"] for line in process.stdin.lines]

    def side_runner(self, **kwargs):
        from agentic.agent_runner import AgentRunner
        kwargs.setdefault("language", "tr")
        return AgentRunner(provider_type="subscription", api_key="", model_name="gemini-3.8-flash",
                           conversation_id=SIDE_ID, workspace_path=self.tmp.name, read_only=True,
                           side_turn=SideTurn(question="Bu neden böyle?", main_history="ANA GEÇMİŞ"),
                           **kwargs)

    async def collect(self, stream):
        return [event async for event in stream]

    async def wait_written(self, index):
        while len(self.processes) <= index:
            await asyncio.sleep(0)
        await self.processes[index].written.wait()

    def assert_back_to_published(self):
        self.assertIsNone(agy_session._SIDE_TURN)
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())
        self.assertEqual(self.file_mode(), self.mode)

    async def test_idle_agy_runs_the_side_turn_in_its_own_session_on_side_state(self):
        release = asyncio.Event()
        self.plans = [{"release": release}]
        runner = self.side_runner()
        turn = asyncio.create_task(self.collect(runner._run_inner("Bu neden böyle?")))
        await asyncio.wait_for(self.wait_written(0), 5)
        # During the turn: the side chat's own session, the file on "side".
        session = agy_session.peek_session(SIDE_ID)
        self.assertIs(agy_session._SIDE_TURN, session)
        self.assertIsNone(agy_session.peek_session(MAIN_ID))
        self.assertEqual(self.spawn_modes, ["side"])
        self.assertEqual(self.file_mode(), "side")
        release.set()
        events = await asyncio.wait_for(turn, 5)
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(self.sent(self.processes[0]), [runner.side_turn.text(full=True)])
        self.assert_back_to_published()
        self.assertTrue(session.is_live)
        # A follow-up question resumes the side conversation: only the new
        # parts. The kept child's hooks.json gets the side matchers again.
        with patch.object(agy_provider, "step_gate_matchers",
                          wraps=agy_provider.step_gate_matchers) as matchers:
            events = await asyncio.wait_for(self.collect(self.side_runner()._run_inner("peki?")), 5)
        self.assertEqual(matchers.call_args.args, (gate.SIDE_MODE,))
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.sent(self.processes[0])[1], runner.side_turn.text(full=False))
        self.assert_back_to_published()

    async def test_side_turn_is_refused_while_any_agy_turn_holds_the_lock(self):
        await BaseCLIProvider._AGY_LOCK.acquire()
        try:
            agy_provider.write_gate_state(auto=True)
            events = await self.collect(self.side_runner()._run_inner("q"))
            self.assertEqual([(e.type, e.data.get("code")) for e in events],
                             [("error", "agy_side_busy")])
            self.assertEqual(events[0].data["message"], agy_session.SIDE_BUSY_MESSAGE)
            self.assertEqual(self.processes, [])
            self.assertEqual(self.file_mode(), "auto")
            self.assertIsNone(agy_session._SIDE_TURN)
            self.assertTrue(agy_session.agy_turn_busy())
        finally:
            BaseCLIProvider._AGY_LOCK.release()
        self.assertFalse(agy_session.agy_turn_busy())

    async def test_a_crashed_side_turn_restores_the_state_and_frees_the_lock(self):
        self.mode = "step"
        self.plans = [{"crash": True, "turns": []}]
        events = await self.collect(self.side_runner()._run_inner("q"))
        self.assertEqual(events[-1].type, "error")
        self.assertIsNone(events[-1].data.get("code"))
        self.assertEqual(self.spawn_modes, ["side"])
        self.assert_back_to_published()

    async def test_the_side_turn_times_out_after_two_minutes(self):
        self.assertEqual(agy_session.SIDE_TURN_TIMEOUT_S, 120)
        for language, text in (("tr", "Yan soru 2 dakikada bitmedi, durduruldu."),
                               ("en", "The side question did not finish within 2 minutes "
                                      "and was stopped.")):
            self.reset_registry()
            self.mode = "balanced"
            self.plans = [{"turns": [[TURNS[0][0]]]}]  # init only: no result ever
            with patch.object(agy_session, "SIDE_TURN_TIMEOUT_S", 0.05):
                events = await self.collect(self.side_runner(language=language)._run_inner("q"))
            self.assertEqual([(e.type, e.data.get("code"), e.data.get("message")) for e in events],
                             [("error", "agy_side_timeout", text)])
            self.assertTrue(self.processes[-1].killed)
            self.assert_back_to_published()

    async def test_a_main_turn_stops_the_side_turn_and_runs_after_it(self):
        self.mode = "step"
        self.plans = [{"turns": [[TURNS[0][0]]]}, {}]  # side waits forever; main is normal
        side = asyncio.create_task(self.collect(self.side_runner(language="tr")._run_inner("q")))
        await asyncio.wait_for(self.wait_written(0), 5)
        self.assertEqual(self.file_mode(), "side")
        main = agy_session.get_session(MAIN_ID, cwd=self.tmp.name)
        main_events = await asyncio.wait_for(
            self.collect(main.stream("ana tur", cwd=self.tmp.name)), 5)
        side_events = await asyncio.wait_for(side, 5)
        self.assertEqual([(e.type, e.data.get("code"), e.data.get("message")) for e in side_events],
                         [("error", "agy_side_preempted", "Ana sohbet başladı, yan soru durduruldu.")])
        self.assertEqual(main_events[-1]["type"], "done")
        # The main turn never queued behind the side one, and spawned after the
        # side child was stopped, on the published mode.
        self.assertNotIn("status", [e["type"] for e in main_events])
        self.assertTrue(self.processes[0].killed)
        self.assertEqual(self.spawn_modes, ["side", "step"])
        self.assert_back_to_published()
        self.assertEqual(agy_session.side_stop_message("agy_side_preempted", "en"),
                         "The main chat started, so the side question was stopped.")

    async def test_a_flip_during_the_side_turn_cannot_loosen_it(self):
        release = asyncio.Event()
        self.plans = [{"release": release}]
        self.mode = "step"
        turn = asyncio.create_task(self.collect(self.side_runner()._run_inner("q")))
        await asyncio.wait_for(self.wait_written(0), 5)
        self.mode = "auto"
        self.assertTrue(agy_session._sync_gate_state())
        self.assertEqual(self.file_mode(), "side")
        agy_session.tighten_gate_state()
        self.assertEqual(self.file_mode(), "side")
        agy_session.peek_session(SIDE_ID).auto_approve = True
        self.assertEqual(self.file_mode(), "side")
        release.set()
        await asyncio.wait_for(turn, 5)
        # Restored to the mode published NOW, not the one before the turn.
        self.assertEqual(self.file_mode(), "auto")
        self.assert_back_to_published()

    async def test_a_stale_side_state_is_overwritten_when_a_main_turn_starts(self):
        # A backend that died mid side turn left "side" behind.
        gate.write_state(self.state, gate.SIDE_MODE, "")
        session = agy_session.get_session(MAIN_ID, cwd=self.tmp.name)
        events = await self.collect(session.stream("ana", cwd=self.tmp.name))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(self.spawn_modes, ["auto"])
        self.assertEqual(self.file_mode(), "auto")
        # A kept main process resyncs at its next turn start as well.
        gate.write_state(self.state, gate.SIDE_MODE, "")
        events = await self.collect(session.stream("ana 2", cwd=self.tmp.name))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.file_mode(), "auto")

    async def test_closing_the_side_chat_closes_its_agy_session(self):
        events = await self.collect(self.side_runner()._run_inner("q"))
        self.assertEqual(events[-1].type, "done")
        self.assertIsNotNone(agy_session.peek_session(SIDE_ID))
        await agy_session.close_session(SIDE_ID)
        self.assertIsNone(agy_session.peek_session(SIDE_ID))
        self.assertTrue(self.processes[0].killed)


if __name__ == "__main__":
    unittest.main()
