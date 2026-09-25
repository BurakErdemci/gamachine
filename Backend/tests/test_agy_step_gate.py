"""Step-mode gate for agy's built-in file writers (workspace .agents/hooks.json).

The live behaviour was measured against agy 1.2.8 on 25 Sep 2026 (writes denied
with this reason, MCP calls untouched); these tests pin the file shape and the
script output that measurement depended on.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from providers import agy_provider
from providers.agy_provider import AgyProvider, AgyStepGateError, STEP_GATE_KEY, STEP_GATE_TOOLS


class TestAgyStepGate(unittest.TestCase):
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
        self.hooks_path = os.path.join(self.ws, ".agents", "hooks.json")

    def tearDown(self):
        self.expand.stop()
        self.tmp.cleanup()

    def read_hooks(self):
        with open(self.hooks_path, encoding="utf-8") as f:
            return json.load(f)

    def test_step_mode_denies_every_builtin_writer(self):
        self.assertTrue(AgyProvider()._write_step_gate(self.ws, step_mode=True))
        entries = self.read_hooks()[STEP_GATE_KEY]["PreToolUse"]
        self.assertEqual([e["matcher"] for e in entries], [
            "write_to_file", "replace_file_content", "multi_replace_file_content",
            "sed_file", "notebook_edit", "send_command_input", "run_command"])
        # MCP calls are gated by the server's approval gate, never here.
        self.assertNotIn("call_mcp_tool", STEP_GATE_TOOLS)
        command = entries[0]["hooks"][0]["command"]
        self.assertTrue(os.path.exists(command))
        self.assertNotIn('"', command)
        with open(os.path.join(self.ws, ".gitignore"), encoding="utf-8") as f:
            self.assertIn(".agents/hooks.json", f.read())
        with open(os.path.join(self.home, ".unity_architect_ai", "agy", "step-gate.json"),
                  encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(state["mode"], "step")
        self.assertEqual(state["launcher"], AgyProvider()._launcher_path("unityai"))

    def run_shim(self, payload: dict) -> dict:
        if not hasattr(self, "shim"):
            self.shim = self.read_hooks()[STEP_GATE_KEY]["PreToolUse"][0]["hooks"][0]["command"]
        command = self.shim
        argv = ["cmd", "/c", command] if sys.platform == "win32" else [command]
        out = subprocess.run(argv, input=json.dumps(payload).encode(),
                             capture_output=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.decode("utf-8").strip())

    def test_generated_shim_runs_the_backend_hook(self):
        AgyProvider()._write_step_gate(self.ws, step_mode=True)
        launcher = AgyProvider()._launcher_path("unityai")
        bridge = (f'& "{launcher}" delete-file --path "a.txt"' if sys.platform == "win32"
                  else f'{launcher} delete-file --path "a.txt"')
        write = self.run_shim({"toolCall": {"name": "write_to_file", "args": {}}})
        self.assertEqual(write["decision"], "deny")
        self.assertIn("unityai save-file", write["reason"])
        shell = self.run_shim({"toolCall": {"name": "run_command",
                                            "args": {"CommandLine": "python -c \"open('x','w')\""}}})
        self.assertEqual(shell["decision"], "deny")
        ok = self.run_shim({"toolCall": {"name": "run_command", "args": {"CommandLine": bridge}}})
        self.assertEqual(ok["decision"], "allow")
        # A flip to auto reaches a hook that is already installed.
        AgyProvider()._write_step_gate(self.ws, step_mode=False)
        self.assertEqual(self.run_shim({"toolCall": {"name": "write_to_file"}})["decision"], "allow")

    def test_user_hooks_are_kept_and_auto_removes_only_ours(self):
        os.makedirs(os.path.dirname(self.hooks_path))
        user = {"lint": {"PostToolUse": [{"matcher": "run_command", "hooks": [{"command": "x"}]}]}}
        with open(self.hooks_path, "w", encoding="utf-8") as f:
            json.dump(user, f)
        provider = AgyProvider()
        self.assertTrue(provider._write_step_gate(self.ws, step_mode=True))
        both = self.read_hooks()
        self.assertEqual(both["lint"], user["lint"])
        self.assertIn(STEP_GATE_KEY, both)
        self.assertTrue(provider._write_step_gate(self.ws, step_mode=False))
        self.assertEqual(self.read_hooks(), user)
        # A file that existed before is the user's: we do not gitignore it.
        self.assertFalse(os.path.exists(os.path.join(self.ws, ".gitignore")))

    def test_auto_mode_without_a_file_creates_nothing(self):
        self.assertTrue(AgyProvider()._write_step_gate(self.ws, step_mode=False))
        self.assertFalse(os.path.exists(self.hooks_path))

    def write_user_file(self, text):
        os.makedirs(os.path.dirname(self.hooks_path), exist_ok=True)
        with open(self.hooks_path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_unreadable_user_file_is_left_alone_and_step_mode_refuses(self):
        for text in ("{not json", "[1, 2]", '"text"', "{", "\xff\xfe"):
            with self.subTest(text=text):
                self.write_user_file(text)
                with self.assertRaises(AgyStepGateError) as caught:
                    AgyProvider()._write_step_gate(self.ws, step_mode=True)
                message = str(caught.exception)
                self.assertIn(self.hooks_path, message)
                self.assertIn("agy başlatılmadı", message)
                self.assertIn("silin", message)
                with open(self.hooks_path, encoding="utf-8") as f:
                    self.assertEqual(f.read(), text)
                # Auto mode only needs our entry gone; it reports, never raises.
                self.assertFalse(AgyProvider()._write_step_gate(self.ws, step_mode=False))

    def test_state_file_not_written_refuses_even_with_a_stale_auto_state(self):
        agy_provider.write_gate_state(auto=True)  # left over from an auto session
        with patch.object(agy_provider, "write_gate_state", side_effect=OSError("disk full")):
            with self.assertRaises(AgyStepGateError) as caught:
                AgyProvider()._write_step_gate(self.ws, step_mode=True)
        self.assertIn("step-gate.json", str(caught.exception))
        self.assertIn("disk full", str(caught.exception))

    def test_stale_auto_state_is_caught_by_the_read_back(self):
        agy_provider.write_gate_state(auto=True)
        with patch.object(agy_provider, "write_gate_state"):  # a write that silently did nothing
            with self.assertRaises(AgyStepGateError) as caught:
                AgyProvider()._write_step_gate(self.ws, step_mode=True)
        self.assertIn("doğrulanamadı", str(caught.exception))

    def test_hooks_write_refused_raises_in_step_mode(self):
        with patch("providers.workspace_config.guvenli_config_yaz", return_value=False):
            with self.assertRaises(AgyStepGateError) as caught:
                AgyProvider()._write_step_gate(self.ws, step_mode=True)
        self.assertIn(self.hooks_path, str(caught.exception))
        self.assertFalse(os.path.exists(self.hooks_path))

    def test_shim_write_failure_raises_in_step_mode(self):
        with patch.object(AgyProvider, "_step_gate_command", side_effect=PermissionError("locked")):
            with self.assertRaises(AgyStepGateError) as caught:
                AgyProvider()._write_step_gate(self.ws, step_mode=True)
        self.assertIn("locked", str(caught.exception))

    def test_read_back_rejects_a_tampered_entry(self):
        provider = AgyProvider()
        provider._write_step_gate(self.ws, step_mode=True)
        hooks = self.read_hooks()
        command = hooks[STEP_GATE_KEY]["PreToolUse"][0]["hooks"][0]["command"]
        state = os.path.join(self.home, ".unity_architect_ai", "agy", "step-gate.json")
        self.assertIsNone(provider._step_gate_problem(self.hooks_path, state, command))
        hooks[STEP_GATE_KEY]["PreToolUse"].pop()  # run_command no longer gated
        with open(self.hooks_path, "w", encoding="utf-8") as f:
            json.dump(hooks, f)
        self.assertIsNotNone(provider._step_gate_problem(self.hooks_path, state, command))


class TestSpawnRefusedWithoutGate(unittest.IsolatedAsyncioTestCase):
    """The audit's shape end to end: step mode, one stray character in the
    user's hooks.json, the real installer; agy must not be spawned."""

    async def test_malformed_hooks_file_blocks_the_spawn(self):
        import asyncio
        from providers import agy_session
        from providers.cli_base import BaseCLIProvider
        with tempfile.TemporaryDirectory() as tmp:
            home, ws = os.path.join(tmp, "home"), os.path.join(tmp, "ws")
            os.makedirs(os.path.join(ws, ".agents"))
            hooks = os.path.join(ws, ".agents", "hooks.json")
            with open(hooks, "w", encoding="utf-8") as f:
                f.write("{")
            real_expand = os.path.expanduser
            spawns = []

            async def spawn(*argv, **kwargs):
                spawns.append(argv)
                raise AssertionError("agy spawned without a step gate")

            patches = [
                patch.object(agy_provider.os.path, "expanduser",
                             side_effect=lambda p: p.replace("~", home, 1) if p.startswith("~") else real_expand(p)),
                patch.object(BaseCLIProvider, "_AGY_LOCK", asyncio.Lock()),
                patch.object(AgyProvider, "_agy_binary", return_value="fake-agy"),
                patch.object(AgyProvider, "_resolve_exec", side_effect=lambda c: c),
                patch.object(AgyProvider, "_write_mcp_config", return_value=""),
                patch.object(AgyProvider, "_set_agy_model"),
                patch.object(AgyProvider, "_stream_instructions", return_value=""),
                patch.object(agy_session, "_global_auto_mode", return_value=False),
                patch.object(agy_session.asyncio, "create_subprocess_exec", side_effect=spawn),
            ]
            for p in patches:
                p.start()
            try:
                session = agy_session.get_session(4242, cwd=ws)
                events = [e async for e in session.stream("merhaba", cwd=ws)]
                await agy_session.close_all_sessions()
            finally:
                for p in reversed(patches):
                    p.stop()
            self.assertEqual(spawns, [])
            self.assertEqual([e["type"] for e in events], ["error"])
            self.assertIn(hooks, events[0]["message"])
            self.assertIn("agy başlatılmadı", events[0]["message"])
            with open(hooks, encoding="utf-8") as f:
                self.assertEqual(f.read(), "{")


if __name__ == "__main__":
    unittest.main()
