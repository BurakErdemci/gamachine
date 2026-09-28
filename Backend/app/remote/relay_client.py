"""The PC's one outbound socket to the relay (relay/README.md, "Control frames").

`/ws/pc/<pair_id>` with subprotocols `gamachine.v1` + `key.<room_key>` and no
`Origin` header: the relay refuses a PC socket that carries one. Every frame
the relay sends goes to `handler` except the ones this class consumes
(`pong`, `tokens_ok` and the token errors, which answer `token_op`).
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import random
import time
from typing import Awaitable, Callable, Deque, Optional

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

logger = logging.getLogger(__name__)

PROTOCOL = "gamachine.v1"
PC_FRAME_MAX = 1024 * 1024

CLOSE_REPLACED = 4000
CLOSE_ROOM_RESET = 4006
CLOSE_ROOM_EXPIRED = 4007

_TOKEN_ERRORS = ("bad_hashes", "bad_hash", "too_many_tokens")


class TokenOpError(Exception):
    pass


class RelayClient:
    # Tests shrink these.
    backoff_base_s = 1.0
    backoff_cap_s = 60.0
    # A 429 on the PC socket means "too many new rooms" (Retry-After 3600).
    rate_limit_min_s = 60.0
    rate_limit_cap_s = 3600.0
    # 401/403/404: our key or pair_id is refused; retrying fast cannot help.
    refused_delay_s = 300.0
    ping_every_s = 25.0
    dead_after_s = 75.0
    open_timeout_s = 15.0

    def __init__(self, ws_origin: str, pair_id: str, room_key: str,
                 handler: Callable[[dict], Awaitable[None]],
                 on_disconnect: Optional[Callable[[], Awaitable[None]]] = None):
        self.url = f"{ws_origin}/ws/pc/{pair_id}"
        self._room_key = room_key
        self._handler = handler
        self._on_disconnect = on_disconnect
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._connected = asyncio.Event()
        self._token_waiters: Deque[asyncio.Future] = collections.deque()
        self.last_error: Optional[str] = None
        self.retry_at: Optional[float] = None
        self.connects = 0
        self.gave_up = False

    # ── lifecycle ───────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self.gave_up = False
            self._task = asyncio.get_running_loop().create_task(self._run(), name="remote-relay")

    async def stop(self) -> None:
        self._stopping = True
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        self._task = None

    async def wait_connected(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ── sending ─────────────────────────────────────────────────────────
    async def send(self, obj: dict) -> bool:
        ws = self._ws
        if ws is None or not self.connected:
            return False
        text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        if len(text.encode("utf-8")) > PC_FRAME_MAX:
            logger.error("[remote] frame over the relay's 1 MiB cap not sent (%s)", obj.get("type"))
            return False
        try:
            await ws.send(text)
            return True
        except ConnectionClosed:
            return False

    async def token_op(self, obj: dict, timeout: float = 10.0) -> dict:
        """Send `register_tokens` / `drop_token` and wait for the relay's answer.
        The relay answers these in order, so waiters are a FIFO."""
        fut = self.send_token_op(obj)
        if fut is None:
            raise TokenOpError("relay_not_connected")
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise TokenOpError("relay_timeout")

    def send_token_op(self, obj: dict) -> Optional[asyncio.Future]:
        """Queue the waiter before sending so an answer can never overtake it."""
        if self._ws is None or not self.connected:
            return None
        fut = asyncio.get_running_loop().create_future()
        self._token_waiters.append(fut)
        asyncio.get_running_loop().create_task(self._send_or_fail(obj, fut))
        return fut

    async def _send_or_fail(self, obj: dict, fut: asyncio.Future) -> None:
        if not await self.send(obj) and not fut.done():
            fut.set_exception(TokenOpError("relay_not_connected"))
            try:
                self._token_waiters.remove(fut)
            except ValueError:
                pass

    # ── connection loop ────────────────────────────────────────────────
    def _backoff(self, attempt: int) -> float:
        return min(self.backoff_cap_s, self.backoff_base_s * (2 ** attempt)) * random.uniform(0.5, 1.0)

    async def _run(self) -> None:
        attempt = 0
        while not self._stopping:
            delay = None
            try:
                async with connect(
                    self.url,
                    subprotocols=[PROTOCOL, f"key.{self._room_key}"],
                    origin=None,
                    user_agent_header="Gamachine-remote-bridge/1",
                    compression=None,
                    open_timeout=self.open_timeout_s,
                    ping_interval=None,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    self.connects += 1
                    welcomed = await self._session(ws)
                    if welcomed:
                        attempt = 0
            except InvalidStatus as exc:
                status = exc.response.status_code
                if status == 429:
                    retry = _retry_after(exc.response.headers.get("Retry-After"))
                    delay = min(self.rate_limit_cap_s, max(self.rate_limit_min_s, retry or 0))
                    self.last_error = "rate_limited"
                elif status in (401, 403, 404):
                    delay = self.refused_delay_s
                    self.last_error = f"refused_{status}"
                else:
                    self.last_error = f"http_{status}"
            except ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd is not None else None
                if code == CLOSE_REPLACED:
                    # Another Gamachine with this room key took over; two PCs
                    # would replace each other forever.
                    self.last_error = "replaced_by_another_pc"
                    self.gave_up = True
                    self._stopping = True
                elif code == CLOSE_ROOM_RESET:
                    self.last_error = "room_reset"
                elif code == CLOSE_ROOM_EXPIRED:
                    self.last_error = "room_expired"
                else:
                    self.last_error = f"closed_{code}" if code else "connection_lost"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = type(exc).__name__
                logger.info("[remote] relay connection failed: %s", exc)
            finally:
                await self._dropped()
            if self._stopping:
                break
            if delay is None:
                delay = self._backoff(attempt)
                attempt += 1
            self.retry_at = time.time() + delay
            await asyncio.sleep(delay)
            self.retry_at = None

    async def _session(self, ws) -> bool:
        """Read frames until the socket closes; True if a welcome arrived."""
        welcomed = False
        last_rx = time.monotonic()
        pinger = asyncio.get_running_loop().create_task(self._pinger(ws, lambda: last_rx))
        try:
            async for raw in ws:
                last_rx = time.monotonic()
                if not isinstance(raw, str):
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                mtype = msg.get("type")
                if mtype == "pong":
                    continue
                if mtype == "welcome":
                    welcomed = True
                    self.last_error = None
                    self._connected.set()
                if mtype == "tokens_ok" or (mtype == "error" and msg.get("error") in _TOKEN_ERRORS):
                    self._answer_token_op(msg)
                    continue
                try:
                    await self._handler(msg)
                except Exception:
                    logger.exception("[remote] relay frame %s not handled", mtype)
        finally:
            pinger.cancel()
        return welcomed

    async def _pinger(self, ws, last_rx: Callable[[], float]) -> None:
        # The relay answers '{"type":"ping"}' itself (auto-response); protocol
        # pings are left off because a hibernating Durable Object is not
        # documented to answer them.
        try:
            while True:
                await asyncio.sleep(self.ping_every_s)
                if time.monotonic() - last_rx() > self.dead_after_s:
                    logger.info("[remote] relay silent for %.0f s; reconnecting", self.dead_after_s)
                    await ws.close(code=1000, reason="silent")
                    return
                await ws.send('{"type":"ping"}')
        except (asyncio.CancelledError, ConnectionClosed):
            return

    def _answer_token_op(self, msg: dict) -> None:
        while self._token_waiters:
            fut = self._token_waiters.popleft()
            if fut.done():
                continue
            if msg.get("type") == "tokens_ok":
                fut.set_result(msg)
            else:
                fut.set_exception(TokenOpError(str(msg.get("error"))))
            return

    async def _dropped(self) -> None:
        was_connected = self.connected
        self._ws = None
        self._connected.clear()
        while self._token_waiters:
            fut = self._token_waiters.popleft()
            if not fut.done():
                fut.set_exception(TokenOpError("relay_disconnected"))
        if was_connected and self._on_disconnect is not None:
            try:
                await self._on_disconnect()
            except Exception:
                logger.exception("[remote] disconnect handler failed")


def _retry_after(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
