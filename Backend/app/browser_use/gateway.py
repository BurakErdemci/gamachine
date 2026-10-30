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
PAGE_CHANGED = "The page changed while the approval card was open, so nothing was done. Look at the page again."
FOCUS_CHANGED = "The focused element changed after the approval, so Enter was not pressed."
TARGET_CHANGED = "The element or the page changed after the approval, so nothing was done. Look at the page again."
CHAT_DELETED = "This chat was deleted, so the browser step was not run."
USER_CONTROL = (
    "The user is using this chat's browser tab. Do not call browser tools again in this turn; "
    "tell the user you will continue after their next message."
)
_ELEMENT_OPS = frozenset({"click", "fill", "select"})
_NAVIGATION_OPS = frozenset({"navigate", "back", "forward", "reload"})
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
    if state.get("deleted"):
        return None, CHAT_DELETED
    runtime = state.get("runtime")
    if (not isinstance(runtime, dict) or runtime.get("running") is not True
            or type(runtime.get("port")) is not int or not isinstance(runtime.get("token"), str)):
        return None, ("The agent browser is not running (the app has not started it, or the "
                      "backend runs in Docker, which is not supported).")
    return runtime, None


def _recheck(first, now, same_page=True) -> tuple:
    """(runtime, refusal) for a state read again before dispatch: still usable, on the same browser
    runtime as `first` and, with same_page, under the same page generation."""
    runtime, refusal = _usable(now)
    if refusal:
        return None, refusal
    if now.get("stop_gen") != first.get("stop_gen"):
        # A Stop of this chat, even one whose halt a new user message has already cleared.
        return None, HALTED
    if runtime != first.get("runtime") or (same_page and now.get("page_gen") != first.get("page_gen")):
        return None, PAGE_CHANGED
    return runtime, None


def _target_fingerprint(op, args, element) -> str:
    """What a grant is keyed on: one element of one snapshot; Enter has no ref, so it uses the
    element's own fingerprint plus the page's id for that element when the host sends one."""
    fingerprint = policy.fingerprint(element)
    if op != "press":
        return fingerprint + f"|{args['snapshot_id']}|{args['ref']}"
    element_id = element.get("element_id")
    if isinstance(element_id, str) or type(element_id) is int:
        fingerprint += f"|{element_id}"
    return fingerprint


def _seen(described, op, args):
    """(url, fingerprint, element id or None) of a describe reply, or None when it described nothing."""
    element = described.get("element") if described.get("ok") is True else None
    if element is None:
        return None
    element = element if isinstance(element, dict) else {}
    element_id = element.get("element_id")
    if not (isinstance(element_id, str) or type(element_id) is int):
        element_id = None
    return str(described.get("url") or ""), _target_fingerprint(op, args, element), element_id


def _same_target(first, second) -> bool:
    """Whether two _seen results name the same page and element; ids are compared when both carry one."""
    if first is None or second is None:
        return first is second
    return first[:2] == second[:2] and (first[2] is None or second[2] is None or first[2] == second[2])


