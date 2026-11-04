"""The backend's agent-browser gateway: the only way a browser command reaches Electron's host.

POST /browser/command hands every agent-browser tool call here. Navigation is checked, critical
steps are described and classified, and a critical step opens an approval card and returns at
once unless the card is answered within APPROVAL_WAIT_S; the card's one-time grant lets the
identical retry through (see docs/agent-browser.md).
"""
import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from urllib.parse import unquote, urlsplit

import httpx

import unity_file_guard
from path_containment import resolve_within

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
DOWNLOAD_NEEDS_APPROVAL = (
    "needs_approval: the download of {name} is paused and an approval card is open in the chat. It starts "
    "as soon as the user approves it; check it with browser_downloads instead of clicking again."
)
# A click whose card opened because it starts a download approves the first agent download of that chat, if
# it arrives this soon after the click, on the same tab, page and Stop generation, and fits the link.
DOWNLOAD_CLICK_WINDOW_S = 10.0
# How long a card answered after the in-call wait still resumes or cancels its download; the host
# cancels a paused download on its own after 3 minutes (the card itself is swept at 170 s).
DOWNLOAD_SETTLE_S = 190.0
_SETTLE_POLL_S = 0.5
_DOWNLOAD_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_ELEMENT_OPS = frozenset({"click", "fill", "select"})
_NAVIGATION_OPS = frozenset({"navigate", "back", "forward", "reload"})
# A successful one of these puts the agent on another page, even at the same URL.
_PAGE_CHANGE_OPS = _NAVIGATION_OPS | {"use_tab"}
# The host command contract: what each op may forward. Anything else in args is dropped.
_ARGS = {
    "navigate": ("url",), "snapshot": ("max_chars",),
    "click": ("snapshot_id", "ref"), "fill": ("snapshot_id", "ref", "value"),
    "select": ("snapshot_id", "ref", "option"), "press": ("key",),
    "scroll": ("direction", "amount"), "back": (), "forward": (), "reload": (), "screenshot": (),
    "tabs": (), "use_tab": ("tab_id",),
    "downloads": ("wait_ms",), "download_to_project": ("id", "dest", "overwrite"),
}
# download_resume, download_cancel and download_info are host ops only the gateway sends, so they are
# not here: an agent never resumes its own download or reads its save path.
_TEXT_ARG = {"fill": "value", "select": "option", "press": "key"}


async def _post_host(runtime: dict, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=_CMD_TIMEOUT_S) as client:
        response = await client.post(
            f"http://127.0.0.1:{runtime['port']}/cmd",
            headers={"Authorization": f"Bearer {runtime['token']}"}, json=body)
        response.raise_for_status()
        return response.json()


def _denied_ports(runtime: dict) -> list:
    return sorted(policy.gamachine_ports((runtime["port"],)))


async def post_cmd(runtime: dict, op: str, conversation_id: int, args: dict) -> dict:
    # The host denies the same local ports as the policy here, including ones only this process knows
    # (the whisper server's random port); it refuses every loopback address until it has seen this list.
    denied = _denied_ports(runtime)
    target = {"port": runtime["port"], "token": runtime["token"]}
    _ports_push["runtime"] = target
    if _ports_push["task"] is None or _ports_push["task"].done():
        _ports_push["task"] = asyncio.get_running_loop().create_task(_push_ports())
    reply = await _post_host(runtime, {"op": op, "conversation_id": conversation_id, "args": args,
                                       "denied_ports": denied})
    # Only a command the host took delivered the list; after a failed post the task sends it.
    if _ports_push["runtime"] == target:
        _ports_push.update(sent=denied)
    return reply


# A Gamachine listener can start or move with no agent command in between (the whisper server's random
# port, a Unity MCP restart): one task sends the host the new list within _PORTS_PUSH_S. It ends when the
# host does not take a list (closed, or replaced by a runtime with another port or token); the next command
# starts it again.
_PORTS_PUSH_S = 5.0
_ports_push = {"runtime": None, "sent": None, "task": None}


async def _push_ports() -> None:
    while True:
        await asyncio.sleep(_PORTS_PUSH_S)
        runtime = _ports_push["runtime"]
        denied = _denied_ports(runtime)
        if denied == _ports_push["sent"]:
            continue
        try:
            await _post_host(runtime, {"op": "ports", "denied_ports": denied})
        except Exception:
            # A command may have moved the task to a new runtime meanwhile: it keeps pushing to that one.
            if _ports_push["runtime"] == runtime:
                return
            continue
        if _ports_push["runtime"] == runtime:
            _ports_push["sent"] = denied


