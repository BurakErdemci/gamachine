"""The backend's agent-browser gateway: the only way a browser command reaches Electron's host.

POST /browser/command hands every agent-browser tool call here. Navigation is checked, critical
steps are described and classified, and a critical step opens an approval card and returns at
once unless the card is answered within APPROVAL_WAIT_S; the card's one-time grant lets the
identical retry through (see docs/agent-browser.md).
"""
import asyncio
import hashlib
import json
import time
from urllib.parse import urlsplit

import httpx

from . import policy

APPROVAL_WAIT_S = 2.0
_POLL_S = 0.05
_CMD_TIMEOUT_S = 90.0
_PREVIEW_LIMIT = 80
NEEDS_APPROVAL = (
    "needs_approval: {reason}. An approval card is open in the chat. Tell the user what you want "
    "to do and why, then wait for their next message before trying again."
)
HALTED = ("The user stopped the browser for this turn. Do not use browser tools "
          "again until the user's next message.")
_ELEMENT_OPS = frozenset({"click", "fill", "select"})
# The host command contract: what each op may forward. Anything else in args is dropped.
_ARGS = {
    "navigate": ("url",), "snapshot": ("max_chars",),
    "click": ("snapshot_id", "ref"), "fill": ("snapshot_id", "ref", "value"),
    "select": ("snapshot_id", "ref", "option"), "press": ("key",),
    "scroll": ("direction", "amount"), "back": (), "forward": (), "reload": (), "screenshot": (),
}
_TEXT_ARG = {"fill": "value", "select": "option", "press": "key"}


async def post_cmd(runtime: dict, op: str, conversation_id: int, args: dict) -> dict:
    async with httpx.AsyncClient(timeout=_CMD_TIMEOUT_S) as client:
        response = await client.post(
            f"http://127.0.0.1:{runtime['port']}/cmd",
            headers={"Authorization": f"Bearer {runtime['token']}"},
            json={"op": op, "conversation_id": conversation_id, "args": args})
        response.raise_for_status()
        return response.json()


def _refusal(text, **extra) -> dict:
    return {"ok": False, "error": text, **extra}


def _host_of(url) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _has_target(args) -> bool:
    ref = args.get("ref")
    return (isinstance(args.get("snapshot_id"), str) and bool(args["snapshot_id"])
            and (isinstance(ref, str) or type(ref) is int))


def _valid_args(op, args) -> bool:
    if op in _ELEMENT_OPS and not _has_target(args):
        return False
    return op not in _TEXT_ARG or isinstance(args.get(_TEXT_ARG[op]), str)


def _usable(state) -> tuple:
    """(runtime, refusal): where the host listens, or why the browser cannot be used."""
    if not isinstance(state, dict) or state.get("enabled") is not True:
        return None, "The agent browser is turned off in Settings."
    if state.get("halted"):
        return None, HALTED
    runtime = state.get("runtime")
    if (not isinstance(runtime, dict) or runtime.get("running") is not True
            or type(runtime.get("port")) is not int or not isinstance(runtime.get("token"), str)):
        return None, ("The agent browser is not running (the app has not started it, or the "
                      "backend runs in Docker, which is not supported).")
    return runtime, None


