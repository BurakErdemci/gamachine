"""Stdlib-only preview and approval bridge for agy's native file writers."""
import contextvars
import hashlib
import json
import ntpath
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid

import local_token_file
import unity_file_guard

# The hook supplies its actual state file without adding a model-controlled argument.
state_path = contextvars.ContextVar("agy_native_state_path", default=None)
POST_SECONDS = 10.0
POLL_SECONDS = 150.0
TOTAL_SECONDS = 165.0
REPLACE_REASON = ("Unverified replacement; use write_file (write_to_file on older agy) "
                  "with the full content.")


class _WallTimeout(TimeoutError):
    """The HTTP reader's wall-clock budget expired; it may still be alive."""


def _read_file(path):
    try:
        with open(path, "rb") as stream:
            data = stream.read()
        return True, data
    except FileNotFoundError:
        return False, b""


def _read_json_with_retry(path, budget_s=0.5):
    """Read and decode JSON from path, retrying transient sharing/half-visible errors."""
    deadline = time.monotonic() + budget_s
    while True:
        try:
            with open(path, encoding="utf-8") as stream:
                return json.load(stream)
        except FileNotFoundError:
            raise
        except (PermissionError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _state_matches(path, allowed_modes=("balanced",)):
    # Only Safe Auto asks for a card; auto never reaches here, and any other
    # state (closed, side, unknown) must not be approved into a write. After a
    # card is approved, "auto" is also accepted because switching to auto
    # approves every open card.
    state = _read_json_with_retry(path)
    return isinstance(state, dict) and state.get("mode") in allowed_modes


_ARG_NAMES = {name.replace("_", "").lower(): name for name in (
    "TargetFile", "CodeContent", "Overwrite", "TargetContent", "ReplacementContent",
    "StartLine", "EndLine", "AllowMultiple")}


# Every spelling a writer's target path may take, folded (case and "_").
PATH_KEYS = frozenset({"targetfile", "path", "filepath", "file", "absolutepath", "targetpath"})
# agy 1.5.15 (measured 24 Nov 2026): write_file sends exactly Path, Content and
# Overwrite; edit_file sends Target, Replacement and a path key whose spelling
# was not recorded. They map onto the agy 1.2.x names, and the preview treats
# them as write_to_file and replace_file_content. Any other key is refused, not
# skipped: an unmeasured flag (append, all occurrences, a line range) could make
# agy write something other than what the card showed.
_V15_ARG_NAMES = {
    "write_file": {"path": "TargetFile", "content": "CodeContent", "overwrite": "Overwrite"},
    "edit_file": {"target": "TargetContent", "replacement": "ReplacementContent",
                  **{key: "TargetFile" for key in PATH_KEYS}},
}
_PREVIEW_AS = {"write_file": "write_to_file", "edit_file": "replace_file_content"}
NATIVE_WRITERS = ("write_to_file", "replace_file_content") + tuple(_V15_ARG_NAMES)


def _normalized_args(args, tool_name=None):
    """Fold native argument spellings, rejecting even equal duplicates."""
    if not isinstance(args, dict):
        raise ValueError("Missing or invalid native write arguments.")
    strict = _V15_ARG_NAMES.get(tool_name)
    normalized = {}
    for key, value in args.items():
        folded = key.replace("_", "").lower() if isinstance(key, str) else None
        if strict is not None:
            name = strict.get(folded)
            if name is None:
                raise ValueError(f"Unverified {tool_name} argument {key!r}: refused.")
        else:
            name = _ARG_NAMES.get(folded)
        if name is not None:
            if name in normalized:
                raise ValueError(f"Duplicate spellings of {name}.")
            normalized[name] = value
    return normalized


def _preview(tool_name, args, original, exists):
    if tool_name == "write_to_file":
        content = args.get("CodeContent")
        if not isinstance(content, str):
            raise ValueError("Missing or invalid CodeContent.")
        if "Overwrite" in args and type(args["Overwrite"]) is not bool:
            raise ValueError("Invalid Overwrite: unverified agy behaviour.")
        if exists and args.get("Overwrite") is not True:
            raise ValueError("Existing file without Overwrite=True: unverified agy behaviour.")
        content = content.replace("\r\n", "\n")
        return content if content.endswith("\n") else content + "\n"
    if not exists or type(args.get("AllowMultiple", False)) is not bool or args.get("AllowMultiple", False):
        raise ValueError(REPLACE_REASON)
    target, replacement = args.get("TargetContent"), args.get("ReplacementContent")
    if not isinstance(target, str) or not target or not isinstance(replacement, str):
        raise ValueError(REPLACE_REASON)
    if "StartLine" in args or "EndLine" in args:
        start, end = args.get("StartLine"), args.get("EndLine")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end:
            raise ValueError(REPLACE_REASON)
    without_crlf = original.replace("\r\n", "")
    if "\r" in without_crlf or ("\r\n" in original and "\n" in without_crlf):
        raise ValueError(REPLACE_REASON)
    text = original.replace("\r\n", "\n")
    target = target.replace("\r\n", "\n")
    replacement = replacement.replace("\r\n", "\n")
    first = text.find(target)
    # Count overlapping occurrences too: there must be only one possible match.
    if first < 0 or text.find(target, first + 1) >= 0:
        raise ValueError(REPLACE_REASON)
    if "StartLine" in args and "EndLine" in args:
        start, end = args["StartLine"], args["EndLine"]
        first_line = text.count("\n", 0, first) + 1
        last_line = text.count("\n", 0, first + len(target) - 1) + 1
        if not (1 <= start <= first_line <= last_line <= end):
            raise ValueError(REPLACE_REASON)
    result = text[:first] + replacement + text[first + len(target):]
    return result.replace("\n", "\r\n") if "\r\n" in original else result


def _http_json(request, deadline, socket_seconds):
    """Bound the whole response, including a peer that trickles bytes forever.

    At most one daemon reader is alive at a time. If its wall-clock budget
    expires the caller denies and never starts another request.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _WallTimeout("Approval request timed out.")
    result = queue.Queue(maxsize=1)

    def read():
        try:
            with urllib.request.urlopen(request, timeout=min(socket_seconds, remaining)) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise urllib.error.HTTPError(request.full_url, status,
                                                 "Approval HTTP error", None, None)
                value = json.loads(response.read().decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Malformed approval response.")
            result.put((value, None))
        except Exception as error:
            result.put((None, error))

    threading.Thread(target=read, daemon=True).start()
    try:
        value, error = result.get(timeout=max(0.001, deadline - time.monotonic()))
    except queue.Empty:
        raise _WallTimeout("Approval request timed out.") from None
    if time.monotonic() >= deadline:
        raise _WallTimeout("Approval request timed out.")
    if error is not None:
        raise error
    return value


def _approval(params, workspace, total_deadline):
    backend = os.environ.get("UNITYAI_URL", os.environ.get("ANTIGRAVITY_URL", "http://localhost:8000"))
    token = local_token_file.read_local_app_token()
    headers = {"X-Session-Token": token} if token else {}
    gate_id = uuid.uuid4().hex[:10]
    body = {"gate_id": gate_id, "tool": "write_file", "params": params,
            "workspace_path": workspace}
    owner = os.environ.get("GAMACHINE_CONVERSATION_ID", "")
    if owner.isascii() and owner.isdigit():
        conversation_id = int(owner)
        if 0 < conversation_id < 2**63:
            body["conversation_id"] = conversation_id
    request = urllib.request.Request(backend.rstrip("/") + "/mcp-approval-request",
        data=json.dumps(body).encode("utf-8"), headers={**headers, "Content-Type": "application/json"})
    post_deadline = min(total_deadline, time.monotonic() + POST_SECONDS)
    while True:
        try:
            data = _http_json(request, post_deadline, 8.0)
            break
        except (urllib.error.URLError, OSError) as error:
            # A wall-clock timeout may leave a daemon reader: do not retry it.
            if isinstance(error, _WallTimeout) or time.monotonic() + 1 >= post_deadline:
                raise
            time.sleep(1.0)
    if data.get("status") == "resolved":
        return data
    if data.get("status") not in ("ok", "pending"):
        raise ValueError("Malformed approval response status.")
    poll_deadline = min(total_deadline, time.monotonic() + POLL_SECONDS)
    request = urllib.request.Request(backend.rstrip("/") + "/mcp-approval-result/" + gate_id,
                                     headers=headers)
    while time.monotonic() < poll_deadline:
        time.sleep(min(0.5, max(0.0, poll_deadline - time.monotonic())))
        if time.monotonic() >= poll_deadline:
            break
        data = _http_json(request, poll_deadline, 5.0)
        if data.get("status") == "resolved":
            return data
        if data.get("status") != "pending":
            raise ValueError("Malformed approval response status.")
    raise TimeoutError("Approval timed out.")


def request_native_write(tool_name, args, workspace, base=None):
    """Return (allowed, reason); never write the target and never allow on error.

    `workspace` is the hook workspace the card is for; `base` the directory
    agy resolves a relative target against (the hook payload's
    workspacePaths[0]), the workspace when not given."""
    deadline = time.monotonic() + TOTAL_SECONDS
    try:
        import action_risk  # stdlib-only; like agy_step_gate, only on a gated write
        if tool_name not in NATIVE_WRITERS:
            return False, "Unsupported native write tool."
        if not isinstance(workspace, str) or not workspace or not os.path.isabs(workspace):
            return False, "Missing or invalid hook workspace."
        workspace = os.path.realpath(workspace)
        base = workspace if base is None else base
        if not isinstance(base, str) or not base or not os.path.isabs(base):
            return False, "Missing or invalid agy workspace."
        args = _normalized_args(args, tool_name)
        if not isinstance(args.get("TargetFile"), str) or not args["TargetFile"]:
            return False, "Missing or invalid TargetFile."
        raw_target = args["TargetFile"]
        drive, tail = ntpath.splitdrive(raw_target)
        if (raw_target.replace("/", "\\").startswith("\\\\")
                or (drive and not tail.startswith(("/", "\\")))):
            return False, "Unverified TargetFile path: use a plain absolute or relative path."
        target = args["TargetFile"].replace("\\", os.sep).replace("/", os.sep)
        target = target if os.path.isabs(target) else os.path.join(base, target)
        path = os.path.realpath(target)
        # Outside the workspace is the card's decision (owner decision, 4 Nov
        # 2026); Gamachine's own key and token files are refused in every mode.
        if action_risk.gamachine_secret_target(path) is not None:
            return False, action_risk.secret_refusal(path)
        refusal = unity_file_guard.check_write(path, workspace)
        if refusal is not None:
            return False, refusal.message
        saved_state_path = state_path.get()
        if not saved_state_path or not _state_matches(saved_state_path):
            return False, "Native approval could not verify the gate state."
        exists, data = _read_file(path)
        original = data.decode("utf-8")
        digest = hashlib.sha256(data).digest()
        content = _preview(_PREVIEW_AS.get(tool_name, tool_name), args, original, exists)
        result = _approval({"path": path, "content": content, "original": original}, workspace, deadline)
        if result.get("approved") is not True:
            return False, "Native file write was not explicitly approved."
        if not _state_matches(saved_state_path, ("balanced", "auto")):
            return False, "Gate mode changed while awaiting approval."
        current_exists, current = _read_file(path)
        if (os.path.realpath(target) != path or exists != current_exists
                or hashlib.sha256(current).digest() != digest):
            return False, "file changed since the card was shown"
        if time.monotonic() >= deadline:
            return False, "Native approval timed out."
        return True, "Native file write explicitly approved."
    except Exception as error:
        return False, f"Native approval denied: {error}"
