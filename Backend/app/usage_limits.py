"""Cached subscription limits, independent of conversation sessions."""
import asyncio
import copy
import json
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone


FAMILIES = ("claude", "codex", "agy")
# Local /usage queries need no model turn (usage spike, 2 Oct 2026).
FETCH_TIMEOUT_S = 45
_CLOSE_TIMEOUT_S = 5
_SERVICE_CLOSE_TIMEOUT_S = 10
logger = logging.getLogger(__name__)
_MONTHS = {name: i for i, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _object(value):
    return value if isinstance(value, dict) else {}


def _pct(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite usage percentage")
    return max(0, min(100, round(number)))


def _claude_reset(text, now):
    # Windows CLI: "Oct 3, 1am"; macOS CLI: "Oct 3 at 1am" (both measured, 2 Oct 2026).
    match = re.match(r"^([A-Za-z]{3})\s+(\d{1,2})(?:,\s*|\s+at\s+)(\d{1,2})(?::(\d{2}))?(am|pm)(?:\s*\([^)]*\))?\Z",
                     text, re.IGNORECASE)
    if not match:
        return None
    try:
        month, day, hour, minute, meridiem = match.groups()
        hour = int(hour)
        if not 1 <= hour <= 12:
            return None
        local_now = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
        local = datetime(local_now.year, _MONTHS[month.title()], int(day),
                         hour % 12 + (12 if meridiem.lower() == "pm" else 0), int(minute or 0))
        if local < local_now - timedelta(days=1):
            local = local.replace(year=local.year + 1)
        # CLI zone annotations describe the machine's local zone, not an IANA lookup.
        # Windows has no tz database in this venv (usage spike, 2 Oct 2026).
        return local.astimezone(timezone.utc).isoformat()
    except (ValueError, KeyError, OverflowError, OSError):
        return None


def parse_claude_usage(text, now=None):
    result = {"plan": None, "windows": []}
    if not isinstance(text, str):
        return result
    now = now or datetime.now()
    for line in text.splitlines():
        match = re.match(r"^\s*(Current session|Current week \(([^)]+)\)):\s*(\d+)% used\s*.*?resets\s+(.+?)\s*$", line)
        if not match:
            continue
        label, model, percentage, reset = match.groups()
        group = model if model and model != "all models" else None
        kind = "5h" if label == "Current session" else "week"
        slug = re.sub(r"[^a-z0-9]+", "-", group.lower()).strip("-") if group else ""
        identifier = "session" if kind == "5h" else (f"week-{slug}" if group else "week")
        result["windows"].append({
            "id": identifier, "group": group, "label": label, "kind": kind,
            "used_pct": _pct(percentage), "resets_at": _claude_reset(reset, now),
            "resets_text": reset,
        })
    return result


def parse_codex_ratelimits(result):
    limits = _object(_object(result).get("rateLimits"))
    plan = limits.get("planType")
    parsed = {"plan": plan if isinstance(plan, str) else None, "windows": []}
    for name in ("primary", "secondary"):
        window = _object(limits.get(name))
        try:
            minutes = int(window["windowDurationMins"])
            if minutes <= 0:
                continue
            used = _pct(window["usedPercent"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        kind = "5h" if minutes < 1440 else "week"
        identifier = {300: "5h", 10080: "week"}.get(minutes, f"w{minutes}")
        reset = None
        try:
            reset = datetime.fromtimestamp(float(window["resetsAt"]), timezone.utc).isoformat()
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            pass
        parsed["windows"].append({
            "id": identifier, "group": None,
            "label": "5-hour limit" if minutes == 300 else ("Weekly limit" if minutes == 10080 else f"{minutes}-minute limit"),
            "kind": kind, "used_pct": used, "resets_at": reset, "resets_text": None,
        })
    return parsed


def parse_agy_usage(obj):
    parsed = {"plan": None, "windows": []}
    groups = _object(_object(_object(obj).get("command")).get("data")).get("groups")
    if not isinstance(groups, list):
        return parsed
    for group in groups:
        group = _object(group)
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            continue
        for bucket in buckets:
            bucket = _object(bucket)
            if bucket.get("window") not in ("5h", "weekly") or not isinstance(bucket.get("id"), str):
                continue
            try:
                used = _pct((1 - float(bucket["remaining_fraction"])) * 100)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            parsed["windows"].append({
                "id": bucket["id"], "group": group.get("name") if isinstance(group.get("name"), str) else None,
                "label": bucket.get("name") if isinstance(bucket.get("name"), str) else bucket["id"],
                "kind": "5h" if bucket["window"] == "5h" else "week",
                "used_pct": used, "resets_at": bucket.get("reset_time") if isinstance(bucket.get("reset_time"), str) else None,
                "resets_text": None,
            })
    return parsed


class CLIUnavailable(RuntimeError):
    """No resolvable CLI; retrying cannot produce data in this process."""


def _claude_binary():
    from providers.claude_sdk_session import claude_ikilisini_coz
    resolved = claude_ikilisini_coz()
    if not resolved and not shutil.which("claude"):
        raise CLIUnavailable("Claude CLI is not installed")
    return resolved


def _agy_binary():
    from providers.agy_provider import AgyProvider
    binary = AgyProvider._agy_binary()
    if not shutil.which("agy") and not os.path.isfile(binary):
        raise CLIUnavailable("Antigravity CLI is not installed")
    return binary


def _neutral_cwd():
    database = os.environ.get("DB_PATH")
    return os.path.dirname(os.path.abspath(database)) if database else os.path.expanduser("~/.unity_architect_ai")


class ClaudeUsageProbe:
    """Own one lazy SDK connection, so SessionStart hooks run only at connect."""

    def __init__(self, cwd=None, client_factory=None):
        self.cwd = cwd or _neutral_cwd()
        self._factory = client_factory
        self._client = None
        self._lock = asyncio.Lock()
        self._requests = asyncio.Queue()
        self._runner = None
        self._closed = False

    def _new_client(self):
        if self._factory is not None:
            return self._factory()
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        from providers.claude_sdk_session import CLAUDE_SETTING_SOURCES
        binary = _claude_binary()
        options = dict(cwd=self.cwd, setting_sources=list(CLAUDE_SETTING_SOURCES), strict_mcp_config=True)
        if binary:
            options["cli_path"] = binary
        return ClaudeSDKClient(options=ClaudeAgentOptions(**options))

    async def _disconnect(self, client=None):
        client = self._client if client is None else client
        if self._client is client:
            self._client = None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                # Cleanup must not replace cancellation (usage audit, 2 Oct 2026).
                logger.warning("Claude usage disconnect failed", exc_info=True)

    async def _query(self):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
        client = self._client
        try:
            async with asyncio.timeout(FETCH_TIMEOUT_S):
                if client is None:
                    client = self._client = self._new_client()
                    await client.connect()
                await client.query("/usage")
                texts = []
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        texts.extend(block.text for block in message.content if isinstance(block, TextBlock))
                    elif isinstance(message, ResultMessage):
                        if message.is_error:
                            raise RuntimeError("Claude usage query failed")
                        return parse_claude_usage("\n".join(texts))
                raise RuntimeError("Claude usage stream ended before a result")
        except BaseException:
            failed_client, client = client, None
            await self._disconnect(failed_client)
            raise
        finally:
            # A retired runner must never touch its replacement's connection.
            # Keep cleanup in the SDK owner task (usage audit, 2 Oct 2026).
            if asyncio.current_task() is not self._runner and client is not None:
                await self._disconnect(client)

    async def _serve(self, requests):
        # SDK/AnyIO connection scopes must be entered and exited in one task.
        # HTTP refresh tasks are short-lived; this owner lasts until shutdown.
        try:
            while asyncio.current_task() is self._runner:
                response = await requests.get()
                if response is None:
                    break
                try:
                    parsed = await self._query()
                except asyncio.CancelledError:
                    if not response.done():
                        response.cancel()
                    raise
                except Exception as error:
                    if not response.done():
                        response.set_exception(error)
                else:
                    if not response.done():
                        response.set_result(parsed)
        finally:
            if asyncio.current_task() is self._runner:
                await self._disconnect()

    async def fetch(self):
        async with self._lock:
            if self._closed:
                raise RuntimeError("Claude usage probe is closed")
            if self._runner is None or self._runner.done():
                self._runner = asyncio.create_task(self._serve(self._requests))
            response = asyncio.get_running_loop().create_future()
            self._requests.put_nowait(response)
            try:
                return await response
            except asyncio.CancelledError:
                runner = self._runner
                runner.cancel()
                try:
                    done, _ = await asyncio.wait((runner,), timeout=_CLOSE_TIMEOUT_S)
                    if not done:
                        logger.warning("Claude usage runner abandoned after cancellation")
                finally:
                    runner.add_done_callback(_consume_task_result)
                    self._runner = None
                    self._requests = asyncio.Queue()
                    self._client = None
                raise

    async def aclose(self):
        self._closed = True

        async def close():
            async with self._lock:
                if self._runner is not None:
                    self._requests.put_nowait(None)
                    await asyncio.shield(self._runner)

        try:
            await asyncio.wait_for(close(), _CLOSE_TIMEOUT_S)
        except (Exception, asyncio.CancelledError):
            logger.warning("Claude usage close failed or timed out", exc_info=True)
        finally:
            if self._runner is not None:
                self._runner.cancel()
                self._runner.add_done_callback(_consume_task_result)


def _consume_task_result(task):
    # Abandoned tasks can finish later (usage audit, 2 Oct 2026).
    if not task.cancelled():
        task.exception()


# Spelled out as plain keyword arguments at each spawn, not a **dict: the spawn-env gate
# (tests/test_spawn_env_gate.py) cannot prove a splatted dict leaves `env` alone.
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_NEW_SESSION = os.name != "nt"


async def _kill_probe(proc):
    """Kill the owned tree, including native CLI children of npm launchers."""
    if proc is None:
        return
    if proc.returncode is None:
        if os.name == "nt":
            # One bounded cleanup helper per owned probe; never match by name.
            try:
                await asyncio.to_thread(subprocess.run,
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=5, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    # Release pipes even after a timeout (usage audit, 2 Oct 2026).
    if proc.stdin is not None:
        proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), _CLOSE_TIMEOUT_S)
    except TimeoutError:
        logger.warning("Usage probe process did not exit after kill")


async def fetch_codex_usage():
    if not shutil.which("codex"):
        raise CLIUnavailable("Codex CLI is not installed")
    from providers.cli_base import build_spawn_env
    from providers.codex_session import _APP_SERVER_STREAM_LIMIT, _resolve_codex_appserver_cmd
    proc = None
    try:
        async with asyncio.timeout(FETCH_TIMEOUT_S):
            proc = await asyncio.create_subprocess_exec(
                *_resolve_codex_appserver_cmd(), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=build_spawn_env(family="codex", overrides={"NO_COLOR": "1"}),
                cwd=_neutral_cwd(), limit=_APP_SERVER_STREAM_LIMIT,
                creationflags=_CREATIONFLAGS, start_new_session=_NEW_SESSION)

            async def send(message):
                proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                await proc.stdin.drain()

            async def receive(identifier):
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        raise RuntimeError("Codex usage process exited before a response")
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(message, dict) or message.get("id") != identifier:
                        continue
                    if "error" in message:
                        raise RuntimeError(f"Codex usage request failed: {message['error']}")
                    if "result" in message:
                        return message["result"]

            await send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "gamachine", "version": "0.1.0"}}})
            await receive(1)
            await send({"method": "initialized"})
            await send({"id": 2, "method": "account/rateLimits/read", "params": {}})
            return parse_codex_ratelimits(await receive(2))
    finally:
        await _kill_probe(proc)


