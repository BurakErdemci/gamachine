"""Shared transport and result shaping for the `playtest` tool group.

Private module (leading underscore): tool discovery skips it, the five playtest
wrappers import it.
"""
from __future__ import annotations

import asyncio
import functools
import json
import time
from typing import Any, Callable

from fastmcp import Context

from services.registry.tool_actions import READ, classify, sole_value
from services.tools import get_unity_instance_from_context
from services.tools.utils import parse_json_payload
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

GROUP = "playtest"

# Same poll key the fork's RequiresPolling tools use (McpForUnityToolAttribute.PollAction
# default, custom_tool_service._poll_until_complete).
POLL_ACTION = "status"
_MIN_POLL_S = 0.1
_MAX_POLL_S = 5.0
# The Unity side reads timeout_seconds as its own phase limit (play_session start),
# so polling must outlast it to receive Unity's "phase X timed out" answer.
POLL_SLACK_S = 30.0
# A reply lost to a reload comes back within seconds (plugin_hub waits up to 20 s for
# the plugin to reconnect, a timed-out wait costs 30 s); failing this long means Unity is gone.
TRANSPORT_GAP_S = 60.0

# The only playtest sends Unity may receive twice. Everything else changes game or
# editor state (game_hooks get included: it runs the game's own state getters,
# which may have side effects; action hooks are refused by C#).
RESENDABLE = frozenset({
    ("play_session", "status"),
    ("run_playtest", "status"),
    ("game_hooks", "list"),
})
PLAYTEST_COMMANDS = frozenset({"game_hooks", "play_session", "play_step", "play_capture", "run_playtest"})

# Module-level aliases so tests can fake the clock without patching asyncio's own.
monotonic = time.monotonic
sleep = asyncio.sleep


def _as_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return {"success": False, "error": str(result)}


def _send_fn(command: str, params: dict[str, Any]) -> Callable:
    """The legacy stdio sender, told not to resend unless the command is resendable.

    Its default resends the same command after a "reloading" reply, although Unity
    may already have acted on the first one. Only the legacy path calls this
    function; the HTTP path never resends after sending, and its pre-send wait for
    the plugin to reconnect (what retry_on_reload means there) is left as it was.
    """
    if is_resendable(command, params):
        return async_send_command_with_retry
    return functools.partial(async_send_command_with_retry, retry_on_reload=False)


def is_resendable(command: str, params: dict[str, Any]) -> bool:
    """May Unity receive this command twice? Playtest commands only when in
    RESENDABLE; any other command only when the approval ledger proves it a read."""
    if command in PLAYTEST_COMMANDS:
        return (command, sole_value(params, "action")) in RESENDABLE
    return classify(command, params) == READ


async def send(ctx: Context, command: str, params: dict[str, Any]) -> dict[str, Any]:
    unity_instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        _send_fn(command, params), unity_instance, command, params
    )
    return _as_dict(result)


def is_pending(response: dict[str, Any]) -> bool:
    return response.get("_mcp_status") == "pending"


def is_transport_failure(response: dict[str, Any]) -> bool:
    """The reply never arrived: disconnect (domain reload), no session, server-side wait timeout.

    Every such path in the transport answers success=false with hint="retry"
    (plugin_hub.send_command, unity_transport.send_with_unity_instance). A C#
    ErrorResponse never carries a hint, so a real Unity answer such as "phase X timed
    out" is not mistaken for one.
    """
    return response.get("success") is False and response.get("hint") == "retry"


