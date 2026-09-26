"""Codex app-server oturumu ve onay protokolü regresyon testleri."""
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from providers.codex_session import (
    CodexSession,
    _APP_SERVER_STREAM_LIMIT,
    _trusted_mcp_config,
)


class TestProtocolLimits(unittest.TestCase):
    def test_stream_limit_handles_large_unity_tool_schemas(self):
        self.assertGreater(_APP_SERVER_STREAM_LIMIT, 64 * 1024)
        self.assertLessEqual(_APP_SERVER_STREAM_LIMIT, 8 * 1024 * 1024)


class TestTrustedMcpConfig(unittest.TestCase):
    def _manager_modules(self, running: bool):
        manager_module = MagicMock()
        manager_module.unity_mcp_manager.is_running.return_value = running
        package = MagicMock()
        package.unity_mcp_manager = manager_module.unity_mcp_manager
        return patch.dict(
            sys.modules,
            {
                "unity_ai_mcp": package,
                "unity_ai_mcp.unity_mcp_manager": manager_module,
            },
        )

    def test_unityai_is_approved_at_codex_layer(self):
        with self._manager_modules(running=False), patch(
            "providers.codex_session._configured_codex_mcp_names",
            return_value={"unityai"},
        ):
            config = _trusted_mcp_config()

        self.assertEqual(
            config["mcp_servers"]["unityai"]["default_tools_approval_mode"],
            "approve",
        )
        self.assertNotIn("unityMCP", config["mcp_servers"])

    def test_running_unity_mcp_is_approved_at_codex_layer(self):
        with self._manager_modules(running=True), patch(
            "providers.codex_session._configured_codex_mcp_names",
            return_value={"unityai", "unityMCP"},
        ):
            config = _trusted_mcp_config()

        self.assertEqual(
            config["mcp_servers"]["unityMCP"]["default_tools_approval_mode"],
            "approve",
        )

    def test_unregistered_unity_mcp_does_not_break_thread_config(self):
        with self._manager_modules(running=True), patch(
            "providers.codex_session._configured_codex_mcp_names",
            return_value={"unityai"},
        ):
            config = _trusted_mcp_config()

        self.assertNotIn("unityMCP", config["mcp_servers"])

    def test_forwarded_env_goes_to_registered_servers_only(self):
        # unityMCP registered but the server is down: it still gets the name,
        # so a bridge that connects later names the chat too.
        with self._manager_modules(running=False), patch(
            "providers.codex_session._configured_codex_mcp_names",
            return_value={"unityMCP", "someone_elses"},
        ):
            config = _trusted_mcp_config(forward_env=("GAMACHINE_CONVERSATION_ID",))

        self.assertEqual(config["mcp_servers"], {
            "unityMCP": {"env_vars": ["GAMACHINE_CONVERSATION_ID"]},
        })

    def test_no_forwarded_env_adds_no_env_vars(self):
        with self._manager_modules(running=True), patch(
            "providers.codex_session._configured_codex_mcp_names",
            return_value={"unityai", "unityMCP"},
        ):
            config = _trusted_mcp_config()

        for entry in config["mcp_servers"].values():
            self.assertNotIn("env_vars", entry)


class TestCodexSessionNamesItsChat(unittest.IsolatedAsyncioTestCase):
    """Codex hands a stdio MCP child only its default env + `env` + `env_vars`
    (measured, 0.157.0), so the id must be in the app-server's env AND named in
    the thread config, or neither bridge sees it."""

    async def _start(self, conversation_id):
        from providers import codex_session as cs

        spawned = {}
        requests = []

        async def fake_spawn(*argv, **kwargs):
            spawned.update(argv=argv, env=kwargs["env"])
            return MagicMock()

        async def fake_request(method, params=None, timeout=None):
            requests.append((method, params))
            if method == "thread/start":
                return {"result": {"thread": {"id": "t1"}, "approvalsReviewer": "user"}}
            return {"result": {}}

        async def no_read_loop():
            return None

        manager = MagicMock()
        manager.unity_mcp_manager.is_running.return_value = True
        session = CodexSession(conversation_id)
        with patch.object(cs.asyncio, "create_subprocess_exec", side_effect=fake_spawn), \
                patch.object(session, "_request", side_effect=fake_request), \
                patch.object(session, "_notify", AsyncMock()), \
                patch.object(session, "_read_loop", side_effect=no_read_loop), \
                patch.object(cs, "_configured_codex_mcp_names",
                             return_value={"unityai", "unityMCP"}), \
                patch.dict(sys.modules, {"unity_ai_mcp.unity_mcp_manager": manager}), \
                patch.dict(os.environ, {"GAMACHINE_CONVERSATION_ID": "999"}):
            await session.start()
        thread_config = dict(requests)["thread/start"]["config"]
        return spawned["env"], thread_config

    async def test_a_chat_session_passes_its_id_to_both_servers(self):
        env, config = await self._start(7)
        self.assertEqual(env["GAMACHINE_CONVERSATION_ID"], "7")
        for name in ("unityai", "unityMCP"):
            self.assertEqual(config["mcp_servers"][name], {
                "default_tools_approval_mode": "approve",
                "env_vars": ["GAMACHINE_CONVERSATION_ID"],
            })

    async def test_throwaway_ids_name_no_chat(self):
        # The backend's own env must not leak through either (999 is set).
        for conversation_id in (0, -3):
            env, config = await self._start(conversation_id)
            self.assertNotIn("GAMACHINE_CONVERSATION_ID", env)
            for entry in config["mcp_servers"].values():
                self.assertNotIn("env_vars", entry)