class BrowserGateway:
    """host_transport(runtime, op, conversation_id, args) -> host reply;
    open_card(conversation_id, tool, params) -> the card decision ({"status": "resolved",
    "approved", "error"?} or {"status": "ok", "gate_id"}); wait_card(gate_id) -> the card's
    result once answered, else None; now() -> monotonic seconds."""

    def __init__(self, host_transport, open_card, wait_card, now=time.monotonic):
        self._host = host_transport
        self._open_card = open_card
        self._wait_card = wait_card
        self._now = now
        # One command at a time, like the host's own queue: a step's describe must see its own page.
        self._lock = asyncio.Lock()

    async def _cmd(self, runtime, conversation_id, op, args):
        try:
            reply = await self._host(runtime, op, conversation_id, args)
        except Exception:
            return {"ok": False, "error": "The browser did not answer."}
        return reply if isinstance(reply, dict) else {"ok": False, "error": "The browser sent an invalid reply."}

    async def _card_result(self, decision):
        if not isinstance(decision, dict):
            return None
        if decision.get("status") == "resolved":
            return decision
        deadline = self._now() + APPROVAL_WAIT_S
        while True:
            result = self._wait_card(decision.get("gate_id"))
            if result is not None or self._now() >= deadline:
                return result
            await asyncio.sleep(_POLL_S)

    async def _gate(self, runtime, conversation_id, op, args):
        """Refusal (dict) for a step that needs a card, or None when it may run."""
        if op == "press":
            if args["key"].casefold() != "enter":
                return None
            # The host sends the key to whatever has focus, so that is what gets described.
            target = {"focused": True}
        else:
            target = {"snapshot_id": args["snapshot_id"], "ref": args["ref"]}
        element, url = None, ""
        described = await self._cmd(runtime, conversation_id, "describe", target)
        if described.get("ok") is not True:
            if op != "press":
                return _refusal(str(described.get("error") or "The element could not be described."))
        else:
            element, url = described.get("element"), str(described.get("url") or "")
        reason = (policy.critical_reason(op, element) if element is not None
                  else "Enter was pressed with no known target, so it could submit a form")
        if not reason:
            return None
        host = _host_of(url)
        shown = f"{reason} (page {host})" if host else reason
        element = element if isinstance(element, dict) else {}
        preview = ""
        if op in {"fill", "select"}:
            preview = "••••" if element.get("is_password") is True else policy.display_name(
                args[_TEXT_ARG[op]], _PREVIEW_LIMIT)
        # The exact string the step will send, so a card approved for one value cannot cover another.
        value_hash = hashlib.sha256(args[_TEXT_ARG[op]].encode("utf-8")).hexdigest() if op in _TEXT_ARG else ""
        result = await self._card_result(self._open_card(conversation_id, f"browser_{op}", {
            "element_name": policy.display_name(element.get("name")), "page_url": url,
            "value_preview": preview, "risky": True, "risk_reason": shown,
            # Not shown: the backend keys the one-time grant on it.
            "fingerprint": policy.fingerprint(element), "value_hash": value_hash,
        }))
        if result is None:
            return _refusal(NEEDS_APPROVAL.format(reason=shown), needs_approval=True)
        if not isinstance(result, dict) or result.get("approved") is not True:
            error = result.get("error") if isinstance(result, dict) else None
            return _refusal(str(error or "The user did not approve this step."))
        return None

    async def call(self, conversation_id: int, op: str, args, state) -> dict:
        """Run one tool call for a verified chat; state() -> {enabled, halted, runtime}, read under the lock."""
        if op not in _ARGS:
            return _refusal(f"Unknown browser tool browser_{op}.")
        if not isinstance(args, dict) or not _valid_args(op, args):
            return _refusal(f"Invalid arguments for browser_{op}.")
        async with self._lock:
            runtime, refusal = _usable(state())
            if refusal:
                return _refusal(refusal)
            if op == "navigate":
                refusal = policy.navigation_refusal(args.get("url"))
                if refusal:
                    return _refusal(f"Refused: {refusal}")
            if op in _ELEMENT_OPS or op == "press":
                refusal = await self._gate(runtime, conversation_id, op, args)
                if refusal:
                    return refusal
            forward = {key: args[key] for key in _ARGS[op] if key in args}
            reply = await self._cmd(runtime, conversation_id, op, forward)
            ok = reply.get("ok") is True
            if ok and op == "screenshot" and isinstance(reply.get("image"), str):
                meta = {key: value for key, value in reply.items() if key != "image"}
                return {"ok": True, "text": json.dumps(meta, ensure_ascii=False), "image": reply["image"]}
            return {"ok": ok, "text": json.dumps(reply, ensure_ascii=False)}
