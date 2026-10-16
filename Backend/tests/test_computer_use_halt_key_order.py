"""Keep batch stop markers ahead of step details in serialized reports."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from computer_use import server
from computer_use.driver_client import DriverClient
from computer_use.server import ComputerServer


@pytest.fixture
def fake(tmp_path, monkeypatch):
    # Each test starts at most one fake driver process and closes it afterward.
    fixture = Path(__file__).parent / "fixtures" / "fake_cua_driver.py"
    executable = tmp_path / "cua-driver"
    executable.write_text(
        "#!" + sys.executable + "\nimport sys, runpy\nsys.dont_write_bytecode = True\n"
        + "runpy.run_path(" + repr(str(fixture)) + ", run_name='__main__')\n")
    executable.chmod(0o700)
    log = tmp_path / "calls.jsonl"
    socket = str(tmp_path / "fake-private-socket")
    driver = DriverClient(str(executable), socket)
    monkeypatch.setattr(server, "fetch_runtime", AsyncMock(return_value=(str(executable), socket)))
    driver.env["FAKE_CUA_LOG"] = str(log)
    approval = AsyncMock(return_value={"approved": True})
    monkeypatch.setattr(server.approval_bridge, "request_approval", approval)
    return driver, approval


@pytest.mark.parametrize("reason", ["halted", "disabled", "unverified", None])
def test_batch_report_key_order(fake, monkeypatch, reason):
    driver, approval = fake
    monkeypatch.setattr(server, "_batch_stop", AsyncMock(side_effect=[None, reason]))

    async def run():
        handler = ComputerServer(driver)
        try:
            return await handler.call("computer_run_steps", {
                "pid": 4242,
                "steps": [{"type_text": {"text": str(i)}} for i in range(2)],
            })
        finally:
            await handler.close()

    result = asyncio.run(run())
    approval.assert_awaited_once()
    report = json.loads(result.content[0].text)
    existing_keys = ["executed", "total", "failed_step", "ok", "steps"]
    if reason is not None:
        assert list(report)[:3] == ["halted", "stop_reason", "halt_reason"]
        assert list(report)[3:] == existing_keys
        assert report["stop_reason"] == reason
        assert report["executed"] == 1
        assert result.is_error
    else:
        assert list(report)[0] == "executed"
        assert list(report) == existing_keys
        assert report["executed"] == 2
        assert not result.is_error
