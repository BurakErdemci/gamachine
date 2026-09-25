"""Persistent Antigravity stream-json sessions, one subprocess per conversation."""
import asyncio
import json
import logging
import os
from collections.abc import Hashable, Mapping
from typing import AsyncGenerator, Dict, Optional

from .agy_provider import AgyProvider
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
    """The one global approval mode, read at spawn time.

    Not `self.auto_approve` from the request: agy fixes its hooks when it
    starts, so the value that counts is the backend's at that moment.
    Unreadable counts as step (fail closed).
    """
    try:
        from agentic import approval_mode
        return approval_mode.is_auto()
    except Exception:
        logger.warning("[agy] approval mode unreadable; spawning in step mode", exc_info=True)
        return False


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
        # never registered in _SESSIONS or the _RESUME_IDS store.
        self.conversation_id = conversation_id
        self.cwd = os.path.abspath(cwd)
        self.session_id = resume_id if conversation_id >= 0 else None
        self.model = None
        self._spawned_auto: Optional[bool] = None
        self._active_process = None
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
        from the global mode, so a running step hook starts allowing at once
        after a flip to auto. A flip to step still needs the respawn in
        _start: a process spawned in auto has no hook to tighten.
        """
        self._auto_approve = bool(value)
        if self.is_live and self._spawned_auto is False:
            try:
                from .agy_provider import write_gate_state
                write_gate_state(auto=_global_auto_mode())
            except Exception:
                logger.warning("[agy] step gate state not refreshed", exc_info=True)

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
                logger.info("[agy] child stopped pid=%s exit_status=%s",
                            pid, getattr(process, "returncode", None))
            else:
                logger.warning("[agy] child could not be reaped pid=%s", pid)

    async def cancel_active_process(self) -> bool:
        was_live = self.is_live
        await self._stop_process(force=True)
        return was_live

    async def close(self, *, preserve_resume: bool = False) -> None:
        if self.conversation_id >= 0 and _SESSIONS.get(self.conversation_id) is self:
            _SESSIONS.pop(self.conversation_id, None)
        if not preserve_resume and self.conversation_id >= 0:
            _RESUME_IDS.pop((self.conversation_id, self.cwd), None)
        await oturumu_kapat(self)
        await self._stop_process(force=True)

    async def _start(self, model: str, cwd: str) -> str:
        if self._kapandi:
            raise RuntimeError("agy session was stopped.")
        if not os.path.isdir(cwd):
            raise AgyWorkspaceError("agy workspace directory does not exist.")
        # The process holds ~/.gemini/settings.json state and the workspace
        # hooks for its life. Model and approval-mode changes require closing
        # and respawning, while retaining the UUID.
        auto = _global_auto_mode()
        self.auto_approve = auto
        if self._active_process is not None and (
            not self.is_live or self.model != model or self.cwd != cwd
            or self._spawned_auto != auto
        ):
            await self._stop_process()
        if self._kapandi:
            raise RuntimeError("agy session was stopped.")
        if self.is_live:
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
        if not provider._write_step_gate(cwd, step_mode=not auto):
            logger.error("[agy] step gate not in the wanted state (auto=%s) cwd=%s", auto, cwd)
        self._spawned_auto = auto
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
        # Register before draining stdin, so Stop can reach a blocked writer.
        self._active_process = process
        self.active_provider = self
        self._stderr_task = asyncio.create_task(self._drain_stderr(process))
        self.model = model
        logger.info("[agy] child spawned pid=%s cwd=%s model=%s",
                    getattr(process, "pid", None), cwd, model)
        if self._kapandi:
            await self._stop_process(force=True)
            raise RuntimeError("agy session was stopped during startup.")
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
    if session is None:
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
        await close_session(conversation_id)
    _RESUME_IDS.clear()
