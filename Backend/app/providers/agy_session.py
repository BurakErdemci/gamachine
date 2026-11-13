"""Persistent Antigravity stream-json sessions, one subprocess per conversation."""
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
import process_tree
from collections.abc import Hashable, Mapping
from datetime import datetime, timedelta
from typing import AsyncGenerator, Dict, Optional

from agy_step_gate import SIDE_MODE
from .agy_provider import AgyProvider, AgyStepGateError, _gate_write_failed, gate_state_path
from .cli_base import BaseCLIProvider, _CREATE_NO_WINDOW, build_spawn_env
from .saglayici_sahipligi import SaglayiciSahipligi, oturumu_kapat
from .text_blocks import block_break
from secret_redaction import redact_secrets
from spawn_env import conversation_env

_SESSIONS: Dict[int, "AgyStreamSession"] = {}
# Retain an init UUID even if a process dies before done can reach the disk store.
_RESUME_IDS: Dict[tuple, str] = {}
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens",
               "thinking_tokens", "total_tokens")
_DRAIN_MAX_LINES = 200
_DRAIN_MAX_SECONDS = 0.5
# Turns are serialized machine-wide, so a second agy chat waits for another
# chat's whole turn and looked frozen. `code` lets the UI use its own language;
# `detail` is the fallback for a client that does not know the code.
_QUEUED_EVENT = {"type": "status", "code": "agy_queued",
                 "detail": "Sırada — başka bir agy sohbetinin turu bitince başlayacak"}
_STARTED_EVENT = {"type": "status", "code": "agy_started", "detail": "Çalışıyor…"}
logger = logging.getLogger(__name__)

# A kept child with no turn for this long has its process tree stopped by
# stop_idle_children; the next message respawns it with the same resume id.
_AGY_IDLE_STOP_S = 900

# A read-only side question on agy (Burak, 27 Sep 2026) runs only while the
# turn lock is free, never queues, and gives way to any other agy turn.
SIDE_TURN_TIMEOUT_S = 120
SIDE_BUSY_MESSAGE = (
    "Yan soru Antigravity (agy) ile kullanılamıyor: agy aynı anda tek bir tur "
    "çalıştırıyor, yani yan soru ana sohbetin turunun bitmesini beklerdi. "
    "Yan soru için başka bir model seç."
)
_SIDE_STOP_MESSAGES = {
    "agy_side_preempted": {"tr": "Ana sohbet başladı, yan soru durduruldu.",
                           "en": "The main chat started, so the side question was stopped."},
    "agy_side_timeout": {"tr": "Yan soru 2 dakikada bitmedi, durduruldu.",
                         "en": "The side question did not finish within 2 minutes and was stopped."},
}


def side_stop_message(code: str, language: str = "tr") -> str:
    texts = _SIDE_STOP_MESSAGES[code]
    return texts["en"] if language == "en" else texts["tr"]


class _AgyIdleLimit(Exception):
    """No child output for BaseCLIProvider._AGY_IDLE_LIMIT seconds."""


async def _within_idle(awaitable, idle_limit: Optional[float]):
    """Await a child pipe operation; _AgyIdleLimit after idle_limit seconds."""
    try:
        return await asyncio.wait_for(awaitable, idle_limit)
    except TimeoutError:
        raise _AgyIdleLimit() from None


# Quota exhaustion as agy reports it (owner, 12 Nov 2026): "Individual quota
# reached ... AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429) ...}",
# once in the result error and again in stderr, so the user saw it three times.
_QUOTA_PATTERN = re.compile(
    r"resource_exhausted|quota (?:reached|exceeded|exhausted)|\(code 429\)", re.IGNORECASE)
_RESET_PATTERN = re.compile(r"resets?\s+in\s+((?:\d+\s*[dhms]\s*)+)", re.IGNORECASE)
_RESET_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_CHANGED_FILES_SHOWN = 15
_GIT_TIMEOUT_S = 10
# How long a turn waits for the workspace snapshot before its prompt; past
# this the turn goes on and its end message omits the file list.
_SNAPSHOT_WAIT_S = 3


def _say(language: str, tr: str, en: str) -> str:
    return en if language == "en" else tr


def _span(seconds: float, language: str) -> str:
    """119h46m43s -> "119 sa 46 dk" / "119 h 46 min" (seconds dropped)."""
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    units = (("gün", "d"), ("sa", "h"), ("dk", "min"))
    parts = [f"{value} {_say(language, *unit)}"
             for value, unit in zip((days, hours, minutes), units) if value]
    if days:
        parts = parts[:2]
    return " ".join(parts) or _say(language, "1 dakikadan az", "under a minute")


def quota_info(text: str) -> Optional[dict]:
    """Parse an agy quota failure; None when the text is not one."""
    if not text or not _QUOTA_PATTERN.search(text):
        return None
    info = {"individual": "individual quota" in text.lower(), "reset_seconds": None}
    match = _RESET_PATTERN.search(text)
    if match:
        info["reset_seconds"] = sum(int(n) * _RESET_UNITS[u.lower()] for n, u in
                                    re.findall(r"(\d+)\s*([dhmsDHMS])", match.group(1)))
    return info


def quota_message(info: dict, model: str, language: str, now: Optional[datetime] = None) -> str:
    """One clean line: which quota, when it resets, and the way on."""
    model = model or "agy"
    quota = (_say(language, f"{model} için bireysel Antigravity kotası doldu.",
                  f"Your individual Antigravity quota for {model} is used up.")
             if info.get("individual") else
             _say(language, f"{model} için Antigravity kotası doldu.",
                  f"The Antigravity quota for {model} is used up."))
    reset = ""
    if info.get("reset_seconds"):
        # The relative countdown goes stale on screen (owner saw a frozen
        # "Resets in 119h46m43s"), so the clock time it points at goes with it.
        at = (now or datetime.now()) + timedelta(seconds=info["reset_seconds"])
        clock = (f"{_MONTHS_EN[at.month - 1]} {at.day}, {at:%H:%M}" if language == "en"
                 else f"{at.day:02d}.{at.month:02d} {at:%H:%M}")
        span = _span(info["reset_seconds"], language)
        reset = " " + _say(language, f"{span} sonra sıfırlanıyor ({clock} civarı).",
                           f"It resets in {span} (around {clock}).")
    hint = _say(language,
                "Model seçiciden başka bir model seçip aynı sohbete devam edebilirsin.",
                "Pick another model in the model selector to continue this chat.")
    return f"{quota}{reset} {hint}"


def _squeeze(text: str) -> tuple:
    """`text` with every whitespace run as one space, and the index in
    `text` each kept character came from."""
    chars, origin = [], []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] == " ":
                continue
            char = " "
        chars.append(char)
        origin.append(index)
    return "".join(chars), origin


