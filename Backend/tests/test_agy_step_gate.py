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
from providers.agy_provider import AgyProvider, STEP_GATE_KEY, STEP_GATE_TOOLS


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

    def test_unreadable_user_file_is_left_alone(self):
        os.makedirs(os.path.dirname(self.hooks_path))
        with open(self.hooks_path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertFalse(AgyProvider()._write_step_gate(self.ws, step_mode=True))
        with open(self.hooks_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{not json")


if __name__ == "__main__":
    unittest.main()