class TestCodexApprovalResponses(unittest.IsolatedAsyncioTestCase):
    async def test_client_request_omits_jsonrpc_wire_field(self):
        session = CodexSession(0)
        sent = []

        async def fake_send(message):
            sent.append(message)
            session._pending[message["id"]].set_result({
                "id": message["id"],
                "result": {"ok": True},
            })

        session._send = fake_send

        response = await session._request("config/read", {})

        self.assertEqual(response["result"], {"ok": True})
        self.assertNotIn("jsonrpc", sent[0])
        self.assertEqual(sent[0]["method"], "config/read")

    async def test_client_notification_omits_jsonrpc_wire_field(self):
        session = CodexSession(0)
        session._send = AsyncMock()

        await session._notify("initialized")

        session._send.assert_awaited_once_with({"method": "initialized"})

    async def test_permissions_approval_uses_current_appserver_schema(self):
        session = CodexSession(1)
        requested = {
            "fileSystem": {"read": ["/project"]},
            "network": {"enabled": False},
        }
        session._resolve_approval = AsyncMock(return_value="accept")
        session._send = AsyncMock()

        await session._handle_server_request({
            "id": 7,
            "method": "item/permissions/requestApproval",
            "params": {"permissions": requested, "reason": "MCP tool call"},
        })

        session._send.assert_awaited_once_with({
            "id": 7,
            "result": {
                "permissions": requested,
                "scope": "turn",
            },
        })

    async def test_permissions_rejection_grants_nothing(self):
        session = CodexSession(2)
        session._resolve_approval = AsyncMock(return_value="decline")
        session._send = AsyncMock()

        await session._handle_server_request({
            "id": 8,
            "method": "item/permissions/requestApproval",
            "params": {"permissions": {"network": {"enabled": True}}},
        })

        session._send.assert_awaited_once_with({
            "id": 8,
            "result": {
                "permissions": {},
                "scope": "turn",
            },
        })

    async def test_command_approval_keeps_decision_response(self):
        session = CodexSession(3)
        session._resolve_approval = AsyncMock(return_value="accept")
        session._send = AsyncMock()

        await session._handle_server_request({
            "id": 9,
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "git status"},
        })

        session._send.assert_awaited_once_with({
            "id": 9,
            "result": {"decision": "accept"},
        })

    async def test_auto_mode_approval_never_creates_a_ui_gate(self):
        session = CodexSession(4, auto_approve=True)
        session._out_q = __import__("asyncio").Queue()

        decision = await session._resolve_approval(
            "item/commandExecution/requestApproval",
            {"command": "touch Assets/test.txt"},
        )

        self.assertEqual(decision, "accept")
        self.assertTrue(session._out_q.empty())

    async def test_auto_mode_structured_question_continues_without_prompting_user(self):
        session = CodexSession(5, auto_approve=True)
        session._send = AsyncMock()

        await session._handle_server_request({
            "id": 10,
            "method": "item/tool/requestUserInput",
            "params": {"question": "Should I continue?"},
        })

        session._send.assert_awaited_once_with({
            "id": 10,
            "result": {
                "value": (
                    "Proceed using your best judgment without asking for confirmation."
                ),
            },
        })