async def send_or_transport_failure(ctx: Context, command: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return await send(ctx, command, params)
    except Exception as exc:  # the legacy transport raises where HTTP answers with a hint
        return {"success": False, "error": str(exc) or type(exc).__name__, "hint": "retry"}


def _poll_interval(pending: dict[str, Any]) -> float:
    try:
        interval = float(pending.get("_mcp_poll_interval", 1.0))
    except (TypeError, ValueError):
        interval = 1.0
    return max(_MIN_POLL_S, min(interval, _MAX_POLL_S))


async def send_long(
    ctx: Context,
    command: str,
    params: dict[str, Any],
    *,
    timeout_s: float,
    unity_timeout_s: float | None = None,
    confirm_after_loss: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Send a job-style command (Unity answers pending) and poll action="status" to the end.

    The first call is sent exactly once: start/stop/run are not idempotent, so a
    lost reply is never re-sent - the outcome is learned from `status` instead.
    `status` is idempotent and is re-sent after a transport failure (a reply lost
    while a scenario enters or leaves play mode) until the overall deadline.

    When any reply was lost, the final answer may be a plain status rather than
    the command's own outcome (play_session hands its outcome over once, and a
    command that never reached Unity leaves no outcome at all), so
    `confirm_after_loss` turns such an answer into a success or an error.

    `unity_timeout_s` is sent as timeout_seconds only where Unity reads it
    (play_session start phase limit); it also widens the transport wait.
    """
    budget_s = timeout_s + POLL_SLACK_S
    deadline = monotonic() + budget_s
    first = dict(params)
    if unity_timeout_s is not None:
        first["timeout_seconds"] = unity_timeout_s
    response = await send_or_transport_failure(ctx, command, first)
    initial_lost = is_transport_failure(response)
    if not initial_lost and not is_pending(response):
        return response

    # Both C# status handlers read nothing but the action.
    poll = {"action": POLL_ACTION}
    last_pending = response if is_pending(response) else {}
    retries = 0
    gap_started = monotonic() if initial_lost else None
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            result = {
                "success": False,
                "error": (
                    f"{command} still pending after {budget_s:.0f}s; "
                    f"last Unity message: {last_pending.get('message') or '-'}"
                ),
                "data": last_pending.get("data"),
            }
            break
        if gap_started is not None and monotonic() - gap_started > TRANSPORT_GAP_S:
            result = {
                "success": False,
                "error": (
                    f"{command}: no reply from Unity for {TRANSPORT_GAP_S:.0f}s "
                    f"(last transport error: {response.get('error') or '-'}); "
                    f"the command may still be running, call it with action='{POLL_ACTION}'."
                ),
            }
            break
        await sleep(min(_poll_interval(last_pending), remaining))
        response = await send_or_transport_failure(ctx, command, poll)
        if is_transport_failure(response):
            retries += 1
            if gap_started is None:
                gap_started = monotonic()
            continue
        gap_started = None
        if is_pending(response):
            last_pending = response
            continue
        result = response
        if (initial_lost or retries) and confirm_after_loss is not None:
            result = confirm_after_loss(result)
        break

    if retries or initial_lost:
        result = dict(result)
        result["transport_retries"] = retries
        if initial_lost:
            result["initial_reply_lost"] = True
    return result


def error(message: str) -> dict[str, Any]:
    # Same failure shape as the fork's C# ErrorResponse: the text lives in `error`.
    return {"success": False, "error": message}


def parse_list(value: Any, name: str) -> tuple[list | None, str | None]:
    value = parse_json_payload(value)
    if value is None:
        return None, None
    if not isinstance(value, list):
        return None, f"{name} must be a list, got {type(value).__name__}."
    return value, None


def parse_object(value: Any, name: str) -> tuple[dict | None, str | None]:
    value = parse_json_payload(value)
    if value is None:
        return None, None
    if not isinstance(value, dict):
        return None, f"{name} must be an object, got {type(value).__name__}."
    return value, None


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def string_list(value: Any, name: str) -> tuple[list[str] | None, str | None]:
    """A list of non-empty strings; a bare string is one element."""
    value = parse_json_payload(value)
    if value is None:
        return None, None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        return None, f"{name} must be a list of non-empty strings."
    return [v.strip() for v in value], None


def _sniff_mime(b64: str) -> str:
    if b64.startswith("iVBOR"):
        return "image/png"
    return "image/jpeg"


def shape_image_result(response: dict[str, Any], image_key: str | None = None) -> Any:
    """Turn image_base64 into one MCP ImageContent plus compact JSON text.

    The image fields sit in `data` (play_capture) or in `data[image_key]`
    (play_step's `image`). The base64 never reaches the text part, where it would
    be paid as text tokens instead of as an image.
    """
    data = response.get("data")
    if not isinstance(data, dict):
        return response
    fields = data.get(image_key) if image_key else data
    if not isinstance(fields, dict) or not fields.get("image_base64"):
        return response

    from fastmcp.tools import ToolResult
    from mcp.types import ImageContent, TextContent

    image_b64 = fields["image_base64"]
    mime = fields.get("mime")
    if not isinstance(mime, str) or not mime:
        mime = _sniff_mime(image_b64)
    stripped = {k: v for k, v in fields.items() if k != "image_base64"}
    text_data = {**data, image_key: stripped} if image_key else stripped
    text = {k: v for k, v in response.items() if k != "data"}
    text["data"] = text_data
    return ToolResult(
        content=[
            TextContent(type="text", text=json.dumps(text, separators=(",", ":"), ensure_ascii=False)),
            ImageContent(type="image", data=image_b64, mimeType=mime),
        ],
    )