class _ReplayFilter:
    """Removes agy's quota failure, replayed as answer text in a resumed chat
    (owner, 12 Nov 2026: the old quota line came back under a later
    successful answer), from the streamed and the stored answer.

    Only an exact replay of a failure text this conversation received (see
    _QUOTA_FAILURES) is removed, whitespace and line endings aside; the text
    around it stays as it came. Pattern rules dropped real answers that
    discussed a quota (audit 170ccc19), so there are none. With no recorded
    text the filter passes everything through and holds nothing."""

    def __init__(self, texts=()):
        self.texts = [flat for flat in (_squeeze(text)[0].strip() for text in texts) if flat]
        self.longest = max(map(len, self.texts), default=0)
        self.step = None
        self.held = ""  # the step's tail that may still become a replay
        self.skip_space = False  # the whitespace right after a removed replay
        self.removed = False
        self.answer_steps = {}  # step -> its raw text, for record_quota_failure

    def feed(self, step, delta: str) -> str:
        """The text to stream now for a text delta of `step`."""
        self.answer_steps[step] = self.answer_steps.get(step, "") + delta
        if not self.texts:
            return delta
        out = ""
        if step != self.step:
            out = self.end()
            self.step = step
        shown, self.held = self._split(self.held + delta, final=False)
        return out + shown

    def end(self) -> str:
        """The step is over: what it held back did not become a replay."""
        held, self.held, self.step = self.held, "", None
        # A completed shorter text may have been held for a longer one that
        # never came: it is a replay after all.
        held = self._split(held, final=True)[0] if held and self.texts else held
        self.skip_space = False
        return held

    def clean(self, response: str) -> str:
        """result.response glues the turn's blocks: drop the same replay."""
        if not self.texts:
            return response
        self.skip_space = False
        return self._split(response, final=True)[0]

    def _split(self, text: str, *, final: bool) -> tuple:
        """(text to show, tail held back): every replay removed and, unless
        `final`, the tail that is still the start of one held back."""
        shown = []
        while text:
            if self.skip_space:
                if not text.strip():
                    break
                text, self.skip_space = text.lstrip(), False
            flat, origin = _squeeze(text)
            hits = [(flat.find(needle), -len(needle)) for needle in self.texts if needle in flat]
            if hits:
                at, minus_length = min(hits)
                tail = flat[at:]
                if not final and any(len(needle) > len(tail) and needle.startswith(tail)
                                     for needle in self.texts):
                    # Still the start of a longer recorded text: the longest wins.
                    shown.append(text[:origin[at]])
                    return "".join(shown), text[origin[at]:]
                start, stop = origin[at], origin[at - minus_length - 1] + 1
                shown.append(text[:start])
                logger.warning("[agy] replayed quota text removed from the answer: %s",
                               text[start:stop][:500])
                self.removed = self.skip_space = True
                text = text[stop:]
                continue
            if not final:
                for at in range(max(0, len(flat) - self.longest), len(flat)):
                    if any(needle.startswith(flat[at:]) for needle in self.texts):
                        shown.append(text[:origin[at]])
                        return "".join(shown), text[origin[at]:]
            shown.append(text)
            break
        return "".join(shown), ""


# The quota failure text agy sent each conversation, keyed like _RESUME_IDS:
# a resumed chat may replay it as answer text. A record serves at most
# _REPLAY_TURNS turns and ends at the first successful turn without a replay,
# so it cannot keep eating later answers. A text shorter than
# _REPLAY_MIN_CHARS is not recorded: removing it could cut a real sentence.
_QUOTA_FAILURES: Dict[tuple, dict] = {}
_REPLAY_TURNS = 2
_REPLAY_MIN_CHARS = 20


def record_quota_failure(key: tuple, texts) -> None:
    texts = [text for text in dict.fromkeys(text.strip() for text in texts if text)
             if len(_squeeze(text)[0]) >= _REPLAY_MIN_CHARS]
    if texts:
        _QUOTA_FAILURES[key] = {"texts": texts, "turns": _REPLAY_TURNS}


def take_quota_replay(key: tuple) -> Optional[dict]:
    """The record a starting turn filters with; this turn counts against it."""
    record = _QUOTA_FAILURES.get(key)
    if record is not None:
        record["turns"] -= 1
        if record["turns"] <= 0:
            _QUOTA_FAILURES.pop(key, None)
    return record


def _git(cwd: str, *args: str) -> Optional[str]:
    """git's output, None when it fails; raises when git cannot run."""
    done = subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                          timeout=_GIT_TIMEOUT_S, creationflags=_CREATE_NO_WINDOW,
                          stdin=subprocess.DEVNULL, env=build_spawn_env())
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", errors="replace")


