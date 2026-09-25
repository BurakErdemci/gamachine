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
            # agy's own keys are blanked for the child (agy passes its whole env).
            self.assertEqual(entry["env"], {"UNITY_MCP_URL": URL, "GEMINI_API_KEY": "",
                                            "GOOGLE_API_KEY": "", "GOOGLE_APPLICATION_CREDENTIALS": ""})
            self.assertNotIn("headers", entry)
            unityai_env = self.servers(key)["unityai"]["env"]
            for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
                self.assertEqual(unityai_env[name], "")
        for key in ("cli_settings", "global_settings"):
            self.assertNotIn("unityMCP", self.servers(key))
        self.assertIn("meshy", self.servers("migrated"))

    def test_server_off_removes_every_unity_entry(self):
        self.register(None)
        for key in self.paths:
            self.assertNotIn("unityMCP", self.servers(key), key)
        self.assertIn("meshy", self.servers("migrated"))


class TestUnusableConfigIsLeftAlone(unittest.TestCase):
    """Audit 25 Sep 2026: an unparseable file was read as {} and rewritten,
    dropping the user's own entries (meshy, playwright, trustedWorkspaces)."""

    # Unusable for every writer.
    BAD = {
        "invalid_json": b'{"mcpServers": {"own": {"command": "keep"}}, invalid}',
        "cp1254": '{"mcpServers": {"own": {"command": "özel"}}}'.encode("cp1254"),
        "array": b'[{"mcpServers": {"own": {"command": "keep"}}}]',
    }
    # Unusable only for the writer that has to reshape that field.
    SERVERS_NOT_OBJECT = b'{"mcpServers": ["own"], "trustedWorkspaces": ["C:/x"]}'
    TRUSTED_NOT_LIST = b'{"mcpServers": {}, "trustedWorkspaces": "C:/x"}'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        real_expand = os.path.expanduser
        self.patches = [
            patch.object(agy_provider.os.path, "expanduser",
                         side_effect=lambda p: p.replace("~", self.home, 1) if p.startswith("~") else real_expand(p)),
            patch.object(AgyProvider, "_write_cli_env"),
            patch.object(unity_mcp_manager, "mcp_url", return_value=URL),
        ]
        for p in self.patches:
            p.start()
        self.paths = [os.path.join(self.home, ".gemini", *rel) for rel in (
            ("config", "mcp_config.json"), ("antigravity-cli", "mcp_config.json"),
            ("antigravity-cli", "settings.json"), ("settings.json",))]
        for path in self.paths:
            os.makedirs(os.path.dirname(path), exist_ok=True)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def leftovers(self):
        return [name for path in self.paths for name in os.listdir(os.path.dirname(path))
                if name.endswith(".tmp")]

    def test_every_writer_leaves_an_unusable_file_byte_identical(self):
        for case, raw in self.BAD.items():
            with self.subTest(case=case):
                for path in self.paths:
                    with open(path, "wb") as f:
                        f.write(raw)
                AgyProvider()._register_mcp("launcher", self.home, "http://127.0.0.1:1")
                AgyProvider()._set_agy_model("Gemini 3.8 Flash (High)", self.home)
                for path in self.paths:
                    with open(path, "rb") as f:
                        self.assertEqual(f.read(), raw, path)
                self.assertEqual(self.leftovers(), [])

    def test_a_field_of_the_wrong_type_is_not_reshaped(self):
        for path in self.paths:
            with open(path, "wb") as f:
                f.write(self.SERVERS_NOT_OBJECT)
        AgyProvider()._register_mcp("launcher", self.home, "http://127.0.0.1:1")
        for path in self.paths:
            with open(path, "rb") as f:
                self.assertEqual(f.read(), self.SERVERS_NOT_OBJECT, path)
        for path in self.paths[2:]:
            with open(path, "wb") as f:
                f.write(self.TRUSTED_NOT_LIST)
        AgyProvider()._set_agy_model("Gemini 3.8 Flash (High)", self.home)
        for path in self.paths[2:]:
            with open(path, "rb") as f:
                self.assertEqual(f.read(), self.TRUSTED_NOT_LIST, path)

    def test_bom_and_empty_files_are_read_not_skipped(self):
        path = self.paths[0]
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbf" + json.dumps({"mcpServers": {"meshy": {"command": "npx"}}}).encode())
        with open(self.paths[3], "wb") as f:
            f.write(b"  \n")
        AgyProvider()._register_mcp("launcher", self.home, "http://127.0.0.1:1")
        with open(path, encoding="utf-8") as f:
            servers = json.load(f)["mcpServers"]
        self.assertEqual(set(servers), {"meshy", "unityai", "unityMCP"})
        with open(self.paths[3], encoding="utf-8") as f:
            self.assertIn("unityai", json.load(f)["mcpServers"])

    def test_write_is_atomic_and_a_failed_replace_keeps_the_original(self):
        path = self.paths[2]
        original = json.dumps({"trustedWorkspaces": ["C:/mine"], "colorScheme": "light"}).encode()
        with open(path, "wb") as f:
            f.write(original)
        with patch.object(agy_provider.os, "replace", side_effect=PermissionError("locked")):
            AgyProvider()._set_agy_model("Gemini 3.8 Flash (High)", self.home)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), original)
        self.assertEqual(self.leftovers(), [])
        AgyProvider()._set_agy_model("Gemini 3.8 Flash (High)", self.home)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["trustedWorkspaces"], ["C:/mine", self.home])
        self.assertEqual(data["colorScheme"], "light")

    def test_a_symlinked_config_is_written_through(self):
        real = os.path.join(self.home, "dotfiles", "settings.json")
        os.makedirs(os.path.dirname(real))
        with open(real, "w", encoding="utf-8") as f:
            json.dump({"own": 1}, f)
        try:
            os.symlink(real, self.paths[3])
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need developer mode or admin on Windows")
        AgyProvider()._set_agy_model("Gemini 3.8 Flash (High)", self.home)
        self.assertTrue(os.path.islink(self.paths[3]))
        with open(real, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["own"], 1)


class TestAgySettingsEncoding(unittest.TestCase):
    """agy writes its settings as UTF-8; a locale-encoded read (cp1252 on this
    machine) turned a Turkish workspace path into mojibake that grew on every
    agy round trip (seen in a real antigravity-cli/settings.json)."""

    def test_non_ascii_trusted_workspace_survives_a_round_trip(self):
        with tempfile.TemporaryDirectory() as home:
            real_expand = os.path.expanduser
            path = os.path.join(home, ".gemini", "antigravity-cli", "settings.json")
            os.makedirs(os.path.dirname(path))
            workspace = os.path.join("C:", "Unity Projeler", "Körebe")
            with open(path, "w", encoding="utf-8") as f:
                # Raw UTF-8 bytes, as agy writes them.
                json.dump({"trustedWorkspaces": [workspace]}, f, ensure_ascii=False)
            with patch.object(agy_provider.os.path, "expanduser",
                              side_effect=lambda p: p.replace("~", home, 1) if p.startswith("~") else real_expand(p)):
                AgyProvider()._set_agy_model("Gemini 3.8 Flash (High)", workspace)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["trustedWorkspaces"], [workspace])


if __name__ == "__main__":
    unittest.main()
