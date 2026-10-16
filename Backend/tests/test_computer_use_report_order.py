"""Batch headers remain readable when a long serialized step list is truncated."""
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
    # At most one fake driver process per test; run_batch always closes it.
    fixture = Path(__file__).parent / "fixtures" / "fake_cua_driver.py"
    executable = tmp_path / "cua-driver"
    executable.write_text(
        "#!" + sys.executable + "\nimport sys, runpy\nsys.dont_write_bytecode = True\n"
        + "runpy.run_path(" + repr(str(fixture)) + ", run_name='__main__')\n")
    executable.chmod(0o700)
    log = tmp_path / "calls.jsonl"
    socket = str(tmp_path / "fake-private-socket")
    driver = DriverClient(str(executable), socket)
    driver.env["FAKE_CUA_LOG"] = str(log)
    monkeypatch.setattr(server, "fetch_runtime", AsyncMock(return_value=(str(executable), socket)))
    monkeypatch.setattr(server.approval_bridge, "request_approval", AsyncMock(return_value={"approved": True}))
    return driver


def run_batch(driver):
    async def run():
        handler = ComputerServer(driver)
        try:
            return await handler.call("computer_run_steps", {
                "pid": 4242,
                "steps": [{"type_text": {"text": str(i)}} for i in range(3)],
                "observe": {},
            })
        finally:
            await handler.close()

    return json.loads(asyncio.run(run()).content[0].text)


def test_unstopped_header_precedes_steps_and_observation(fake, monkeypatch):
    monkeypatch.setattr(server, "_batch_stop", AsyncMock(return_value=None))
    report = run_batch(fake)
    assert list(report) == ["executed", "total", "failed_step", "ok", "steps", "observation"]
    assert report["executed"] == report["total"] == 3
    assert report["failed_step"] is None and report["ok"] is True
    assert len(report["steps"]) == 3


def test_stopped_header_keeps_halt_fields_first(fake, monkeypatch):
    monkeypatch.setattr(server, "_batch_stop", AsyncMock(side_effect=[None, "halted"]))
    report = run_batch(fake)
    assert list(report) == [
        "halted", "stop_reason", "halt_reason", "executed", "total", "failed_step", "ok", "steps",
    ]
    assert report["executed"] == 1 and report["total"] == 3
    assert report["halted"] is True and report["stop_reason"] == "halted"
    assert report["failed_step"] is None and report["ok"] is False
    assert len(report["steps"]) == 1
