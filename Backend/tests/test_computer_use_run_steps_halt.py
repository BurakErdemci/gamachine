"""Batch interruption tests use a local fake driver and mocked heartbeat."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

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
    return driver, log, approval


def steps(count):
    return [{"type_text": {"text": str(i)}} for i in range(count)]


def run_batch(fake, args):
    driver, log, approval = fake

    async def run():
        handler = ComputerServer(driver)
        try:
            return await handler.call("computer_run_steps", {"pid": 4242, **args})
        finally:
            await handler.close()

    result = asyncio.run(run())
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    calls = [row["args"] for row in rows if row["tool"] == "run_steps"]
    approval.assert_awaited_once()
    return result, json.loads(result.content[0].text), calls


@pytest.mark.parametrize("checks,executed", [([None, None, None, "halted"], 3),
                                            ([None, "halted"], 1), (["halted"], 0)])
def test_halt_stops_before_next_step(fake, monkeypatch, checks, executed):
    halted = AsyncMock(side_effect=checks)
    monkeypatch.setattr(server, "_batch_stop", halted)
    batch = steps(5)
    result, report, calls = run_batch(fake, {"steps": batch})
    assert [call["steps"] for call in calls] == [[step] for step in batch[:executed]]
    assert result.is_error
    assert report["executed"] == executed and report["total"] == 5
    assert report["halted"] is True and report["ok"] is False
    assert report["stop_reason"] == "halted"
    assert report["failed_step"] is None
    assert [entry["index"] for entry in report["steps"]] == list(range(executed))
    assert report["halt_reason"] == (
        f"Kullanıcı bilgisayar kullanımını bu tur için durdurdu; {executed}/5 adım çalıştı, "
        "kalanlar çalıştırılmadı. Bu turda bilgisayar araçlarını yeniden deneme.")
    assert halted.await_count == len(checks)


@pytest.mark.parametrize("explicit_tool", [False, True])
def test_failure_stops_without_further_halt_checks(fake, monkeypatch, explicit_tool):
    halted = AsyncMock(return_value=None)
    monkeypatch.setattr(server, "_batch_stop", halted)
    batch = steps(4)
    batch[1]["type_text"]["text"] = "fail-step"
    if explicit_tool:
        batch = [{"tool": "type_text", "args": step["type_text"]} for step in batch]
    result, report, calls = run_batch(fake, {"steps": batch})
    assert [call["steps"] for call in calls] == [[step] for step in batch[:2]]
    assert result.is_error and report["ok"] is False
    assert report["executed"] == 2 and report["total"] == 4
    assert report["failed_step"] == 1
    assert report["steps"][1]["message"] == "failed"
    assert all(entry["tool"] == "type_text" for entry in report["steps"])
    assert "halted" not in report
    assert halted.await_count == 2


def test_defaults_delay_and_final_observation(fake, monkeypatch):
    halted = AsyncMock(return_value=None)
    monkeypatch.setattr(server, "_batch_stop", halted)
    sleep = AsyncMock()
    monkeypatch.setattr(server.asyncio, "sleep", sleep)
    defaults = {"pid": 4242, "window_id": 7, "delivery_mode": "background",
                "foreground_fallback": True}
    batch = steps(3)
    observe = {"include_screenshot": True}
    result, report, calls = run_batch(fake, {
        **defaults, "steps": batch, "delay_ms": 10, "observe": observe})
    assert calls == [{**defaults, "steps": [step], **({"observe": observe} if i == 2 else {})}
                     for i, step in enumerate(batch)]
    assert not result.is_error and report["ok"] is True
    assert report["executed"] == report["total"] == 3
    assert report["failed_step"] is None
    assert [entry["index"] for entry in report["steps"]] == [0, 1, 2]
    assert report["observation"] == {"ok": True, "tool": "get_window_state"}
    assert result.content[1] == ImageContent(type="image", data="aGVsbG8=", mime_type="image/png")
    assert halted.await_count == 3
    approval_params = fake[2].await_args.kwargs["params"]
    assert all(call.args == (approval_params,) for call in halted.await_args_list)
    assert approval_params["window_id"] == 7
    assert sleep.await_count == 2
    assert all(call.args == (0.01,) for call in sleep.await_args_list)


def test_single_step_is_forwarded_without_halt_check(fake, monkeypatch):
    halted = AsyncMock(return_value="halted")
    monkeypatch.setattr(server, "_batch_stop", halted)
    args = {"pid": 4242, "steps": steps(1), "foreground_fallback": False,
            "window_id": 7, "delivery_mode": "background", "observe": {}}
    result, report, calls = run_batch(fake, args)
    assert calls == [args]
    assert not result.is_error and report["ok"] is True
    halted.assert_not_awaited()


@pytest.mark.parametrize("body,status,error,expected", [
    ({"stop": None}, 200, False, None),
    ({"stop": "halted"}, 200, False, "halted"),
    ({"stop": "disabled"}, 200, False, "disabled"),
    ({"stop": None}, 200, True, "unverified"),
    ({"stop": None}, 500, False, "unverified"),
    ({"stop": None}, 302, False, "unverified"),
    ({}, 200, False, "unverified"),
    ([], 200, False, "unverified"),
    (None, 200, False, "unverified"),
    ({"stop": "other"}, 200, False, "unverified"),
    ({"stop": False}, 200, False, "unverified"),
    ({"stop": []}, 200, False, "unverified"),
    ("invalid-json", 200, False, "unverified"),
])
@pytest.mark.parametrize("conversation", ["42", None, "0", "-1", "invalid", "1.5"])
def test_batch_stop_fails_closed(monkeypatch, conversation, body, status, error, expected):
    if conversation is None:
        monkeypatch.delenv("GAMACHINE_CONVERSATION_ID", raising=False)
    else:
        monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", conversation)
    headers = {"X-Test-Approval": "local-only"}
    monkeypatch.setattr(server.approval_bridge, "_get_headers", lambda: headers)
    client_class = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["X-Test-Approval"] == "local-only"
        assert request.extensions["timeout"]["read"] == 5
        if error:
            raise httpx.ConnectError("offline", request=request)
        if body == "invalid-json":
            return httpx.Response(status, content=b"invalid-json")
        return httpx.Response(status, json=body)

    def client(**kwargs):
        return client_class(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(server.httpx, "AsyncClient", client)
    params = {"bundle_id": "com.apple.TextEdit", "app_name": "TextEdit", "pid": 42,
              "window_id": 7, "summary": "not sent"}
    assert asyncio.run(server._batch_stop(params)) == expected
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{server.approval_bridge.BACKEND_URL}/computer-use/batch-step"
    payload = {"params": {key: params[key] for key in ("bundle_id", "app_name", "pid", "window_id")}}
    if conversation == "42":
        payload["conversation_id"] = 42
    assert json.loads(request.content) == payload


@pytest.mark.parametrize("reason,prefix,suffix", [
    ("halted", "Kullanıcı bilgisayar kullanımını bu tur için durdurdu",
     " Bu turda bilgisayar araçlarını yeniden deneme."),
    ("disabled", "Bilgisayar kullanımı kapatıldı", ""),
    ("unverified", "Durdurma durumu doğrulanamadı", ""),
])
@pytest.mark.parametrize("executed", [0, 2])
def test_stop_reason(fake, monkeypatch, reason, prefix, suffix, executed):
    monkeypatch.setattr(server, "_batch_stop", AsyncMock(side_effect=[None] * executed + [reason]))
    result, report, calls = run_batch(fake, {"steps": steps(4)})
    assert len(calls) == executed
    assert result.is_error and report["halted"] is True
    assert report["stop_reason"] == reason and report["executed"] == executed
    assert report["halt_reason"] == (
        f"{prefix}; {executed}/4 adım çalıştı, kalanlar çalıştırılmadı.{suffix}")


def test_delay_precedes_check_except_for_first_step(monkeypatch):
    events = []

    async def stop(params):
        events.append("check")
        return "disabled" if len(events) == 3 else None

    async def sleep(delay):
        assert delay == 0.01
        events.append("sleep")

    driver = AsyncMock()
    driver.call.return_value = CallToolResult(content=[TextContent(
        type="text", text=json.dumps({"ok": True, "steps": [{"ok": True}]}))])
    monkeypatch.setattr(server, "_batch_stop", stop)
    monkeypatch.setattr(server.asyncio, "sleep", sleep)
    asyncio.run(ComputerServer()._run_steps_split(
        driver, {"pid": 4242, "steps": steps(3), "delay_ms": 10}, {}))
    assert events == ["check", "sleep", "check"]
    driver.call.assert_awaited_once()


@pytest.mark.parametrize("report", [None, [], {"steps": []}, {"steps": [None]},
                                  {"steps": [{}, {}]}])
def test_unreadable_step_result_preserves_original_content_and_stops(monkeypatch, report):
    original = [TextContent(type="text", text=json.dumps(report)),
                ImageContent(type="image", data="aGVsbG8=", mime_type="image/png")]
    driver = AsyncMock()
    driver.call.return_value = CallToolResult(content=original)
    halted = AsyncMock(return_value=None)
    monkeypatch.setattr(server, "_batch_stop", halted)
    result = asyncio.run(ComputerServer()._run_steps_split(driver, {"pid": 4242, "steps": steps(3)}, {}))
    assert result.is_error
    assert result.content[0].text == "Adım 1/3 sonucu okunamadı; kalan adımlar çalıştırılmadı."
    assert result.content[1:] == original
    driver.call.assert_awaited_once()
    halted.assert_awaited_once_with({})


@pytest.mark.parametrize("failure", ["result", "report", "entry", None])
def test_structured_report_preferred_and_all_failure_signals_stop(monkeypatch, failure):
    report = {"ok": failure != "report", "steps": [{"ok": failure != "entry", "index": 0}],
              "observation": {"ok": True}}
    image = ImageContent(type="image", data="aGVsbG8=", mime_type="image/png")
    driver = AsyncMock()
    driver.call.return_value = CallToolResult(
        content=[TextContent(type="text", text="not JSON"), image],
        structured_content=report, is_error=failure == "result")
    halted = AsyncMock(return_value=None)
    monkeypatch.setattr(server, "_batch_stop", halted)
    result = asyncio.run(ComputerServer()._run_steps_split(driver, {"pid": 4242, "steps": steps(2)}, {}))
    merged = json.loads(result.content[0].text)
    assert result.is_error is (failure is not None)
    assert merged["executed"] == (1 if failure else 2)
    assert merged["failed_step"] == (0 if failure else None)
    assert merged["steps"][-1]["index"] == (0 if failure else 1)
    assert report["steps"][0]["index"] == 0
    assert merged["observation"] == {"ok": True}
    assert result.content[1:] == [image]
    assert halted.await_count == (1 if failure else 2)