class BrowserGateway:
    """host_transport(runtime, op, conversation_id, args) -> host reply;
    open_card(conversation_id, tool, params) -> the card decision ({"status": "resolved",
    "approved", "error"?} or {"status": "ok", "gate_id"}); wait_card(gate_id) -> the card's
    result once answered, else None; drop_grants(conversation_id) reports that this chat's page
    changed (a navigation, or any reply that shows another URL) so every approval that chat was given
    before it is forgotten; now() -> monotonic seconds."""

    def __init__(self, host_transport, open_card, wait_card, drop_grants=None, now=time.monotonic):
        self._host = host_transport
        self._open_card = open_card
        self._wait_card = wait_card
        self._drop_grants = drop_grants
        self._now = now
        # Per chat, as the last host reply showed it: every chat has its own tab.
        self._page_url: dict[int, str] = {}
        # One command at a time per chat, like the host's own per-tab queue: a step's describe must
        # see its own page. A chat waiting for its card blocks no other chat.
        self._locks: dict[int, asyncio.Lock] = {}

    async def _cmd(self, runtime, conversation_id, op, args):
        try:
            reply = await self._host(runtime, op, conversation_id, args)
        except Exception:
            return {"ok": False, "error": "The browser did not answer."}
        if not isinstance(reply, dict):
            return {"ok": False, "error": "The browser sent an invalid reply."}
        if reply.get("ok") is False and reply.get("error") == "user_control":
            # The user holds the tab: not a page change, nothing is dropped.
            return {"ok": False, "error": USER_CONTROL}
        # Any reply that shows another URL, failed ones included (a load error or a click's navigation
        # timeout still leaves the view on the new page), is a page change; it is reported before the
        # new URL becomes the baseline. A successful navigation op is one even on the same URL.
        url = reply.get("url")
        known = self._page_url.get(conversation_id)
        moved = isinstance(url, str) and known is not None and url != known
        if (moved or (op in _NAVIGATION_OPS and reply.get("ok") is True)) and self._drop_grants is not None:
            self._drop_grants(conversation_id)
        if isinstance(url, str):
            self._page_url[conversation_id] = url
        return reply

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

    async def _gate(self, before, conversation_id, op, args, state):
        """(refusal, runtime): a refusal (dict) for a step that may not run, or None and the runtime to
        send it to, taken from a state read after the describe and after any approval wait."""
        runtime, _ = _usable(before)
        if op == "press":
            if args["key"].casefold() != "enter":
                return None, runtime
            # The host sends the key to whatever has focus, so that is what gets described.
            target = {"focused": True}
        else:
            target = {"snapshot_id": args["snapshot_id"], "ref": args["ref"]}
        element, url = None, ""
        described = await self._cmd(runtime, conversation_id, "describe", target)
        # A Stop, a switch-off, a deletion or a host restart can land while the describe is out. The
        # generation is not compared here: the describe itself may have reported a page change.
        mark = state()
        runtime, refusal = _recheck(before, mark, same_page=False)
        if refusal:
            return _refusal(refusal), None
        if described.get("error") == USER_CONTROL:
            return _refusal(USER_CONTROL), None
        if described.get("ok") is not True:
            if op != "press":
                return _refusal(str(described.get("error") or "The element could not be described.")), None
        else:
            element, url = described.get("element"), str(described.get("url") or "")
        reason = (policy.critical_reason(op, element) if element is not None
                  else "Enter was pressed with no known target, so it could submit a form")
        if not reason:
            return None, runtime
        host = _host_of(url)
        shown = f"{reason} (page {host})" if host else reason
        element = element if isinstance(element, dict) else {}
        preview = ""
        if op in {"fill", "select"}:
            preview = "••••" if element.get("is_password") is True else policy.display_name(
                args[_TEXT_ARG[op]], _PREVIEW_LIMIT)
        fingerprint = _target_fingerprint(op, args, element)
        # The exact string the step will send, so a card approved for one value cannot cover another.
        value_hash = hashlib.sha256(args[_TEXT_ARG[op]].encode("utf-8")).hexdigest() if op in _TEXT_ARG else ""
        decision = self._open_card(conversation_id, f"browser_{op}", {
            "element_name": policy.display_name(element.get("name")), "page_url": url,
            "value_preview": preview, "risky": True, "risk_reason": shown,
            # Not shown: the backend keys the one-time grant on it.
            "fingerprint": fingerprint, "value_hash": value_hash,
        })
        result = await self._card_result(decision)
        if result is None:
            return _refusal(NEEDS_APPROVAL.format(reason=shown), needs_approval=True), None
        if not isinstance(result, dict) or result.get("approved") is not True:
            error = result.get("error") if isinstance(result, dict) else None
            return _refusal(str(error or "The user did not approve this step.")), None
        if isinstance(decision, dict) and decision.get("status") != "resolved":
            # The page, the element or the focus can change while the card waits; the step goes only
            # to what was approved. A grant needs no second look: nothing runs between its describe
            # and the step.
            again = await self._cmd(runtime, conversation_id, "describe", target)
            if again.get("error") == USER_CONTROL:
                return _refusal(USER_CONTROL), None
            if not _same_target(_seen(again, op, args), _seen(described, op, args)):
                return _refusal(FOCUS_CHANGED if op == "press" else TARGET_CHANGED), None
        # The wait can outlast a Stop, a switch-off (even one turned back on), a deleted chat, a host
        # restart or this chat's page change.
        runtime, refusal = _recheck(mark, state())
        if refusal:
            return _refusal(refusal), None
        return None, runtime

    async def call(self, conversation_id: int, op: str, args, state) -> dict:
        """Run one tool call for a verified chat; state() -> {enabled, halted, deleted, runtime, page_gen, stop_gen},
        read under the lock and again after every describe and approval wait."""
        if op not in _ARGS:
            return _refusal(f"Unknown browser tool browser_{op}.")
        if not isinstance(args, dict) or not _valid_args(op, args):
            return _refusal(f"Invalid arguments for browser_{op}.")
        # Read before queueing: a Stop that lands while this call waits for the lock must still refuse it,
        # even after a new user message has cleared the halt. A call that arrives already halted is refused
        # here for the same reason: the halt may be lifted before the lock frees.
        entry = state()
        if isinstance(entry, dict) and entry.get("halted"):
            return _refusal(HALTED)
        entry_stop_gen = entry.get("stop_gen") if isinstance(entry, dict) else None
        async with self._locks.setdefault(conversation_id, asyncio.Lock()):
            before = state()
            if isinstance(before, dict) and before.get("stop_gen") != entry_stop_gen:
                return _refusal(HALTED)
            runtime, refusal = _usable(before)
            if refusal:
                return _refusal(refusal)
            if op == "navigate":
                refusal = policy.navigation_refusal(args.get("url"))
                if refusal:
                    return _refusal(f"Refused: {refusal}")
            if op in _ELEMENT_OPS or op == "press":
                refusal, runtime = await self._gate(before, conversation_id, op, args, state)
                if refusal:
                    return refusal
            forward = {key: args[key] for key in _ARGS[op] if key in args}
            reply = await self._cmd(runtime, conversation_id, op, forward)
            if reply.get("error") == USER_CONTROL:
                return _refusal(USER_CONTROL)
            ok = reply.get("ok") is True
            if ok and op == "screenshot" and isinstance(reply.get("image"), str):
                meta = {key: value for key, value in reply.items() if key != "image"}
                return {"ok": True, "text": json.dumps(meta, ensure_ascii=False), "image": reply["image"]}
            return {"ok": ok, "text": json.dumps(reply, ensure_ascii=False)}