async def fetch_agy_usage():
    from providers.cli_base import BaseCLIProvider, build_spawn_env
    binary = _agy_binary()
    proc = None
    try:
        async with asyncio.timeout(FETCH_TIMEOUT_S):
            proc = await asyncio.create_subprocess_exec(
                *BaseCLIProvider._resolve_exec([binary, "-p", "/usage", "--output-format", "json"]),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=build_spawn_env(family="agy"),
                cwd=_neutral_cwd(),
                creationflags=_CREATIONFLAGS, start_new_session=_NEW_SESSION)
            stdout, stderr = await proc.communicate()
            if proc.returncode:
                raise RuntimeError(f"Antigravity usage failed ({proc.returncode}): {stderr.decode('utf-8', errors='replace')[:500]}")
            return parse_agy_usage(json.loads(stdout))
    finally:
        await _kill_probe(proc)


class UsageService:
    def __init__(self, fetchers: dict | None = None, clock=time.monotonic, ttl_s=120):
        self._clock = clock
        self._ttl = ttl_s
        self._closed = False
        self._tasks = {}
        self._measured = {}
        self._attempted = {}
        self._retry_delay = {}
        self._probe = ClaudeUsageProbe() if fetchers is None else None
        self._fetchers = ({"claude": self._probe.fetch, "codex": fetch_codex_usage, "agy": fetch_agy_usage}
                          if fetchers is None else dict(fetchers))
        self._cache = {family: {
            "family": family, "status": "loading" if self._fetchers.get(family) else "unavailable",
            "plan": None, "measured_at": None, "stale": False, "error": None, "windows": [],
        } for family in FAMILIES}

    def _start_refresh(self, family):
        if self._closed or not self._fetchers.get(family):
            return None
        task = self._tasks.get(family)
        if task is None or task.done():
            task = asyncio.create_task(self._perform_refresh(family))
            self._tasks[family] = task
        return task

    def snapshot(self, force=False, start=True):
        now = self._clock()
        for family in FAMILIES:
            attempted = self._attempted.get(family)
            delay = self._retry_delay.get(family, self._ttl)
            if start and (force or attempted is None or now - attempted >= delay):
                self._start_refresh(family)
        entries = copy.deepcopy([self._cache[family] for family in FAMILIES])
        for entry in entries:
            family = entry["family"]
            measured = self._measured.get(family)
            entry["stale"] = entry["status"] == "error" or (measured is not None and (
                now - measured >= self._ttl or family in self._tasks))
        return {"families": entries, "now": _utc_now()}

    async def _perform_refresh(self, family):
        entry = self._cache[family]
        try:
            # Bound injected fetchers as well as the three production probes.
            async with asyncio.timeout(FETCH_TIMEOUT_S):
                parsed = await self._fetchers[family]()
            entry.update(status="ok", plan=parsed.get("plan"), windows=copy.deepcopy(parsed["windows"]),
                         measured_at=_utc_now(), error=None)
            self._measured[family] = self._clock()
            self._retry_delay.pop(family, None)
        except CLIUnavailable as error:
            entry.update(status="unavailable", error=str(error))
            self._retry_delay[family] = self._ttl
        except asyncio.CancelledError:
            if entry["measured_at"] is None:
                entry.update(status="error", error="Usage service closed")
            raise
        except Exception as error:
            delay = (self._retry_delay.get(family, 15) * 2
                     if entry["status"] == "error" else 30)
            entry.update(status="error", error=str(error) or type(error).__name__)
            self._retry_delay[family] = min(self._ttl, delay)
        finally:
            self._attempted[family] = self._clock()
            self._tasks.pop(family, None)

    async def refresh(self, family):
        if family not in FAMILIES:
            raise ValueError(f"Unknown usage family: {family}")
        task = self._start_refresh(family)
        if task is not None:
            await asyncio.shield(task)

    async def wait_for_refreshes(self, timeout_s=50):
        pending = tuple(self._tasks.values())
        if pending:
            # Waiting for HTTP responses never cancels service-owned probes.
            await asyncio.wait(pending, timeout=timeout_s)

    async def aclose(self):
        self._closed = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()

        async def close_owner(owner):
            try:
                await asyncio.wait_for(owner.aclose(), _CLOSE_TIMEOUT_S)
            except (Exception, asyncio.CancelledError):
                logger.warning("Usage owner close failed or timed out", exc_info=True)

        pending = set(tasks)
        try:
            owners = {getattr(fetcher, "__self__", None) for fetcher in self._fetchers.values()}
            for owner in owners:
                if owner is not None and hasattr(owner, "aclose"):
                    pending.add(asyncio.create_task(close_owner(owner)))
            # wait_for alone can hang on cancellation-resistant cleanup.
            # The outer wait bounds all owners together (usage audit, 2 Oct 2026).
            if pending:
                _, pending = await asyncio.wait(pending, timeout=_SERVICE_CLOSE_TIMEOUT_S)
        except (Exception, asyncio.CancelledError):
            logger.warning("Usage service close failed", exc_info=True)
        finally:
            for task in pending:
                task.cancel()
            for task in tasks:
                task.add_done_callback(_consume_task_result)
            for task in pending:
                task.add_done_callback(_consume_task_result)
            self._tasks.clear()
            for entry in self._cache.values():
                if entry["status"] == "loading":
                    entry.update(status="error", error="Usage service closed")
