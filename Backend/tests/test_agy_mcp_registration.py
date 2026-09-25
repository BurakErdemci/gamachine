"""Where agy's unityMCP entry is written (measured against agy 1.2.8, 25 Sep 2026).

agy reads MCP servers only from mcp_config.json; mcpServers in settings.json is
never read, and the entry once written there had no key and answered 401.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from providers import agy_provider
from providers.agy_provider import AgyProvider
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

URL = "http://127.0.0.1:8080/mcp"
STALE = {"serverUrl": URL, "type": "http", "trust": True}


class TestAgyMcpRegistration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        real_expand = os.path.expanduser
        self.patches = [
            patch.object(agy_provider.os.path, "expanduser",
                         side_effect=lambda p: p.replace("~", self.home, 1) if p.startswith("~") else real_expand(p)),
            patch.object(AgyProvider, "_write_cli_env"),
        ]
        for p in self.patches:
            p.start()
        self.paths = {
            "migrated": os.path.join(self.home, ".gemini", "config", "mcp_config.json"),
            "cli_mcp": os.path.join(self.home, ".gemini", "antigravity-cli", "mcp_config.json"),
            "cli_settings": os.path.join(self.home, ".gemini", "antigravity-cli", "settings.json"),
            "global_settings": os.path.join(self.home, ".gemini", "settings.json"),
        }
        for path in self.paths.values():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"mcpServers": {"unityMCP": dict(STALE), "meshy": {"command": "npx"}}}, f)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def servers(self, key):
        with open(self.paths[key], encoding="utf-8") as f:
            return json.load(f)["mcpServers"]

    def register(self, url):
        with patch.object(unity_mcp_manager, "mcp_url", return_value=url):
            AgyProvider()._register_mcp("launcher", self.home, "http://127.0.0.1:1")

    def test_server_on_bridge_in_mcp_config_nothing_in_settings(self):
        self.register(URL)
        for key in ("migrated", "cli_mcp"):
            entry = self.servers(key)["unityMCP"]
            self.assertIn("codex-mcp-bridge", entry["args"])
            self.assertEqual(entry["env"], {"UNITY_MCP_URL": URL})
            self.assertNotIn("headers", entry)
        for key in ("cli_settings", "global_settings"):
            self.assertNotIn("unityMCP", self.servers(key))
        self.assertIn("meshy", self.servers("migrated"))

    def test_server_off_removes_every_unity_entry(self):
        self.register(None)
        for key in self.paths:
            self.assertNotIn("unityMCP", self.servers(key), key)
        self.assertIn("meshy", self.servers("migrated"))


if __name__ == "__main__":
    unittest.main()
