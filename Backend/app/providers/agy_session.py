"""Persistent Antigravity stream-json sessions, one subprocess per conversation."""
import asyncio
import json
import logging
import os
from collections.abc import Hashable, Mapping
from typing import AsyncGenerator, Dict, Optional

from .agy_provider import AgyProvider, AgyStepGateError, _gate_write_failed, gate_state_path
from .cli_base import BaseCLIProvider, _CREATE_NO_WINDOW, build_spawn_env
from .saglayici_sahipligi import SaglayiciSahipligi, oturumu_kapat
from secret_redaction import redact_secrets

_SESSIONS: Dict[int, "AgyStreamSession"] = {}
# Retain an init UUID even if a process dies before done can reach the disk store.
_RESUME_IDS: Dict[tuple, str] = {}
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens",
               "thinking_tokens", "total_tokens")
_DRAIN_MAX_LINES = 200
_DRAIN_MAX_SECONDS = 0.5
logger = logging.getLogger(__name__)


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

    False only when step mode is on and the file may still say auto; the
    caller must then stop the process. A failed write in step mode first tries
    to delete the file, since a hook with no state file denies everything.
    """
    from . import agy_provider
    with _gate_lock():
        auto = _global_auto_mode()
        try:
            agy_provider.write_gate_state(auto=auto)
            return True
        except Exception:
            logger.warning("[agy] gate state not rewritten (auto=%s)", auto, exc_info=True)
            if auto:
                return True  # a stale "step" only over-restricts
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
    survivors = []
    for process in processes:
        try:
            process.kill()
        except Exception:
            logger.exception("[agy] could not stop pid=%s", getattr(process, "pid", None))
            if getattr(process, "returncode", None) is None:
                survivors.append(process)
    return survivors


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
            agy_provider.write_gate_state(auto=False)
            return
        except Exception:
            logger.warning("[agy] gate state not tightened to step", exc_info=True)
        if _remove_gate_state():
            return
        logger.error("[agy] gate state still allows; stopping every agy child before step")
        survivors = _kill_gated_children()
        if survivors:
            raise AgyStepGateError(
                "Adım adım onay moduna geçilemedi: agy'nin onay kapısı "
                f"({agy_provider.gate_state_path()}) güncellenemedi ve çalışan agy "
                f"süreci durdurulamadı (pid {', '.join(str(getattr(p, 'pid', '?')) for p in survivors)}).\n"
                "Mod değişmedi. agy sürecini kapatın ya da uygulamayı yeniden başlatın, "
                "sonra yeniden deneyin.")


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
        self._active_process = None
        self._gate_token = None  # this child's key in _GATED_CHILDREN
        self._auto_approve = False
        self._stderr_task = None
        self._stderr_tail = b""
        self._usage_totals = {}
        self._num_turns = 0
        self._stop_lock = asyncio.Lock()
        self._sahiplik_kur()

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
                self._active_process.kill()
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
            try:
                process.kill()
                return True
            except OSError:
                if process.returncode is not None:
                    return True
                await asyncio.sleep(0.05)
                try:
                    process.kill()
                    return True
                except OSError:
                    return process.returncode is not None

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
            if reaped or kill_retry_failed:
                self._active_process = None
                if self.active_provider is self:
                    self.active_provider = None
            if reaped:
                # Only a reaped child stops being gated; one that could not be
                # killed stays tracked, so later flips still reach its file.
                with _gate_lock():
                    if _GATED_CHILDREN.get(self._gate_token) is process:
                        _GATED_CHILDREN.pop(self._gate_token, None)
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
                        process.kill()
                    except Exception:
                        # Already gone, or the kill failed: the reap below
                        # retries it, and the child stays tracked until then.
                        logger.debug("[agy] kill on close failed pid=%s",
                                     getattr(process, "pid", None), exc_info=True)
                        still_writing = not denied and process.returncode is None
            if not still_writing and _SESSIONS.get(self.conversation_id) is self:
                _SESSIONS.pop(self.conversation_id, None)

    async def close(self, *, preserve_resume: bool = False) -> None:
        self._retire_gate()
        if not preserve_resume and self.conversation_id >= 0:
            _RESUME_IDS.pop((self.conversation_id, self.cwd), None)
        await oturumu_kapat(self)
        await self._stop_process(force=True)
        with _gate_lock():
            # _retire_gate kept a child it could not stop registered; once the
            # reap untracks it, the session can go.
            if (_GATED_CHILDREN.get(self._gate_token) is None
                    and _SESSIONS.get(self.conversation_id) is self):
                _SESSIONS.pop(self.conversation_id, None)

    async def _start(self, model: str, cwd: str) -> str:
        if self._kapandi:
            raise RuntimeError("agy session was stopped.")
        if not os.path.isdir(cwd):
            raise AgyWorkspaceError("agy workspace directory does not exist.")
        # The process holds ~/.gemini/settings.json state and the workspace
        # hooks for its life. A model change requires closing and respawning,
        # while retaining the UUID. An approval-mode change does not: every
        # process has the hook, and the mode is only in the hook's state file.
        auto = _global_auto_mode()
        self._auto_approve = auto
        if self._active_process is not None and (
            not self.is_live or self.model != model or self.cwd != cwd
        ):
            await self._stop_process()
        if self._kapandi:
            raise RuntimeError("agy session was stopped.")
        if self.is_live:
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
        command = provider._resolve_exec(provider._build_cmd(workspace=cwd))
        # Configuration happens under the same global turn lock as execution.
        # These existing helpers are mocked by the fake-process tests.
        provider._write_mcp_config(cwd)
        provider._set_agy_model(provider._pending_agy_model, cwd)
        token = object()
        try:
            # The mode is read and the state written in one critical section
            # (the ordering rule at approval_mode.GATE_LOCK), and the child is
            # gated from this write on: a flip to step before the spawn below
            # rewrites this file before step is published.
            with _gate_lock():
                auto = _global_auto_mode()
                self._auto_approve = auto
                _GATED_CHILDREN[token] = None
                # In either mode this raises AgyStepGateError unless the gate
                # is verifiably installed, so agy is never spawned ungated;
                # stream() turns the raise into the user's error message.
                provider._write_step_gate(cwd, step_mode=not auto)
            instructions = provider._stream_instructions()
            self._stderr_tail = b""
            self._usage_totals = {}
            self._num_turns = 0
            process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=cwd,
                env=build_spawn_env(family="agy", overrides={"NO_COLOR": "1"}),
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
        if _global_auto_mode() != auto and not _sync_gate_state():
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

    async def stream(self, message: str, *, model: str = "gemini-3.6-flash",
                     cwd: Optional[str] = None) -> AsyncGenerator[dict, None]:
        # Global serialization covers the complete turn, not the process life.
        # A queued turn rechecks the closed flag before spawning or writing.
        completed = False
        preserve_live_process = False
        lock_acquired = False
        lock_wait_logged = False
        loop = asyncio.get_running_loop()
        tool_calls = set()
        tool_results = set()
        try:
            async with asyncio.timeout(BaseCLIProvider._AGY_MAX_TOTAL):
                lock_wait_started = loop.time()
                try:
                    await asyncio.wait_for(BaseCLIProvider._AGY_LOCK.acquire(), timeout=5)
                except asyncio.TimeoutError:
                    lock_waited = loop.time() - lock_wait_started
                    logger.info("[agy] turn lock wait exceeded five seconds conv=%s waited=%.1fs",
                                self.conversation_id, lock_waited)
                    lock_wait_logged = True
                    await BaseCLIProvider._AGY_LOCK.acquire()
                lock_acquired = True
                lock_waited = loop.time() - lock_wait_started
                if lock_waited > 5 and not lock_wait_logged:
                    logger.info("[agy] turn lock wait exceeded five seconds conv=%s waited=%.1fs",
                                self.conversation_id, lock_waited)
                if self._kapandi:
                    yield _redact_event({"type": "error", "message": "agy session was stopped."})
                    return
                started = loop.time()
                instructions = await self._start(model, os.path.abspath(cwd or self.cwd))
                process = self._active_process
                payload = {"event": "user", "message": {"content": instructions + message}}
                process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                await process.stdin.drain()
                while True:
                    line = await process.stdout.readline()
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
                        if step.get("step_type") == "agent_response":
                            text_delta = step.get("text_delta")
                            if not isinstance(text_delta, str):
                                logger.debug("[agy] ignored non-object child output conv=%s preview=%s",
                                             self.conversation_id, repr(event)[:160])
                                continue
                            if text_delta:
                                yield _redact_event({"type": "text", "content": text_delta})
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
                                parameters = info.get("parameters") or {}
                                if isinstance(parameters, str):
                                    try:
                                        parameters = json.loads(parameters)
                                    except ValueError:
                                         parameters = {"summary": parameters}
                                yield _redact_event({"type": "tool_call", "tool": tool_name,
                                                     "arguments": parameters, "iteration": 1})
                            if key not in tool_results and (
                                step.get("state") in ("DONE", "ERROR")
                                or info.get("output") is not None or info.get("error")
                            ):
                                tool_results.add(key)
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
                        completed = True
                        await self._discard_buffered_output(process)
                        break
                    elif event_type == "error":
                        raise RuntimeError(str(event.get("error") or event.get("message")
                                               or "agy reported an error."))
                # The turn timer ends at result, before delivering terminal events.
            yield _redact_event(usage)
            yield _redact_event({"type": "response", "content": result.get("response") or ""})
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
            message = (f"agy turn timed out after {BaseCLIProvider._AGY_MAX_TOTAL} seconds."
                       if isinstance(exc, asyncio.TimeoutError) else str(exc))
            tail = self._stderr_tail.decode("utf-8", errors="replace").strip()
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
                if lock_acquired:
                    BaseCLIProvider._AGY_LOCK.release()


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


async def close_session(conversation_id: int) -> None:
    if conversation_id < 0:
        return
    session = _SESSIONS.get(conversation_id)
    if session is not None:
        await session.close()
    for key in list(_RESUME_IDS):
        if key[0] == conversation_id:
            _RESUME_IDS.pop(key, None)


async def close_all_sessions() -> None:
    for conversation_id in list(_SESSIONS):
        if conversation_id < 0:
            session = _SESSIONS.get(conversation_id)
            if session is not None:
                await session.close()
            continue
        await close_session(conversation_id)
    _RESUME_IDS.clear()
