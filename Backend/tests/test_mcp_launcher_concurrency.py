"""run_mcp_server.cmd must start while another unityai server is running.

Measured 27 Sep 2026: the launcher redirected stderr with `2>> mcp_server.log`;
cmd.exe opens that file without write sharing, so while one unityai server ran
(a long agy or Codex session) every new launch died at the redirection before
Python started. OpenCode showed "MCP error -32000: Connection closed" and an
agy branch lost its mail tools. These tests start the real launcher (our own
MCP server only, no agent CLI, no model API) with the log pointed at a temp file.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe launcher")

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAUNCHER = os.path.join(BACKEND, "run_mcp_server.cmd")
_DEADLINE_S = 60.0


def _env(tmp_path, log_path):
    env = dict(os.environ)
    env["UNITYAI_MCP_LOG_FILE"] = str(log_path)
    # Nothing listens on port 9: a stray backend call fails fast instead of
    # reaching the live app.
    env["UNITYAI_URL"] = "http://127.0.0.1:9"
    env.pop("GAMACHINE_CONVERSATION_ID", None)
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    return env


class _Server:
    def __init__(self, tmp_path, log_path):
        self.proc = subprocess.Popen(
            ["cmd.exe", "/d", "/c", LAUNCHER, "--workspace", str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_env(tmp_path, log_path), cwd=str(tmp_path))
        self.lines: queue.Queue = queue.Queue()
        self.stderr = b""
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._drain_err, daemon=True).start()

    def _pump(self):
        for raw in self.proc.stdout:
            self.lines.put(raw)
        self.lines.put(None)

    def _drain_err(self):
        self.stderr = self.proc.stderr.read()

    def send(self, message: dict):
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def reply(self, request_id: int):
        deadline = time.monotonic() + _DEADLINE_S
        while time.monotonic() < deadline:
            try:
                raw = self.lines.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                break
            if raw is None:
                break
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if message.get("id") == request_id:
                return message
        return None

    def handshake(self):
        self.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "launcher-test", "version": "0"}}})
        init = self.reply(1)
        if init is None:
            return None, None
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        return init, self.reply(2)

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            # Only this test's own process tree.
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                           capture_output=True)
            self.proc.wait(timeout=15)


def _tool_names(tools_reply):
    return {t["name"] for t in (tools_reply or {}).get("result", {}).get("tools", [])}


def test_two_launchers_run_at_the_same_time(tmp_path):
    log_path = tmp_path / "mcp_server.log"
    first = _Server(tmp_path, log_path)
    second = None
    try:
        init1, tools1 = first.handshake()
        assert init1 is not None, first.stderr.decode("utf-8", "replace")
        # The first server is alive and holds its log; the second starts now.
        second = _Server(tmp_path, log_path)
        init2, tools2 = second.handshake()
        assert first.proc.poll() is None
        assert init2 is not None, (
            "second launcher never answered initialize: "
            + second.stderr.decode("utf-8", "replace"))
        assert init2["result"]["serverInfo"]["name"] == "unityai"
        for names in (_tool_names(tools1), _tool_names(tools2)):
            assert {"send_chat_message", "list_chats", "save_file"} <= names
        # Both servers log into the one file while both are running: the
        # unreachable backend makes list_chats log a warning.
        for server in (first, second):
            server.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                         "params": {"name": "list_chats", "arguments": {}}})
        assert first.reply(3) is not None and second.reply(3) is not None
    finally:
        first.close()
        if second is not None:
            second.close()
    log_text = log_path.read_text(encoding="utf-8")
    assert log_text.count("[mailbox] list failed") == 2, log_text


def test_launcher_starts_while_an_old_launcher_holds_the_log(tmp_path):
    """The live app keeps old-launcher servers until restart; they hold the log
    without write sharing. A new server must still start (stderr fallback)."""
    log_path = tmp_path / "mcp_server.log"
    holder_cmd = tmp_path / "old_style_holder.cmd"
    holder_cmd.write_text(
        '@echo off\r\nping -n 120 127.0.0.1 >nul 2>> "%~dp0mcp_server.log"\r\n',
        encoding="ascii")
    holder = subprocess.Popen(["cmd.exe", "/d", "/c", str(holder_cmd)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, cwd=str(tmp_path))
    server = None
    try:
        deadline = time.monotonic() + 20
        held = False
        while time.monotonic() < deadline and not held:
            try:
                with open(log_path, "a"):
                    pass
            except PermissionError:
                held = True
            else:
                time.sleep(0.1)
        assert held, "control failed: the cmd.exe redirection did not lock the log"

        server = _Server(tmp_path, log_path)
        init, tools = server.handshake()
        assert init is not None, server.stderr.decode("utf-8", "replace")
        assert "send_chat_message" in _tool_names(tools)
    finally:
        if server is not None:
            server.close()
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(holder.pid)],
                       capture_output=True)
        holder.wait(timeout=15)
