"""agy gets gamachineComputer / gamachineBrowser while their switches are on.

The config files are shared by every chat, so the entries carry no chat id; the
live child is restarted when the switch state it was spawned with changes.
"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from browser_use import registration as browser_registration
from computer_use import registration as computer_registration
from providers import agy_provider, agy_session
from providers.agy_provider import AgyProvider
from spawn_env import CONVERSATION_ENV
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

NAMES = ("gamachineComputer", "gamachineBrowser")


def _switches(on):
    return (patch("computer_use.settings.enabled_now", lambda: on),
            patch("browser_use.settings.enabled_now", lambda: on))


class TestAgyComputerUseRegistration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        real_expand = os.path.expanduser
        self.patches = [
            patch.object(agy_provider.os.path, "expanduser",
                         side_effect=lambda p: p.replace("~", self.home, 1) if p.startswith("~") else real_expand(p)),
            patch.object(AgyProvider, "_write_cli_env"),
            patch.object(unity_mcp_manager, "mcp_url", return_value=None),
        ]
        for p in self.patches:
            p.start()
        self.paths = [os.path.join(self.home, ".gemini", *parts, "mcp_config.json")
                      for parts in (("config",), ("antigravity-cli",))]
        for path in self.paths:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"mcpServers": {"meshy": {"command": "npx"}}}, f)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def servers(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)["mcpServers"]

    def register(self, on):
        patches = _switches(on)
        for p in patches:
            p.start()
        try:
            AgyProvider()._register_mcp("launcher", self.home, "http://127.0.0.1:1")
        finally:
            for p in patches:
                p.stop()

    def test_switches_on_write_both_servers_without_chat_id(self):
        self.register(True)
        for path in self.paths:
            servers = self.servers(path)
            for name, mod in zip(NAMES, (computer_registration, browser_registration)):
                entry = servers[name]
                self.assertEqual([entry["command"], *entry["args"]], mod.server_argv())
                self.assertTrue(entry["trust"])
                self.assertNotIn(CONVERSATION_ENV, entry["env"])
            self.assertIn("meshy", servers)
            self.assertIn("unityai", servers)

    def test_switches_off_remove_both_servers_and_keep_user_entries(self):
        self.register(True)
        self.register(False)
        for path in self.paths:
            servers = self.servers(path)
            for name in NAMES:
                self.assertNotIn(name, servers)
            self.assertIn("meshy", servers)


class _Stream:
    async def read(self, n=-1):
        return b""

    async def readline(self):
        return b""

    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


class _Proc:
    pid = 4242424
    returncode = None

    def __init__(self):
        self.stdin, self.stdout, self.stderr = _Stream(), _Stream(), _Stream()

    def kill(self):
        self.returncode = -9

    def terminate(self):
        self.returncode = -15

    async def wait(self):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class TestAgySessionRestartOnSwitchChange(unittest.TestCase):
    def test_restart_only_when_switch_state_changes(self):
        state = {"on": False}
        spawns = []

        async def fake_spawn(*argv, **kw):
            spawns.append(argv)
            return _Proc()

        def fake_build_cmd(self, **k):
            self._pending_agy_model_slug = AgyProvider._resolve_agy_model(
                self.binary_name, k.get("thinking_level", "auto"))
            return ["agy"]

        with tempfile.TemporaryDirectory() as ws:
            patches = [
                patch("computer_use.settings.enabled_now", lambda: state["on"]),
                patch("browser_use.settings.enabled_now", lambda: state["on"]),
                patch("asyncio.create_subprocess_exec", fake_spawn),
                patch.object(agy_session, "_effective_gate_mode", lambda: "auto"),
                patch.object(agy_session, "_sync_gate_state", lambda: True),
                patch.object(agy_session, "_stop_retired_children", lambda: []),
                patch.object(AgyProvider, "_build_cmd", fake_build_cmd),
                patch.object(AgyProvider, "_resolve_exec", lambda self, c: list(c)),
                patch.object(AgyProvider, "_write_mcp_config", lambda self, w: None),
                patch.object(AgyProvider, "_register_agy_workspace", lambda self, w: None),
                patch.object(AgyProvider, "_write_step_gate", lambda self, *a, **k: None),
                patch.object(AgyProvider, "_stream_instructions", lambda self: ""),
            ]
            for p in patches:
                p.start()

            async def scenario():
                session = agy_session.AgyStreamSession(778, cwd=ws)
                try:
                    counts = []
                    for on in (False, False, True, True, False):
                        state["on"] = on
                        await session._start("gemini-3.8-flash", ws)
                        counts.append(len(spawns))
                    return counts
                finally:
                    await session.close()

            try:
                counts = asyncio.run(scenario())
            finally:
                for p in reversed(patches):
                    p.stop()
        self.assertEqual(counts, [1, 1, 2, 2, 3])


if __name__ == "__main__":
    unittest.main()