def workspace_state(cwd: str) -> Optional[dict]:
    """Every uncommitted path of cwd's git work tree -> (status, mtime, size);
    None when cwd is not a work tree or git cannot say. Never raises: the
    error path of a turn must not break on it."""
    try:
        root = _git(cwd, "rev-parse", "--show-toplevel")
        status = _git(cwd, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if root is None or status is None:
            return None
        root = root.strip()
        entries = status.split("\0")
        state = {}
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if len(entry) < 4:
                continue
            code, path = entry[:2], entry[3:]
            if "R" in code or "C" in code:
                index += 1  # -z puts the rename source in the next field
            try:
                stat = os.stat(os.path.join(root, path))
                state[path] = (code, stat.st_mtime_ns, stat.st_size)
            except OSError:
                state[path] = (code, None, None)
        return state
    except Exception:
        logger.debug("[agy] workspace git status failed cwd=%s", cwd, exc_info=True)
        return None


def changed_files(cwd: str, before: Optional[dict]) -> Optional[list]:
    """Paths the turn changed: those whose git status, mtime or size differ
    from `before` (workspace_state at turn start), including dirty paths the
    turn reverted, committed or deleted. Edits and deletions that predate the
    turn are not listed. None when either state is unknown: no list beats a
    guessed one."""
    if before is None:
        return None
    after = workspace_state(cwd)
    if after is None:
        return None
    return [path for path in {**before, **after} if before.get(path) != after.get(path)]


def turn_summary(*, tools: dict, failed: int, files: Optional[list], language: str) -> str:
    """What an unfinished turn had done: its steps and the files it left."""
    lines = []
    steps = sum(tools.values())
    if steps:
        top = sorted(tools.items(), key=lambda item: (-item[1], item[0]))
        named = ", ".join(f"`{name}` ×{count}" for name, count in top[:6])
        if len(top) > 6:
            named += _say(language, f" ve {len(top) - 6} araç daha", f" and {len(top) - 6} more tools")
        failed_text = (_say(language, f", {failed} tanesi hata verdi", f", {failed} failed")
                       if failed else "")
        lines.append(_say(language, f"Durmadan önce {steps} adım attı{failed_text}: {named}.",
                          f"Before it stopped it ran {steps} steps{failed_text}: {named}."))
    if files:
        shown = "\n".join(f"- `{path}`" for path in files[:_CHANGED_FILES_SHOWN])
        more = len(files) - _CHANGED_FILES_SHOWN
        if more > 0:
            shown += "\n" + _say(language, f"- … ve {more} dosya daha", f"- … and {more} more files")
        lines.append(_say(language, "Bu turda çalışma klasöründe değişen dosyalar:",
                          "Files this turn changed in the workspace:") + "\n" + shown)
    elif files is not None and steps:
        lines.append(_say(language, "Çalışma klasöründe commit edilmemiş yeni bir değişiklik yok.",
                          "It left no new uncommitted changes in the workspace."))
    return "\n\n".join(lines)


def limit_message(kind: str, *, language: str, summary: str, started: bool) -> str:
    """The localized text of a turn stopped by a time limit.

    `kind` is "idle" (no output for _AGY_IDLE_LIMIT) or "total" (_AGY_MAX_TOTAL).
    The last line keeps the technical "timed out" wording for logs and support."""
    if kind == "idle":
        span = _span(BaseCLIProvider._AGY_IDLE_LIMIT, language)
        head = _say(language,
                    f"Antigravity {span} boyunca hiçbir çıktı vermediği için tur durduruldu.",
                    f"The Antigravity turn was stopped because it produced no output for {span}.")
        tech = f"agy turn timed out: no output for {BaseCLIProvider._AGY_IDLE_LIMIT} seconds."
    else:
        span = _span(BaseCLIProvider._AGY_MAX_TOTAL, language)
        head = (_say(language, f"Antigravity turu {span} süre sınırına ulaştı ve durduruldu.",
                     f"The Antigravity turn reached its {span} time limit and was stopped.")
                if started else
                _say(language,
                     f"Antigravity turu başlayamadı: başka bir agy sohbetinin turu {span} içinde bitmedi.",
                     f"The Antigravity turn could not start: another agy chat's turn did not finish within {span}."))
        tech = f"agy turn timed out after {BaseCLIProvider._AGY_MAX_TOTAL} seconds."
    tail = (_say(language,
                 "Sohbet korunuyor: \"devam et\" yazarsan Antigravity kaldığı yerden sürdürür.",
                 "The conversation is kept: send \"continue\" and Antigravity picks up where it stopped.")
            if started else
            _say(language, "Mesajını yeniden gönderebilirsin.", "You can send your message again."))
    parts = [head] + ([summary] if summary else []) + [tail, f"({tech})"]
    return "\n\n".join(parts)


def _dedupe_tail(message: str, tail: str) -> str:
    """The stderr lines that do not just repeat the message."""
    seen = {line.strip() for line in message.splitlines() if line.strip()}
    kept = []
    for line in tail.splitlines():
        if line.strip() and line.strip() not in seen:
            seen.add(line.strip())
            kept.append(line)
    return "\n".join(kept)


def agy_turn_busy() -> bool:
    """Is an agy turn running or waiting right now? (The side route's gate.)"""
    return _turn_lock_busy(BaseCLIProvider._AGY_LOCK)


def _updater_env(platform: Optional[str] = None) -> Dict[str, str]:
    """Switches off agy's self-updater on Windows, where it flashes a terminal.

    At most every 15 minutes a starting agy spawns `agy --bg-updater` with no
    console, and that worker's `agy --version` child gets a new, visible one
    (measured 26 Sep 2026, agy 1.2.11, Windows 11: one window per check). Our
    CREATE_NO_WINDOW cannot reach that grandchild, and neither did starting agy
    on a hidden console (measured). agy accepts "true" only: with "1" the
    updater still ran (measured). agy still updates when the user runs it.
    """
    if (platform or sys.platform) == "win32":
        return {"AGY_CLI_DISABLE_AUTO_UPDATE": "true"}
    return {}


def _switch_state() -> tuple:
    """Whether the computer-use, agent-browser and connectors servers are on right now."""
    from computer_use import registration as computer_registration
    from browser_use import registration as browser_registration
    from connectors import registration as connectors_registration
    return (computer_registration.stdio_entry() is not None,
            browser_registration.stdio_entry() is not None,
            connectors_registration.stdio_entry() is not None)


def _turn_lock_busy(lock: asyncio.Lock) -> bool:
    """Would acquiring the agy turn lock wait? Mirrors Lock.acquire's fast path:
    a just-released lock whose woken waiter has not run yet is still taken."""
    waiters = getattr(lock, "_waiters", None) or ()
    return lock.locked() or any(not waiter.cancelled() for waiter in waiters)


def _discard_lock_acquire(task: "asyncio.Future", lock: asyncio.Lock) -> None:
    """Abandon an acquire task; a lock it already won is released, not stranded."""
    def _release_if_won(done: "asyncio.Future") -> None:
        if not done.cancelled() and done.exception() is None:
            lock.release()
    if task.done():
        _release_if_won(task)
    else:
        task.cancel()
        task.add_done_callback(_release_if_won)


class AgyWorkspaceError(RuntimeError):
    """The requested workspace was invalid before the live child was touched."""


def _global_auto_mode() -> bool:
    """The one global approval mode.

    Not `self.auto_approve` from the request: the hook's state file must
    follow the backend's mode, whoever set the flag. Unreadable counts as
    step (fail closed).
    """
    try:
        from agentic import approval_mode
        return approval_mode.is_auto()
    except Exception:
        logger.warning("[agy] approval mode unreadable; treating it as step", exc_info=True)
        return False


def _global_gate_mode() -> str:
    """The hook state for the published mode: "auto", "balanced" or "step".

    Asks _global_auto_mode first, so auto has one source; anything that is
    not auto and not balanced, unreadable included, is step. Callers that
    write the file hold _gate_lock(), under which the mode cannot change
    between the two reads (set_mode publishes under the same lock).
    """
    if _global_auto_mode():
        return "auto"
    try:
        from agentic import approval_mode
        return "balanced" if approval_mode.current_mode() == "balanced" else "step"
    except Exception:
        logger.warning("[agy] approval mode unreadable; treating it as step", exc_info=True)
        return "step"


def _gate_lock():
    """approval_mode.GATE_LOCK, the one lock over the mode's publish and every
    write of the hook's state file. The invariant it enforces, and the
    ordering rule every writer here follows, are stated there.

    Imported late: the agentic package imports agent_runner, which imports
    providers, so a module-level import here would be circular.
    """
    from agentic import approval_mode
    return approval_mode.GATE_LOCK


# Every agy child whose hook may read the state file, from the spawn's state
# write until the child is reaped: token -> process (None while spawning). A
# closing child stays here after close() drops its session from _SESSIONS,
# which is what lets a flip to step still reach it (verification round 2,
# 26 Sep 2026: close deregistered first, so a flip in that gap found no
# session and left the closing child's hook in auto). Guarded by _gate_lock().
_GATED_CHILDREN: Dict[object, object] = {}
# The _GATED_CHILDREN tokens of closed sessions whose kill failed, until their
# child is stopped. (Not every closing child: one killed but not yet reaped
# would refuse spawns for nothing.)
# Only the one shared state file stands between such a child and its tools, so
# no new child (whose spawn writes that file) starts while one still runs; see
# _stop_retired_children. Guarded by _gate_lock().
_RETIRED: set = set()
# The session whose side question holds the agy turn lock, else None. While
# it is set every write of the state file writes SIDE_MODE, whatever the
# approval mode: a flip during the side turn must not loosen it. Guarded by
# _gate_lock(). _SIDE_DONE is set once that turn has released the lock.
_SIDE_TURN = None
_SIDE_DONE: Optional[asyncio.Event] = None


def _effective_gate_mode() -> str:
    """What the state file must say now: SIDE_MODE during a side turn, else
    the published approval mode (_global_gate_mode)."""
    if _SIDE_TURN is not None:
        return SIDE_MODE
    return _global_gate_mode()


def _begin_side_turn(session) -> None:
    """Called with the turn lock just taken; the side child's _start then
    writes SIDE_MODE (spawn or resync) before its first stdin line."""
    global _SIDE_TURN, _SIDE_DONE
    with _gate_lock():
        _SIDE_TURN = session
        _SIDE_DONE = asyncio.Event()


def _end_side_turn(session) -> None:
    """Called before the turn lock is released: puts the state file back to
    the mode published NOW (not the one before the turn: a flip to step
    during the turn must not be undone by a stale auto)."""
    global _SIDE_TURN
    with _gate_lock():
        if _SIDE_TURN is not session:
            return
        _SIDE_TURN = None
        if session._gate_token in _RETIRED:
            # Its child survived a kill: the file keeps denying ("side" or
            # "closed"), and _start refuses new children until it is stopped.
            logger.error("[agy] side child still alive after close; gate state left closed")
            return
        _sync_gate_state()


async def _preempt_side_turn(requester) -> None:
    """Any other agy turn wins over a running side question (Burak, 27 Sep
    2026): the side child is closed, and the caller then waits until that
    turn has restored the state file and released the lock."""
    side, done = _SIDE_TURN, _SIDE_DONE
    if side is None or side is requester:
        return
    logger.info("[agy] side question conv=%s stopped for conv=%s",
                side.conversation_id, getattr(requester, "conversation_id", None))
    side._side_preempted = True
    await side._close_safely(preserve_resume=True)
    if done is not None:
        try:
            await asyncio.wait_for(done.wait(), timeout=10)
        except asyncio.TimeoutError:
            logger.warning("[agy] preempted side question has not released the turn lock yet")


def _stop_retired_children() -> list:
    """Retry the kill of every closed session's child; returns the survivors.

    Verification round 4 (26 Sep 2026): a lone closing child whose kill failed
    was denied by a "closed" file, and the next spawn rewrote the file to auto,
    giving the closed child its tools back. Caller holds _gate_lock().
    """
    survivors = []
    for token in list(_RETIRED):
        process = _GATED_CHILDREN.get(token)
        if process is None or getattr(process, "returncode", None) is not None:
            _RETIRED.discard(token)
            continue
        try:
            process_tree.end_tree_sync(process, strict=True)
        except Exception:
            logger.warning("[agy] closed child still not stoppable pid=%s",
                           getattr(process, "pid", None), exc_info=True)
            if getattr(process, "returncode", None) is None:
                survivors.append(process)
                continue
        # Killed: the owning session's reap may never run (it was closed), so
        # the child stops being gated here.
        _GATED_CHILDREN.pop(token, None)
        _RETIRED.discard(token)
    return survivors


def _remove_gate_state() -> bool:
    """True when the state file is gone (a hook with no state file denies)."""
    from . import agy_provider
    try:
        os.remove(agy_provider.gate_state_path())
        return True
    except FileNotFoundError:
        return True
    except OSError:
        logger.error("[agy] gate state could not be removed", exc_info=True)
        return False


def _sync_gate_state() -> bool:
    """Rewrite the hook's state file from the published global mode, now.

    Every agy process has the hook (agy_provider._write_step_gate installs it
    in both modes) and the hook reads this one global file on every tool
    call. The mode is read and the file written under _gate_lock(), so this
    can never write auto after step was published.

    False only when step or balanced mode is on and the file may still say
    auto; the caller must then stop the process. A failed write then first
    tries to delete the file, since a hook with no state file denies
    everything.
    """
    from . import agy_provider
    with _gate_lock():
        mode = _effective_gate_mode()
        try:
            if mode in ("balanced", SIDE_MODE):
                agy_provider.write_gate_state(mode=mode)
            else:
                agy_provider.write_gate_state(auto=(mode == "auto"))
            return True
        except Exception:
            logger.warning("[agy] gate state not rewritten (mode=%s)", mode, exc_info=True)
            if mode == "auto":
                return True  # a stale "step" only over-restricts
            # A stale "auto" must not outlive balanced either.
            return _remove_gate_state()


_UNKNOWN = object()


def _may_have_child(session) -> bool:
    # A registered object this module cannot inspect counts as having one.
    return getattr(session, "_active_process", _UNKNOWN) is not None


def _kill_gated_children() -> list:
    """Kill every child that may read the state file; returns the ones whose
    kill failed while they still run."""
    processes = [p for p in _GATED_CHILDREN.values() if p is not None]
    processes += [s._active_process for s in _SESSIONS.values()
                  if getattr(s, "_active_process", None) is not None]
    # A process is listed once per registry it is in; one kill that works stops
    # it, whatever an earlier attempt raised (verification round 4).
    stopped, survivors = set(), {}
    for process in processes:
        if id(process) in stopped:
            continue
        try:
            process_tree.end_tree_sync(process, strict=True)
        except Exception:
            logger.exception("[agy] could not stop pid=%s", getattr(process, "pid", None))
            if getattr(process, "returncode", None) is None:
                survivors[id(process)] = process
                continue
        stopped.add(id(process))
        survivors.pop(id(process), None)
    return list(survivors.values())


def tighten_gate_state() -> None:
    """approval_mode calls this under GATE_LOCK before it publishes step.

    Nothing reads the file when no agy child exists, and a spawn writes it
    under the same lock, so then there is nothing to do. Otherwise the file
    must deny per the step grammar before step is published: write step,
    else remove it, else stop every child that could read it. If a child
    survives all three, this raises and step is not published (verification
    round 3, 26 Sep 2026: step was published over a live child whose hook
    still allowed).
    """
    from . import agy_provider
    with _gate_lock():
        if not _GATED_CHILDREN and not any(_may_have_child(s) for s in _SESSIONS.values()):
            return
        try:
            if _SIDE_TURN is not None:
                # Side is tighter than step; replacing it with step would
                # loosen a running side question.
                agy_provider.write_gate_state(mode=SIDE_MODE)
            else:
                agy_provider.write_gate_state(auto=False)
            return
        except Exception:
            logger.warning("[agy] gate state not tightened to step", exc_info=True)
        if _remove_gate_state():
            return
        logger.error("[agy] gate state still allows; stopping every agy child before step")
        survivors = _kill_gated_children()
        if survivors:
            pids = ", ".join(str(getattr(p, "pid", "?")) for p in survivors)
            raise AgyStepGateError(
                "Adım adım onay moduna geçilemedi: agy'nin onay kapısı "
                f"({agy_provider.gate_state_path()}) güncellenemedi ve çalışan agy "
                f"süreci durdurulamadı (pid {pids}).\n"
                "Mod değişmedi. agy sürecini kapatın ya da uygulamayı yeniden başlatın, "
                "sonra yeniden deneyin.",
                code="agy_step_refused", pids=pids)


# Observed agy tool payloads nest three or four levels; 40 is far above that and
# far below CPython's 1000-frame limit, so a hostile 2000-level child payload is
# truncated here instead of raising RecursionError and killing the turn.
_REDACTION_MAX_DEPTH = 40
_REDACTION_TRUNCATED = "[redacted: nesting too deep]"
_REDACTION_FAILED = "[redacted]"


def _redact_event(value, _depth: int = 0):
    """The single egress point: everything stream() yields is redacted here."""
    try:
        if _depth > _REDACTION_MAX_DEPTH:
            return _REDACTION_TRUNCATED
        if isinstance(value, str):
            return redact_secrets(value)
        if isinstance(value, Mapping):
            return {_redact_event(key, _depth + 1): _redact_event(item, _depth + 1)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [_redact_event(item, _depth + 1) for item in value]
        return value
    except Exception:
        # A redactor that raises would leak by aborting the turn, not by masking.
        logger.debug("[agy] event redaction fell back to a placeholder", exc_info=True)
        return _REDACTION_FAILED


class AgyStreamSession(SaglayiciSahipligi):
    """Own the process, its stderr reader, and its serialized stdin turns."""

    def __init__(self, conversation_id: int, *, resume_id: Optional[str] = None,
                 cwd: str = "."):
        # A negative conversation ID marks a throwaway one-shot session; it is
        # never in the _RESUME_IDS store, and sits in _SESSIONS only while its
        # process lives (see _start).
        self.conversation_id = conversation_id
        self.cwd = os.path.abspath(cwd)
        self.session_id = resume_id if conversation_id >= 0 else None
        self.model = None
        self._model_slug = None
        self._switches = (False, False)
        self._active_process = None
        self._gate_token = None  # this child's key in _GATED_CHILDREN
        self._auto_approve = False
        self._stderr_task = None
        self._stderr_tail = b""
        self._usage_totals = {}
        self._num_turns = 0
        self._stop_lock = asyncio.Lock()
        self._closed_event = asyncio.Event()
        self._side_preempted = False  # set by _preempt_side_turn
        self._last_used = time.monotonic()  # end of the last turn, for stop_idle_children
        self._sahiplik_kur()

    def kapandi_isaretle(self) -> None:
        super().kapandi_isaretle()
        self._closed_event.set()  # ends a turn queued in _acquire_turn_lock

    async def _acquire_turn_lock(self, lock: asyncio.Lock) -> bool:
        """Wait for the global agy turn lock; False when the session closed first.

        The acquire is its own task so Stop (close_session) can end a queued
        wait without cancelling the caller. Awaiting the lock directly kept a
        stopped turn in the lock's queue until the running turn finished.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        acquire = asyncio.ensure_future(lock.acquire())
        closed = asyncio.ensure_future(self._closed_event.wait())
        won = False
        try:
            done, _ = await asyncio.wait({acquire, closed}, timeout=5,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                logger.info("[agy] turn lock wait exceeded five seconds conv=%s waited=%.1fs",
                            self.conversation_id, loop.time() - started)
                await asyncio.wait({acquire, closed}, return_when=asyncio.FIRST_COMPLETED)
            won = acquire.done() and not acquire.cancelled() and acquire.exception() is None
            return won
        finally:
            closed.cancel()
            if not won:
                _discard_lock_acquire(acquire, lock)

    @property
    def auto_approve(self) -> bool:
        return self._auto_approve

    @auto_approve.setter
    def auto_approve(self, value: bool) -> None:
        """approval_mode._propagate_to_live_sessions sets this on every flip.

        The value itself is not trusted (agent_runner also sets it from the
        request): while a process is live, the hook's state file is rewritten
        from the global mode, so a flip either way bites on that process's
        next tool call. It has the hook whatever mode it was spawned in.
        """
        self._auto_approve = bool(value)
        if self.is_live and not _sync_gate_state():
            # Step mode and the hook may still allow: this process must not
            # keep running. Its turn then ends with the exit error.
            logger.error("[agy] gate state stale in step mode; stopping pid=%s",
                         getattr(self._active_process, "pid", None))
            try:
                process_tree.end_tree_sync(self._active_process, strict=True)
            except Exception:
                logger.exception("[agy] could not stop the child with a stale gate state")

    @property
    def is_live(self) -> bool:
        return (not self._kapandi and self._active_process is not None
                and self._active_process.returncode is None)

    def _remember_id(self, session_id) -> None:
        if isinstance(session_id, str) and session_id:
            self.session_id = session_id
            if self.conversation_id >= 0:
                _RESUME_IDS[(self.conversation_id, self.cwd)] = session_id

    async def _drain_stderr(self, process) -> None:
        while True:
            chunk = await process.stderr.read(4096)
            if not chunk:
                break
            self._stderr_tail = (self._stderr_tail + chunk)[-8192:]

    async def _stop_process(self, *, force: bool = False) -> None:
        # Stop and the stdout EOF path can arrive together; reap each child once.
        async with self._stop_lock:
            await self._reap_process(force=force)

    async def _reap_process(self, *, force: bool) -> None:
        process = self._active_process
        if process is None:
            return
        pid = getattr(process, "pid", None)
        reaped = False
        kill_retry_failed = False

        async def _kill_with_retry() -> bool:
            if await process_tree.end_tree(process):
                return True
            if process.returncode is not None:
                return True
            await asyncio.sleep(0.05)
            return await process_tree.end_tree(process) or process.returncode is not None

        try:
            if force and process.returncode is None:
                if not await _kill_with_retry():
                    kill_retry_failed = True
                    return
            if process.stdin is not None:
                process.stdin.close()
            try:
                wait_result = await asyncio.wait_for(process.wait(), timeout=3)
                reaped = process.returncode is not None or wait_result is not None
            except asyncio.TimeoutError:
                if process.returncode is None:
                    if not await _kill_with_retry():
                        kill_retry_failed = True
                        return
                    try:
                        wait_result = await asyncio.wait_for(process.wait(), timeout=3)
                        reaped = process.returncode is not None or wait_result is not None
                    except asyncio.TimeoutError:
                        pass
                else:
                    reaped = True
        except (ProcessLookupError, BrokenPipeError, ConnectionResetError):
            reaped = process.returncode is not None
        finally:
            if self._stderr_task is not None:
                try:
                    await asyncio.wait_for(self._stderr_task, timeout=1)
                except (asyncio.TimeoutError, asyncio.CancelledError, OSError):
                    self._stderr_task.cancel()
                self._stderr_task = None
            # A closing child that could not be killed keeps its handle, so a
            # later close() or spawn retries the kill (verification round 4:
            # clearing it left no path that could ever stop the child).
            if reaped or (kill_retry_failed and not self._kapandi):
                self._active_process = None
                if self.active_provider is self:
                    self.active_provider = None
            if reaped:
                # Only a reaped child stops being gated; one that could not be
                # killed stays tracked, so later flips still reach its file.
                with _gate_lock():
                    if _GATED_CHILDREN.get(self._gate_token) is process:
                        _GATED_CHILDREN.pop(self._gate_token, None)
                    _RETIRED.discard(self._gate_token)
                logger.info("[agy] child stopped pid=%s exit_status=%s",
                            pid, getattr(process, "returncode", None))
            else:
                logger.warning("[agy] child could not be reaped pid=%s", pid)

    async def cancel_active_process(self) -> bool:
        was_live = self.is_live
        await self._stop_process(force=True)
        return was_live

    def _retire_gate(self) -> None:
        """First step of close(): deny-all, then deregister; the stop follows.

        A closing child never needs a tool again. So before its session leaves
        _SESSIONS, its hook is made to deny everything and the child is sent
        its kill; it stays in _GATED_CHILDREN until reaped, so a flip in the
        gap still reaches its file. The state file is shared by every agy
        child, so it is set to "closed" only when no other child may be
        reading it; otherwise it keeps following the mode (the other children
        need it), which in step mode already denies per the step grammar.

        So beside another child in auto mode, only the kill takes this child's
        write access away. If that kill fails, the session stays registered
        until the child is reaped (close() deregisters it then), instead of
        leaving a live, writing child that no session owns (verification
        round 3, 26 Sep 2026).
        """
        from . import agy_provider
        with _gate_lock():
            process = self._active_process
            still_writing = False
            if process is not None:
                if _GATED_CHILDREN.get(self._gate_token, process) is not process:
                    self._gate_token = None
                if self._gate_token is None:
                    self._gate_token = object()  # a child not spawned by _start
                _GATED_CHILDREN[self._gate_token] = process
                others = any(token is not self._gate_token for token in _GATED_CHILDREN) or any(
                    session is not self and _may_have_child(session)
                    for session in _SESSIONS.values())
                denied = False
                if not others:
                    try:
                        agy_provider.write_gate_state(auto=False, closed=True)
                        denied = True
                    except Exception:
                        logger.warning("[agy] closed gate state not written", exc_info=True)
                        # If this fails too, the kill below stops it.
                        denied = _remove_gate_state()
                if process.returncode is None:
                    try:
                        process_tree.end_tree_sync(process, strict=True)
                    except Exception:
                        # Already gone, or the kill failed: the reap below
                        # retries it, and the child stays tracked until then.
                        logger.debug("[agy] kill on close failed pid=%s",
                                     getattr(process, "pid", None), exc_info=True)
                        if process.returncode is None:
                            _RETIRED.add(self._gate_token)
                        still_writing = not denied and process.returncode is None
            if not still_writing and _SESSIONS.get(self.conversation_id) is self:
                _SESSIONS.pop(self.conversation_id, None)

    async def close(self, *, preserve_resume: bool = False) -> None:
        self._retire_gate()
        if not preserve_resume and self.conversation_id >= 0:
            _RESUME_IDS.pop((self.conversation_id, self.cwd), None)
            _QUOTA_FAILURES.pop((self.conversation_id, self.cwd), None)
        await oturumu_kapat(self)
        await self._stop_process(force=True)
        with _gate_lock():
            # _retire_gate kept a child it could not stop registered; once the
            # reap untracks it, the session can go.
            if (_GATED_CHILDREN.get(self._gate_token) is None
                    and _SESSIONS.get(self.conversation_id) is self):
                _SESSIONS.pop(self.conversation_id, None)

    async def _start(self, model: str, cwd: str, thinking_level: str = "auto") -> str:
        if self._kapandi:
            raise RuntimeError("agy session was stopped.")
        if not os.path.isdir(cwd):
            raise AgyWorkspaceError("agy workspace directory does not exist.")
        # The process holds its model slug and the workspace hooks for its
        # life. A model or effort-derived slug change
        # requires closing and respawning,
        # while retaining the UUID. An approval-mode change does not: every
        # process has the hook, and the mode is only in the hook's state file.
        # Every turn start writes the file below (spawn or resync), which is
        # what clears a "side" a crashed side turn may have left behind.
        mode = _effective_gate_mode()
        self._auto_approve = mode == "auto"
        model_slug = AgyProvider._resolve_agy_model(model, thinking_level)
        # The servers are registered in the global config at spawn only, so a
        # switch flip needs a new child to take effect.
        switches = _switch_state()
        if self._active_process is not None and (
            not self.is_live or self.cwd != cwd
            or self._model_slug != model_slug
            or self._switches != switches
        ):
            await self._stop_process()
        if self._kapandi:
            raise RuntimeError("agy session was stopped.")
        if self.is_live:
            self.model = model
            if mode == SIDE_MODE:
                # A main child spawned since this one narrowed hooks.json.
                # That agy reads it only at start is not measured here, so
                # the side matchers go back before a kept side child's turn;
                # this also writes "side" and verifies both, or raises.
                try:
                    with _gate_lock():
                        AgyProvider(binary_name=model)._write_step_gate(
                            cwd, step_mode=True, mode=SIDE_MODE)
                except AgyStepGateError:
                    await self._stop_process(force=True)
                    raise
                return ""
            # Self-healing before each turn on a kept process: a flip whose
            # rewrite failed must not carry into this turn.
            if not _sync_gate_state():
                await self._stop_process(force=True)
                raise AgyStepGateError(_gate_write_failed(gate_state_path(), "yazılamadı"))
            return ""
        if self.cwd != cwd:
            self.session_id = (_RESUME_IDS.get((self.conversation_id, cwd))
                               if self.conversation_id >= 0 else None)
        self.cwd = cwd
        provider = AgyProvider(binary_name=model)
        provider._resume_uuid = self.session_id
        command = provider._resolve_exec(provider._build_cmd(thinking_level=thinking_level, workspace=cwd))
        # Configuration happens under the same global turn lock as execution.
        # These existing helpers are mocked by the fake-process tests.
        provider._write_mcp_config(cwd)
        provider._register_agy_workspace(cwd)
        token = object()
        try:
            # The mode is read and the state written in one critical section
            # (the ordering rule at approval_mode.GATE_LOCK), and the child is
            # gated from this write on: a flip to step before the spawn below
            # rewrites this file before step is published.
            with _gate_lock():
                survivors = _stop_retired_children()
                if survivors:
                    pids = ", ".join(str(getattr(p, "pid", "?")) for p in survivors)
                    raise AgyStepGateError(
                        f"Kapatılan bir agy süreci durdurulamadı (pid {pids}); o çalışırken "
                        "yeni bir agy süreci başlatılmadı, çünkü ikisi aynı onay kapısını "
                        "paylaşıyor. O süreci kapatın ya da uygulamayı yeniden başlatın, "
                        "sonra mesajınızı yeniden gönderin.",
                        code="agy_closed_child_alive", pids=pids)
                mode = _effective_gate_mode()
                self._auto_approve = mode == "auto"
                _GATED_CHILDREN[token] = None
                # In every mode this raises AgyStepGateError unless the gate
                # is verifiably installed, so agy is never spawned ungated;
                # stream() turns the raise into the user's error message.
                if mode in ("balanced", SIDE_MODE):
                    provider._write_step_gate(cwd, step_mode=True, mode=mode)
                else:
                    provider._write_step_gate(cwd, step_mode=(mode != "auto"))
            instructions = provider._stream_instructions()
            self._stderr_tail = b""
            self._usage_totals = {}
            self._num_turns = 0
            process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=cwd,
                # agy hands its whole env to stdio MCP servers (measured), so
                # the unityMCP bridge sees the chat id without a config entry;
                # the config files are shared by every chat.
                env=build_spawn_env(family="agy", overrides={
                    "NO_COLOR": "1", **_updater_env(),
                    **conversation_env(self.conversation_id)}),
                creationflags=_CREATE_NO_WINDOW,
                limit=BaseCLIProvider._CLI_STREAM_LIMIT_BYTES,
            )
        except BaseException:
            # No child: asyncio kills and waits one it created when the spawn
            # is cancelled.
            with _gate_lock():
                _GATED_CHILDREN.pop(token, None)
            raise
        with _gate_lock():
            _GATED_CHILDREN[token] = process
            self._gate_token = token
        # Register before draining stdin, so Stop can reach a blocked writer.
        self._active_process = process
        self.active_provider = self
        self._stderr_task = asyncio.create_task(self._drain_stderr(process))
        self.model = model
        self._model_slug = provider._pending_agy_model_slug
        self._switches = switches
        if self.conversation_id < 0:
            # Visible to approval_mode's flip propagation while it runs; close()
            # removes it. A one-shot is never looked up by id.
            _SESSIONS[self.conversation_id] = self
        logger.info("[agy] child spawned pid=%s cwd=%s model=%s",
                    getattr(process, "pid", None), cwd, model)
        if self._kapandi:
            await self._stop_process(force=True)
            raise RuntimeError("agy session was stopped during startup.")
        # A flip while the state was written or the process was created found
        # no live process to update. agy makes no tool call before its first
        # stdin line, which stream() writes only after this returns.
        if _effective_gate_mode() != mode and not _sync_gate_state():
            await self._stop_process(force=True)
            raise AgyStepGateError(_gate_write_failed(gate_state_path(), "yazılamadı"))
        return instructions

    async def _close_safely(self, *, preserve_resume: bool = True) -> None:
        try:
            await self.close(preserve_resume=preserve_resume)
        except asyncio.CancelledError:
            # cancelling() is non-zero only when THIS task was cancelled, which is
            # the caller's cancellation and must reach the caller; a CancelledError
            # raised inside cleanup itself is just a failed cleanup.
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                logger.info("[agy] session cleanup cancelled with its caller conv=%s",
                            self.conversation_id)
                raise
            logger.exception("[agy] session cleanup failed conv=%s", self.conversation_id)
        except Exception:
            # Cleanup must not replace the original turn failure.
            logger.exception("[agy] session cleanup failed conv=%s", self.conversation_id)

    def _child_field_ok(self, name: str, value, kinds) -> bool:
        """Gate one child-supplied field; log once at debug and let the caller skip."""
        if isinstance(value, kinds):
            return True
        logger.debug("[agy] ignored malformed child %s conv=%s preview=%s",
                     name, self.conversation_id, repr(value)[:160])
        return False

    async def _discard_buffered_output(self, process) -> None:
        """Discard child lines already buffered after a terminal result."""
        stdout = getattr(process, "stdout", None)
        if stdout is None:
            return
        await asyncio.sleep(0)
        # A child that keeps writing would hold the finished turn here forever, so
        # the drain is best effort: both bounds are far above the handful of lines
        # a real post-result buffer holds.
        deadline = asyncio.get_running_loop().time() + _DRAIN_MAX_SECONDS
        for _ in range(_DRAIN_MAX_LINES):
            if not getattr(stdout, "_buffer", None):
                return
            line = await stdout.readline()
            if not line:
                return
            logger.debug("[agy] ignored post-result child output conv=%s preview=%r",
                         self.conversation_id, line[:160])
            if asyncio.get_running_loop().time() >= deadline:
                break
        logger.info("[agy] post-result drain bound reached conv=%s", self.conversation_id)

    def _turn_usage(self, usage: Mapping, elapsed: float) -> dict:
        # The captured second result contains cumulative process usage. Subtract
        # the preceding result, resetting the baseline on each process start.
        totals = {key: usage.get(key) or 0 for key in _USAGE_KEYS}
        usage = {key: max(0, value - self._usage_totals.get(key, 0))
                 for key, value in totals.items()}
        self._usage_totals = totals
        return {"type": "turn_usage", **usage, "cost_usd": None,
                "duration_ms": int(elapsed * 1000)}

    async def stream(self, message: str, *, model: str = "gemini-3.8-flash",
                     cwd: Optional[str] = None, side: bool = False,
                     language: str = "tr", thinking_level: str = "auto",
                     agent_guide: str = "") -> AsyncGenerator[dict, None]:
        """One turn. `side`: a read-only side question (Burak, 27 Sep 2026):
        refused unless the turn lock is free, run with the state file on
        SIDE_MODE, stopped after SIDE_TURN_TIMEOUT_S or when another agy turn
        starts; `language` picks the text of those stop messages."""
        self._last_used = time.monotonic()
        agent_guide = agent_guide or getattr(self, "agent_guide", "")
        if agent_guide and not side:
            import agent_guide as guides
            computer_on, browser_on, connectors_on = _switch_state()
            if computer_on:
                agent_guide = guides.with_computer_use(agent_guide)
            if browser_on:
                agent_guide = guides.with_browser_use(agent_guide)
            if connectors_on:
                agent_guide = guides.with_connectors(agent_guide)
        # Global serialization covers the complete turn, not the process life.
        # A queued turn rechecks the closed flag before spawning or writing.
        completed = False
        preserve_live_process = False
        lock_acquired = False
        side_started = False
        self._side_preempted = False
        # Released as the object it was acquired as, even if the class
        # attribute is rebound meanwhile (tests patch it).
        turn_lock = BaseCLIProvider._AGY_LOCK
        loop = asyncio.get_running_loop()
        tool_calls = set()
        tool_results = set()
        # Tail of the text streamed so far, and whether a tool step came after it.
        text_tail = ""
        tool_since_text = False
        streamed_text = []
        # For the message of a turn that ends abnormally: what it had done.
        tool_counts = {}
        failed_tools = 0
        turn_started = False
        workspace_before = None
        # Side turns keep their 83a7c6b6 behaviour: no filter, no summary.
        replay = _ReplayFilter()
        replay_record = None
        replay_key = (self.conversation_id, os.path.abspath(cwd or self.cwd))
        idle_limit = None if side else BaseCLIProvider._AGY_IDLE_LIMIT

        def text_event(text):
            nonlocal text_tail, tool_since_text
            if tool_since_text:
                text = block_break(text_tail) + text
                tool_since_text = False
            text_tail = (text_tail + text)[-2:]
            streamed_text.append(text)
            return _redact_event({"type": "text", "content": text})

        if not side:
            await _preempt_side_turn(self)
        try:
            async with asyncio.timeout(SIDE_TURN_TIMEOUT_S if side else BaseCLIProvider._AGY_MAX_TOTAL):
                if side:
                    if _turn_lock_busy(turn_lock):
                        preserve_live_process = True
                        yield _redact_event({"type": "error", "code": "agy_side_busy",
                                             "message": SIDE_BUSY_MESSAGE})
                        return
                    # The lock is free, so this acquire takes Lock.acquire's
                    # fast path and never suspends: a side turn never queues.
                    lock_acquired = await turn_lock.acquire()
                    _begin_side_turn(self)
                    side_started = True
                    queued = False
                else:
                    queued = _turn_lock_busy(turn_lock)
                    if queued:
                        yield _redact_event(_QUEUED_EVENT)
                    lock_acquired = await self._acquire_turn_lock(turn_lock)
                if not lock_acquired or self._kapandi:
                    yield _redact_event({"type": "error", "message": "agy session was stopped."})
                    return
                if queued:
                    yield _redact_event(_STARTED_EVENT)
                started = loop.time()
                turn_started = True
                workspace = os.path.abspath(cwd or self.cwd)
                if not side and self.conversation_id >= 0:
                    replay_record = take_quota_replay(replay_key)
                    if replay_record is not None:
                        replay = _ReplayFilter(replay_record["texts"])
                snapshot = None
                if not side:
                    # A kept child's stderr may still hold an earlier turn's
                    # lines (a 429 retry warning): they must not decide how
                    # this turn's failure reads.
                    self._stderr_tail = b""
                    # What the workspace held before this turn, for the files
                    # an unfinished turn reports; runs while the child starts.
                    snapshot = asyncio.ensure_future(asyncio.to_thread(workspace_state, workspace))
                previous_process = self._active_process
                instructions = await self._start(model, workspace, thinking_level)
                process = self._active_process
                if snapshot is not None:
                    # Taken before the prompt, so nothing in it is this turn's.
                    # A slow git only costs the file list, never the turn.
                    done, _ = await asyncio.wait({snapshot}, timeout=_SNAPSHOT_WAIT_S)
                    workspace_before = snapshot.result() if done else None
                # A kept process already holds the guide and the hint from its first turn.
                guide_prefix = agent_guide + "\n\n" if agent_guide and process is not previous_process else ""
                payload = {"event": "user", "message": {"content": guide_prefix + instructions + message}}
                process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                # A child that stops reading its stdin is as silent as one
                # that stops writing: the same idle limit covers both.
                await _within_idle(process.stdin.drain(), idle_limit)
                while True:
                    # A turn is cut for silence, not for length: the two
                    # measured 1800 s cuts were turns streaming steps the
                    # whole time (see cli_base._AGY_IDLE_LIMIT).
                    line = await _within_idle(process.stdout.readline(), idle_limit)
                    if not line:
                        await self._stop_process(force=True)
                        raise RuntimeError(f"agy process exited before result (rc={process.returncode}).")
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        logger.debug("[agy] ignored invalid child output conv=%s preview=%r",
                                     self.conversation_id, line[:160])
                        continue
                    if not isinstance(event, dict):
                        logger.debug("[agy] ignored non-object child output conv=%s preview=%s",
                                     self.conversation_id, repr(event)[:160])
                        continue
                    event_type = event.get("event")
                    if event_type == "init":
                        self._remember_id(event.get("conversation_id"))
                    elif event_type == "step_update":
                        step = event.get("step_update")
                        if not isinstance(step, Mapping):
                            logger.debug("[agy] ignored non-object child output conv=%s preview=%s",
                                         self.conversation_id, repr(event)[:160])
                            continue
                        if (step.get("conversation_id") and self.session_id
                                and step["conversation_id"] != self.session_id):
                            continue
                        self._remember_id(step.get("conversation_id"))
                        if step.get("step_type") != "agent_response" and (held := replay.end()):
                            yield text_event(held)
                        if step.get("step_type") == "agent_response":
                            text_delta = step.get("text_delta")
                            if not isinstance(text_delta, str):
                                logger.debug("[agy] ignored non-object child output conv=%s preview=%s",
                                             self.conversation_id, repr(event)[:160])
                                continue
                            text_delta = replay.feed(step.get("step_index"), text_delta) if text_delta else ""
                            if text_delta:
                                yield text_event(text_delta)
                        elif step.get("step_type") == "tool":
                            info = step.get("tool_info") or {}
                            if not self._child_field_ok("tool_info", info, Mapping):
                                continue
                            if not info.get("name"):
                                continue
                            tool_name = info["name"]
                            if not self._child_field_ok("tool name", tool_name, str):
                                continue
                            step_index = step.get("step_index")
                            # step_index becomes half of a set key below.
                            if not self._child_field_ok("step_index", step_index, Hashable):
                                continue
                            key = (step_index, tool_name)
                            if key not in tool_calls:
                                tool_calls.add(key)
                                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                                parameters = info.get("parameters") or {}
                                if isinstance(parameters, str):
                                    try:
                                        parameters = json.loads(parameters)
                                    except ValueError:
                                         parameters = {"summary": parameters}
                                tool_since_text = bool(text_tail)
                                yield _redact_event({"type": "tool_call", "tool": tool_name,
                                                     "arguments": parameters, "iteration": 1})
                            if key not in tool_results and (
                                step.get("state") in ("DONE", "ERROR")
                                or info.get("output") is not None or info.get("error")
                            ):
                                tool_results.add(key)
                                if info.get("error") or step.get("state") == "ERROR":
                                    failed_tools += 1
                                output = info.get("error") or info.get("output") or ""
                                if not isinstance(output, str):
                                    output = json.dumps(output, ensure_ascii=False)
                                yield _redact_event({
                                    "type": "tool_result", "tool": tool_name,
                                    "success": not bool(info.get("error")) and step.get("state") != "ERROR",
                                    "summary": output})
                    elif event_type == "result":
                        result = event.get("result")
                        if (not isinstance(result, Mapping)
                                or not isinstance(result.get("status"), str)):
                            logger.debug("[agy] ignored non-object child output conv=%s preview=%s",
                                         self.conversation_id, repr(event)[:160])
                            continue
                        if (result.get("conversation_id") and self.session_id
                                and result["conversation_id"] != self.session_id):
                            continue
                        # A malformed num_turns/usage drops that field, not the whole
                        # result: the turn's only terminal event would otherwise be
                        # dropped and the conversation would hang until the timeout.
                        num_turns = result.get("num_turns", self._num_turns + 1)
                        if not self._child_field_ok("num_turns", num_turns, int):
                            num_turns = self._num_turns + 1
                        if num_turns <= self._num_turns:
                            continue
                        self._remember_id(result.get("conversation_id"))
                        self._num_turns = num_turns
                        raw_usage = result.get("usage") or {}
                        if not self._child_field_ok("usage", raw_usage, Mapping):
                            raw_usage = {}
                        usage = self._turn_usage(raw_usage, loop.time() - started)
                        if result.get("status") != "SUCCESS":
                            raise RuntimeError(str(result.get("error") or result.get("response")
                                                   or f"agy result status: {result.get('status')}"))
                        # A failing result leaves held text to the except path.
                        if held := replay.end():
                            yield text_event(held)
                        completed = True
                        await self._discard_buffered_output(process)
                        break
                    elif event_type == "error":
                        raise RuntimeError(str(event.get("error") or event.get("message")
                                               or "agy reported an error."))
                # The turn timer ends at result, before delivering terminal events.
            yield _redact_event(usage)
            # Store what the user saw streaming (blocks separated); the CLI's
            # own result glues the blocks together.
            stored_answer = "".join(streamed_text)
            if not stored_answer.strip():
                # The failure text the stream held back stays out of here too.
                stored_answer = replay.clean(result.get("response") or "")
            if (replay_record is not None and not replay.removed
                    and _QUOTA_FAILURES.get(replay_key) is replay_record):
                # A successful turn with no replay: agy is past the failure.
                _QUOTA_FAILURES.pop(replay_key, None)
            yield _redact_event({"type": "response", "content": stored_answer})
            yield _redact_event({"type": "done", "iterations": 1,
                                 "session_id": self.session_id})
        except (asyncio.CancelledError, GeneratorExit):
            if not completed:
                await self._close_safely(preserve_resume=True)
            raise
        except Exception as exc:
            if isinstance(exc, AgyWorkspaceError):
                preserve_live_process = True
            else:
                await self._close_safely(preserve_resume=True)
            side_code = None
            if side and self._side_preempted:
                side_code = "agy_side_preempted"
            elif side and isinstance(exc, asyncio.TimeoutError):
                side_code = "agy_side_timeout"
            if side_code:
                # The stderr tail of a child killed on purpose says nothing useful.
                yield _redact_event({"type": "error", "code": side_code,
                                     "message": side_stop_message(side_code, language)})
                return
            tail = self._stderr_tail.decode("utf-8", errors="replace").strip()
            if side:
                yield _redact_event({"type": "error",
                                     "message": str(exc) + (f"\n{tail}" if tail else "")})
                return
            limit = ("idle" if isinstance(exc, _AgyIdleLimit)
                     else "total" if isinstance(exc, asyncio.TimeoutError) else None)
            # Only this turn's failure and stderr decide quota (F1); a replay
            # removed from the answer is an earlier failure with a stale reset.
            raw = "\n".join(part for part in (str(exc), tail) if part)
            quota = None if limit else quota_info(raw)
            # Held text is the start of the recorded failure: on another quota
            # failure it is that failure's replay, otherwise it streams.
            if (held := replay.end()) and not quota:
                yield text_event(held)
            if quota and self.conversation_id >= 0:
                extra = _dedupe_tail(str(exc), tail)
                record_quota_failure(replay_key, [
                    str(exc), extra if quota_info(extra) else "",
                    *(text for text in replay.answer_steps.values() if quota_info(text))])
            if limit or quota:
                files = None
                if turn_started and (limit or tool_counts):
                    # After the close above, so agy no longer writes.
                    files = await asyncio.to_thread(
                        changed_files, os.path.abspath(cwd or self.cwd), workspace_before)
                summary = turn_summary(tools=tool_counts, failed=failed_tools,
                                       files=files, language=language)
                if limit:
                    code = "agy_turn_limit"
                    text = limit_message(limit, language=language, summary=summary,
                                         started=turn_started)
                else:
                    # The raw text (repeated lines, AGY_ERROR JSON) is for the log.
                    logger.warning("[agy] quota exhausted conv=%s model=%s raw=%s",
                                   self.conversation_id, model, raw[:4000])
                    code = "agy_quota"
                    text = quota_message(quota, model, language)
                    if summary:
                        text += "\n\n" + summary
                event = {"type": "error", "code": code, "message": text}
                if limit:
                    event["limit"] = limit
                if files:
                    event["changed_files"] = files
                yield _redact_event(event)
                return
            message = str(exc)
            tail = _dedupe_tail(message, tail)
            yield _redact_event({"type": "error",
                                 "message": message + (f"\n{tail}" if tail else "")})
        finally:
            # Lock ordering: teardown runs inside, the release is the outer finally.
            # Releasing first let a queued turn spawn into the session this turn was
            # still closing; skipping the outer finally would strand the lock.
            try:
                if not preserve_live_process and not completed and not self._kapandi:
                    await self._close_safely(preserve_resume=True)
            finally:
                self._last_used = time.monotonic()
                done = _SIDE_DONE if side_started else None
                try:
                    if side_started:
                        # Before the release: the next turn must find the
                        # file back on the published mode, not on "side".
                        _end_side_turn(self)
                except Exception:
                    logger.exception("[agy] side turn end failed; gate state left on side")
                finally:
                    if lock_acquired:
                        turn_lock.release()
                    if done is not None:
                        done.set()


# Keep the public ownership type used by existing stop/lifecycle callers.
AgySession = AgyStreamSession


def get_session(conversation_id: int, *, resume_id: Optional[str] = None,
                cwd: str = ".") -> AgyStreamSession:
    if conversation_id < 0:
        return AgyStreamSession(conversation_id, cwd=cwd)
    session = _SESSIONS.get(conversation_id)
    if session is None or session._kapandi:
        # A closed session left registered by a failed kill (see _retire_gate)
        # cannot run turns; its child stays in _GATED_CHILDREN either way.
        known_id = _RESUME_IDS.get((conversation_id, os.path.abspath(cwd)), resume_id)
        session = AgyStreamSession(conversation_id, resume_id=known_id, cwd=cwd)
        _SESSIONS[conversation_id] = session
    return session


def peek_session(conversation_id: int) -> Optional[AgyStreamSession]:
    if conversation_id < 0:
        return None
    return _SESSIONS.get(conversation_id)


async def stop_idle_children(now: Optional[float] = None) -> list:
    """Stop the process tree of every kept child idle for _AGY_IDLE_STOP_S.

    The sessions stay registered with their resume ids, so the next turn's
    _start spawns a new child that resumes the conversation. Runs only while
    no agy turn holds the global lock, and holds it so none starts mid-stop."""
    lock = BaseCLIProvider._AGY_LOCK
    if _turn_lock_busy(lock):
        return []
    # The lock is free, so this takes Lock.acquire's fast path and never waits.
    await lock.acquire()
    stopped = []
    try:
        now = time.monotonic() if now is None else now
        for conversation_id, session in list(_SESSIONS.items()):
            idle = now - session._last_used
            if (conversation_id < 0 or session._kapandi or not session.is_live
                    or idle < _AGY_IDLE_STOP_S):
                continue
            process = session._active_process
            pid = getattr(process, "pid", None)
            try:
                work = await asyncio.to_thread(process_tree.has_running_shell_work, pid)
            except Exception:
                work = None
            if work is not False:
                logger.info("[agy] idle stop skipped conv=%s pid=%s: %s", conversation_id, pid,
                            "background work" if work else "process table unreadable")
                continue
            try:
                await session._stop_process(force=True)
            except Exception:
                logger.exception("[agy] idle stop failed conv=%s pid=%s", conversation_id, pid)
                continue
            if process is not None and process.returncode is None:
                # _reap_process has already dropped the reference, so the next
                # turn would spawn a second child next to this live one.
                logger.warning("[agy] idle stop could not end the child conv=%s pid=%s",
                               conversation_id, pid)
                continue
            stopped.append(conversation_id)
            logger.info("[agy] idle child stopped conv=%s pid=%s idle=%.0f min",
                        conversation_id, pid, idle / 60)
    finally:
        lock.release()
    return stopped


async def idle_stop_loop(interval: float = 60) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await stop_idle_children()
        except Exception:
            logger.exception("[agy] idle stop sweep failed")


async def close_session(conversation_id: int) -> None:
    if conversation_id < 0:
        return
    session = _SESSIONS.get(conversation_id)
    if session is not None:
        await session.close()
    for store in (_RESUME_IDS, _QUOTA_FAILURES):
        for key in list(store):
            if key[0] == conversation_id:
                store.pop(key, None)


async def close_all_sessions() -> None:
    for conversation_id in list(_SESSIONS):
        if conversation_id < 0:
            session = _SESSIONS.get(conversation_id)
            if session is not None:
                await session.close()
            continue
        await close_session(conversation_id)
    _RESUME_IDS.clear()
    _QUOTA_FAILURES.clear()