def _refusal(text, **extra) -> dict:
    return {"ok": False, "error": text, **extra}


def _host_of(url) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _file_of(url) -> str:
    """The last path part of a link ('' for none or an invalid URL)."""
    try:
        return unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    except ValueError:
        return ""


def _has_target(args) -> bool:
    ref = args.get("ref")
    return (isinstance(args.get("snapshot_id"), str) and bool(args["snapshot_id"])
            and (isinstance(ref, str) or type(ref) is int))


def _valid_args(op, args) -> bool:
    if op == "use_tab":
        return type(args.get("tab_id")) is int and args["tab_id"] > 0
    if op == "downloads":
        wait = args.get("wait_ms", 0)
        return type(wait) is int and 0 <= wait <= 30_000
    if op == "download_to_project":
        return (isinstance(args.get("id"), str) and _DOWNLOAD_ID.fullmatch(args["id"]) is not None
                and isinstance(args.get("dest"), str) and type(args.get("overwrite", False)) is bool)
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
    before it is forgotten; now() -> monotonic seconds; workspace(conversation_id) -> the chat's project
    folder, where browser_download_to_project copies to."""

    def __init__(self, host_transport, open_card, wait_card, drop_grants=None, now=time.monotonic, workspace=None):
        self._host = host_transport
        self._open_card = open_card
        self._wait_card = wait_card
        self._drop_grants = drop_grants
        self._now = now
        self._workspace = workspace
        # Per chat: the one-time pass of a click approved because it starts a download (see _gate); the next
        # reply with a paused download spends it on the notice that fits the link (_covered_notice), a page
        # change or a failed click drops it.
        self._download_clicks: dict[int, dict] = {}
        # Cards answered after the call returned: kept so the loop does not drop the task.
        self._settling: set = set()
        # Per chat, as the last host reply showed it: every chat has its own tab.
        self._page_url: dict[int, str] = {}
        # One command at a time per chat, like the host's own per-tab queue: a step's describe must
        # see its own page. A chat waiting for its card blocks no other chat.
        self._locks: dict[int, asyncio.Lock] = {}

    def _file_roots(self, conversation_id) -> list:
        """Where a file: page may be read from: the chat's project (when it has one) and the OS temp folder."""
        workspace = self._workspace(conversation_id) if self._workspace else None
        roots = [workspace] if isinstance(workspace, str) and os.path.isdir(workspace) else []
        return [os.path.realpath(root) for root in roots + [tempfile.gettempdir()]]

    async def _cmd(self, runtime, conversation_id, op, args):
        try:
            reply = await self._host(runtime, op, conversation_id, args)
        except Exception:
            return {"ok": False, "error": "The browser did not answer."}
        if not isinstance(reply, dict):
            return {"ok": False, "error": "The browser sent an invalid reply."}
        if reply.get("ok") is False and reply.get("error") == "user_control":
            # The user holds the tab: not a page change, nothing is dropped. Download notices stay, so a
            # paused download still gets its card.
            return {"ok": False, "error": USER_CONTROL,
                    **{key: reply[key] for key in ("download", "downloadsPending") if key in reply}}
        # Any reply that shows another URL, failed ones included (a load error or a click's navigation
        # timeout still leaves the view on the new page), is a page change; it is reported before the
        # new URL becomes the baseline. A successful navigation op is one even on the same URL.
        url = reply.get("url")
        known = self._page_url.get(conversation_id)
        moved = isinstance(url, str) and known is not None and url != known
        if moved or (op in _PAGE_CHANGE_OPS and reply.get("ok") is True):
            self._download_clicks.pop(conversation_id, None)
            if self._drop_grants is not None:
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
        """(refusal, runtime, download pass): a refusal (dict) for a step that may not run, or None and the
        runtime to send it to, taken from a state read after the describe and after any approval wait; the
        pass is set when the approved card opened because the click starts a download, else None."""
        runtime, _ = _usable(before)
        if op == "press":
            if args["key"].casefold() != "enter":
                return None, runtime, None
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
            return _refusal(refusal), None, None
        if described.get("error") == USER_CONTROL:
            return _refusal(USER_CONTROL), None, None
        if described.get("ok") is not True:
            if op != "press":
                return _refusal(str(described.get("error") or "The element could not be described.")), None, None
        else:
            element, url = described.get("element"), str(described.get("url") or "")
        reason = (policy.critical_reason(op, element) if element is not None
                  else "Enter was pressed with no known target, so it could submit a form")
        if not reason:
            return None, runtime, None
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
            return _refusal(NEEDS_APPROVAL.format(reason=shown), needs_approval=True), None, None
        if not isinstance(result, dict) or result.get("approved") is not True:
            error = result.get("error") if isinstance(result, dict) else None
            return _refusal(str(error or "The user did not approve this step.")), None, None
        if isinstance(decision, dict) and decision.get("status") != "resolved":
            # The page, the element or the focus can change while the card waits; the step goes only
            # to what was approved. A grant needs no second look: nothing runs between its describe
            # and the step.
            again = await self._cmd(runtime, conversation_id, "describe", target)
            if again.get("error") == USER_CONTROL:
                return _refusal(USER_CONTROL), None, None
            if not _same_target(_seen(again, op, args), _seen(described, op, args)):
                return _refusal(FOCUS_CHANGED if op == "press" else TARGET_CHANGED), None, None
        # The wait can outlast a Stop, a switch-off (even one turned back on), a deleted chat, a host
        # restart or this chat's page change.
        runtime, refusal = _recheck(mark, state())
        if refusal:
            return _refusal(refusal), None, None
        if not policy.download_click(op, element):
            return None, runtime, None
        # Decided by the rule that opened the card, never by its page-supplied text; bound to the tab and the link.
        href = element.get("href") if isinstance(element.get("href"), str) else ""
        tab_id = described.get("tab_id")
        return None, runtime, {
            "tab_id": tab_id if type(tab_id) is int else None, "host": _host_of(href).casefold(),
            "filename": _file_of(href).casefold()}

    async def _download_cmd(self, runtime, conversation_id, op, args) -> dict:
        """A download op straight to the host: it never moves the page, so _cmd's page tracking stays out."""
        try:
            reply = await self._host(runtime, op, conversation_id, args)
        except Exception:
            return {"ok": False, "error": "The browser did not answer."}
        return reply if isinstance(reply, dict) else {"ok": False, "error": "The browser sent an invalid reply."}

    async def _settle(self, runtime, conversation_id, download_id, approved) -> dict:
        """Resume an approved download, cancel any other; the host's new view of it."""
        op = "download_resume" if approved else "download_cancel"
        reply = await self._download_cmd(runtime, conversation_id, op, {"id": download_id})
        return reply["download"] if isinstance(reply.get("download"), dict) else {}

    async def _settle_later(self, conversation_id, download_id, gate_id, state):
        """The card outlived the call's wait: whenever it is answered (or swept), resume or cancel."""
        deadline = self._now() + DOWNLOAD_SETTLE_S
        result = None
        while result is None and self._now() < deadline:
            await asyncio.sleep(_SETTLE_POLL_S)
            result = self._wait_card(gate_id)
        if result is None:
            return  # the host cancels the paused download itself
        current = state()
        runtime, refusal = _usable(current)
        approved = refusal is None and isinstance(result, dict) and result.get("approved") is True
        # A Stop or a switch-off still cancels on the host that holds the download.
        runtime = runtime or (current.get("runtime") if isinstance(current, dict) else None)
        if isinstance(runtime, dict) and type(runtime.get("port")) is int:
            await self._settle(runtime, conversation_id, download_id, approved)

    def _covered_notice(self, conversation_id, notices, state):
        """The one notice of this reply the chat's click pass covers (or None); pops the pass when the reply has
        a paused download or a covered one. The link's file name decides, else its host, else the first on the tab."""
        paused = any(isinstance(n, dict) and n.get("state") == "awaiting_approval" for n in notices)
        click = self._download_clicks.get(conversation_id)
        if click is None:
            return None
        covered = None
        if self._now() - click["at"] <= DOWNLOAD_CLICK_WINDOW_S:
            now = state()
            # A Stop or a page change since the click ends the pass (a halted chat's call never gets here).
            if (isinstance(now, dict) and now.get("page_gen") == click["page_gen"]
                    and now.get("stop_gen") == click["stop_gen"]):
                # A link path without an extension (/dl/12345) does not name the file; its host decides then.
                name = click["filename"] if policy.file_type(click["filename"]) else ""
                field, want = ("filename", name) if name else ("host", click["host"])
                for notice in notices:
                    if not isinstance(notice, dict) or (click["tab_id"] is not None
                                                        and notice.get("tab_id") != click["tab_id"]):
                        continue
                    if not want or str(notice.get(field) or "").casefold() == want:
                        covered = notice
                        break
        if paused or covered is not None:
            self._download_clicks.pop(conversation_id, None)
        return covered

    async def _approve_download(self, runtime, conversation_id, notice, state, covered=False) -> dict:
        """One approval for a paused agent download: the click card that started it (covered), else its own card."""
        download_id = notice.get("id")
        if not isinstance(download_id, str) or _DOWNLOAD_ID.fullmatch(download_id) is None:
            return notice
        if covered:
            return {**notice, **await self._settle(runtime, conversation_id, download_id, True),
                    "approved_by": "the click card"}
        name = policy.display_name(notice.get("filename")) or "a file"
        host = policy.display_name(notice.get("host"))
        size = notice.get("bytes")
        decision = self._open_card(conversation_id, "browser_download", {
            "filename": name, "bytes": size if type(size) is int and size >= 0 else None, "host": host,
            "page_url": self._page_url.get(conversation_id, ""), "risky": True,
            "risk_reason": f"the agent downloads {name}" + (f" from {host}" if host else ""),
        })
        result = await self._card_result(decision)
        if result is None:
            gate_id = decision.get("gate_id") if isinstance(decision, dict) else None
            if isinstance(gate_id, str):
                task = asyncio.ensure_future(self._settle_later(conversation_id, download_id, gate_id, state))
                self._settling.add(task)
                task.add_done_callback(self._settling.discard)
            return {**notice, "note": DOWNLOAD_NEEDS_APPROVAL.format(name=name)}
        approved = isinstance(result, dict) and result.get("approved") is True
        settled = await self._settle(runtime, conversation_id, download_id, approved)
        if not approved:
            error = result.get("error") if isinstance(result, dict) else None
            settled = {**settled, "reason": str(error or "The user did not approve this download.")}
        return {**notice, **settled}

    async def _report_downloads(self, runtime, conversation_id, reply, state) -> dict:
        """Every paused agent download the host reports in this reply gets its click pass or its own card."""
        found = [reply["download"]] if "download" in reply else []
        if isinstance(reply.get("downloadsPending"), list):
            found += reply["downloadsPending"]
        covered = self._covered_notice(conversation_id, found, state)
        chosen = state() if covered is not None else None

        def still_covered(notice) -> bool:
            # An earlier card of this reply may have waited: a Stop or page change since then ends the pass.
            now = state()
            return (notice is covered and isinstance(now, dict) and isinstance(chosen, dict)
                    and now.get("page_gen") == chosen.get("page_gen") and now.get("stop_gen") == chosen.get("stop_gen"))

        async def settle(notice):
            if isinstance(notice, dict) and notice.get("state") == "awaiting_approval":
                return await self._approve_download(runtime, conversation_id, notice, state, still_covered(notice))
            return notice
        reply = dict(reply)
        if "download" in reply:
            reply["download"] = await settle(reply["download"])
        if isinstance(reply.get("downloadsPending"), list):
            reply["downloadsPending"] = [await settle(notice) for notice in reply["downloadsPending"]]
        notices = [reply.get("download")] + (reply.get("downloadsPending") or [])
        if any(isinstance(notice, dict) and "note" in notice for notice in notices):
            reply["needs_approval"] = True
        return reply

    async def _to_project(self, runtime, conversation_id, args) -> dict:
        """Copy a completed download of this chat into its project, at dest relative to the project root."""
        dest = args["dest"].strip()
        workspace = self._workspace(conversation_id) if self._workspace else None
        if not isinstance(workspace, str) or not os.path.isdir(workspace):
            return _refusal("No project is open for this chat, so there is nowhere to copy the file.")
        parts = [part for part in re.split(r"[\\/]", dest) if part not in ("", ".")]
        if (not parts or os.path.isabs(dest) or dest.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", dest)
                or ".." in parts or any(":" in part for part in parts)):
            return _refusal("dest must be a path inside the project, relative to its root "
                            "(for example Assets/Audio/song.mp3).")
        refusal = policy.project_dest_refusal(parts) or policy.project_copy_refusal(dest)
        if refusal:
            return _refusal(f"Refused: {refusal}.")
        info = await self._download_cmd(runtime, conversation_id, "download_info", {"id": args["id"]})
        entry = info.get("download") if info.get("ok") is True else None
        if not isinstance(entry, dict) or entry.get("state") != "completed":
            return _refusal(str(info.get("error") or "No completed download of this chat has that id; "
                                "see browser_downloads."))
        source, folder = entry.get("savePath"), entry.get("dir")
        refusal = policy.project_copy_refusal(source) or policy.project_copy_refusal(entry.get("filename"))
        if refusal:
            return _refusal(f"Refused: {refusal}.")
        if not policy.file_type(source):
            return _refusal("Refused: a file without an extension is never copied into the project.")
        if policy.file_type(dest) != policy.file_type(source):
            return _refusal(f"Refused: the copy keeps the downloaded file's type, so dest must end in "
                            f"{policy.file_type(source) or 'no extension'}.")
        if (not isinstance(source, str) or not isinstance(folder, str) or os.path.islink(source)
                or resolve_within(source, folder) is None or not os.path.isfile(source)):
            return _refusal("The downloaded file is no longer in the Downloads folder.")
        # resolve_within follows links, so a link inside the project could still lead elsewhere in it:
        # every existing step of the path must be a plain folder or file.
        step = workspace
        for part in parts:
            step = os.path.join(step, part)
            if os.path.islink(step) or os.path.isjunction(step):
                return _refusal("dest goes through a link; pick a plain folder in the project.")
        target = resolve_within(os.path.join(*parts), workspace)
        if target is None:
            return _refusal("dest is outside the project.")
        if os.path.isdir(target):
            return _refusal("dest is a folder; give the file name too (for example Assets/Audio/song.mp3).")
        if os.path.lexists(target) and args.get("overwrite") is not True:
            return _refusal("A file already exists at dest; pass overwrite: true to replace it.")
        refusal = unity_file_guard.check_write(target, workspace)
        if refusal is not None:
            return _refusal(refusal.message)

        def copy() -> int:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            # Hidden and unique: Unity skips dot names, and no existing file can share the staging path.
            partial = os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.{secrets.token_hex(6)}.gmpart")
            try:
                shutil.copyfile(source, partial)
                os.replace(partial, target)
            except Exception:
                try:
                    os.remove(partial)
                except OSError:
                    pass
                raise
            return os.path.getsize(target)

        try:
            size = await asyncio.to_thread(copy)
        except OSError as error:
            return _refusal(f"Copying the file failed: {error.strerror or error}.")
        relative = os.path.relpath(target, os.path.realpath(workspace)).replace(os.sep, "/")
        return {"ok": True, "text": json.dumps({"ok": True, "path": relative, "bytes": size}, ensure_ascii=False)}

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
            file_roots = None
            if op == "navigate":
                file_roots = self._file_roots(conversation_id)
                refusal = policy.navigation_refusal(args.get("url"), file_roots, (runtime["port"],))
                if refusal:
                    return _refusal(f"Refused: {refusal}")
            click_pass = None
            if op in _ELEMENT_OPS or op == "press":
                refusal, runtime, click_pass = await self._gate(before, conversation_id, op, args, state)
                if refusal:
                    return refusal
            if op == "download_to_project":
                return await self._to_project(runtime, conversation_id, args)
            forward = {key: args[key] for key in _ARGS[op] if key in args}
            if file_roots and str(args.get("url")).lower().startswith("file:"):
                # The host checks a file: page against the same roots again, for the links it follows.
                forward["file_roots"] = file_roots
            send = self._download_cmd if op == "downloads" else self._cmd
            reply = await send(runtime, conversation_id, op, forward)
            if click_pass is not None:
                # Valid only once the click has run; a failed click leaves no pass behind, nor an older one.
                self._download_clicks.pop(conversation_id, None)
                after = state()
                if reply.get("ok") is True and isinstance(after, dict):
                    self._download_clicks[conversation_id] = {
                        **click_pass, "at": self._now(), "page_gen": after.get("page_gen"),
                        "stop_gen": after.get("stop_gen")}
            # The host reports the agent downloads it paused in the next reply of that chat.
            reply = await self._report_downloads(runtime, conversation_id, reply, state)
            if reply.get("error") == USER_CONTROL:
                return _refusal(USER_CONTROL)
            ok = reply.get("ok") is True
            if ok and op == "screenshot" and isinstance(reply.get("image"), str):
                meta = {key: value for key, value in reply.items() if key != "image"}
                return {"ok": True, "text": json.dumps(meta, ensure_ascii=False), "image": reply["image"]}
            return {"ok": ok, "text": json.dumps(reply, ensure_ascii=False)}
