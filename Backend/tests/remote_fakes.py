"""Loopback fakes for the remote bridge tests: a relay that behaves like
relay/worker/room.js for everything the bridge uses, a phone that speaks the
doc's byte layouts, and a push service. Nothing here leaves 127.0.0.1."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.datastructures import Headers

from remote import crypto as C

PROTOCOL = "gamachine.v1"
PHONE_FRAME_MAX = 64 * 1024
PC_FRAME_MAX = 1024 * 1024


class Room:
    def __init__(self):
        self.tokens: List[str] = []
        self.pc = None
        self.conns: Dict[str, dict] = {}
        self.last_seen = None


class FakeRelay:
    """One process-local relay. Records what the PC sent so tests can check it."""

    def __init__(self):
        self.rooms: Dict[str, Room] = {}
        self.pc_requests: List[Headers] = []
        self.pc_frames: List[str] = []
        self.pc_connects = 0
        self.refuse_pc: List[tuple] = []  # (status, retry_after) popped per attempt
        self.connect_times: List[float] = []
        self.errors_sent: List[str] = []
        self._seq = 0
        self.server = None
        self.port = None
        self.next_ip = "198.51.100.7"

    async def start(self):
        self.server = await serve(self._handler, "127.0.0.1", 0, subprotocols=[PROTOCOL],
                                  process_request=self._process_request, max_size=4 * 1024 * 1024)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def _conn_id(self) -> str:
        self._seq += 1
        return f"c{self._seq:04d}"

    @staticmethod
    def _parse(path: str):
        parts = urlsplit(path).path.split("/")
        if len(parts) != 4 or parts[1] != "ws" or parts[2] not in ("pc", "phone", "pair"):
            return None, None
        return parts[2], parts[3]

    def _process_request(self, connection, request):
        role, pair_id = self._parse(request.path)
        if role is None:
            return connection.respond(404, "not found")
        protocols = [p.strip() for p in (request.headers.get("Sec-WebSocket-Protocol") or "").split(",") if p.strip()]
        if PROTOCOL not in protocols:
            return connection.respond(400, "missing subprotocol")
        origin = request.headers.get("Origin")
        if role == "pc":
            self.pc_requests.append(request.headers)
            self.connect_times.append(time.monotonic())
            if origin is not None:
                return connection.respond(403, "origin not allowed")
            if self.refuse_pc:
                status, retry = self.refuse_pc.pop(0)
                resp = connection.respond(status, "refused")
                if retry is not None:
                    resp.headers["Retry-After"] = str(retry)
                return resp
            key = next((p[4:] for p in protocols if p.startswith("key.")), None)
            if not key or C.pair_id_for(key) != pair_id:
                return connection.respond(403, "forbidden")
            return None
        if origin != self.url:
            return connection.respond(403, "origin not allowed")
        if role == "phone":
            room = self.rooms.get(pair_id)
            if room is None:
                return connection.respond(404, "not found")
            token = next((p[4:] for p in protocols if p.startswith("tok.")), None)
            if not token or C.token_hash(token) not in room.tokens:
                return connection.respond(401, "unauthorized")
        return None

    async def _handler(self, ws):
        role, pair_id = self._parse(ws.request.path)
        if role == "pc":
            await self._pc(ws, pair_id)
        elif role == "phone":
            await self._phone(ws, pair_id)
        else:
            await self._pair(ws, pair_id)

    # ── PC ───────────────────────────────────────────────────────────────
    async def _pc(self, ws, pair_id):
        room = self.rooms.setdefault(pair_id, Room())
        self.pc_connects += 1
        old = room.pc
        room.pc = ws
        if old is not None:
            await old.close(4000, "replaced")
        phones = [{"conn": c, "token_hash": a["token_hash"]} for c, a in room.conns.items() if a["role"] == "phone"]
        pairs = [{"conn": c, "ip": a["ip"]} for c, a in room.conns.items() if a["role"] == "pair"]
        await ws.send(json.dumps({"type": "welcome", "phones": phones, "pairs": pairs, "tokens": len(room.tokens)}))
        for a in list(room.conns.values()):
            if a["role"] == "phone":
                await _quiet_send(a["ws"], '{"type":"pc_online"}')
        try:
            async for message in ws:
                if message == '{"type":"ping"}':
                    await ws.send('{"type":"pong"}')
                    continue
                self.pc_frames.append(message)
                if len(message.encode("utf-8")) > PC_FRAME_MAX:
                    self.errors_sent.append("too_large")
                    await ws.send(json.dumps({"type": "error", "error": "too_large"}))
                    continue
                await self._pc_message(ws, room, pair_id, json.loads(message))
        except Exception:
            pass
        finally:
            if self.rooms.get(pair_id) is room and room.pc is ws:
                room.pc = None
                room.last_seen = int(time.time() * 1000)
                offline = json.dumps({"type": "pc_offline", "last_seen": room.last_seen})
                for c, a in list(room.conns.items()):
                    await _quiet_send(a["ws"], offline)
                    if a["role"] == "pair":
                        await a["ws"].close(4005, "pc_offline")

    async def _pc_message(self, pc, room, pair_id, m):
        t = m.get("type")
        if t == "to":
            target = room.conns.get(m.get("conn"))
            if target is None or not isinstance(m.get("data"), str):
                await pc.send(json.dumps({"type": "gone", "conn": m.get("conn")}))
                return
            await _quiet_send(target["ws"], m["data"])
            if target["role"] == "pair":
                await target["ws"].close(4002, "pair_done")
        elif t == "register_tokens":
            hashes = m.get("hashes")
            if not isinstance(hashes, list) or not all(isinstance(h, str) and len(h) == 43 for h in hashes):
                await pc.send(json.dumps({"type": "error", "error": "bad_hashes"}))
                return
            nxt = list(dict.fromkeys(hashes)) if m.get("replace") else list(dict.fromkeys(room.tokens + hashes))
            if len(nxt) > 50:
                await pc.send(json.dumps({"type": "error", "error": "too_many_tokens"}))
                return
            room.tokens = nxt
            if m.get("replace"):
                for a in list(room.conns.values()):
                    if a["role"] == "phone" and a["token_hash"] not in nxt:
                        await a["ws"].close(4001, "token_dropped")
            await pc.send(json.dumps({"type": "tokens_ok", "count": len(nxt)}))
        elif t == "drop_token":
            h = m.get("hash")
            room.tokens = [x for x in room.tokens if x != h]
            for a in list(room.conns.values()):
                if a["role"] == "phone" and a["token_hash"] == h:
                    await a["ws"].close(4001, "token_dropped")
            await pc.send(json.dumps({"type": "tokens_ok", "count": len(room.tokens)}))
        elif t == "reset_room":
            await self.wipe(pair_id, 4006)
        else:
            await pc.send(json.dumps({"type": "error", "error": "unknown_type"}))

    async def wipe(self, pair_id: str, code: int = 4007):
        """Delete the room and close every socket (reset_room / 30-day expiry)."""
        room = self.rooms.pop(pair_id, None)
        if room is None:
            return
        for a in list(room.conns.values()):
            await a["ws"].close(code, "wiped")
        if room.pc is not None:
            await room.pc.close(code, "wiped")

    async def kick_pc(self, pair_id: str):
        room = self.rooms.get(pair_id)
        if room is not None and room.pc is not None:
            await room.pc.close(1011, "dropped by test")

    # ── phones ──────────────────────────────────────────────────────────
    async def _phone(self, ws, pair_id):
        room = self.rooms[pair_id]
        token = next(p[4:] for p in ws.request.headers["Sec-WebSocket-Protocol"].split(", ") if p.startswith("tok."))
        h = C.token_hash(token)
        conn = self._conn_id()
        room.conns[conn] = {"ws": ws, "role": "phone", "token_hash": h}
        if room.pc is not None:
            await room.pc.send(json.dumps({"type": "phone_open", "conn": conn, "token_hash": h}))
        else:
            await ws.send(json.dumps({"type": "pc_offline", "last_seen": room.last_seen}))
        await self._client_loop(ws, room, conn, "phone")

    async def _pair(self, ws, pair_id):
        room = self.rooms.get(pair_id)
        if room is None:
            await ws.send('{"type":"no_room"}')
            await ws.close(4008, "no_room")
            return
        conn = self._conn_id()
        ip = ws.request.headers.get("X-Test-IP") or self.next_ip
        room.conns[conn] = {"ws": ws, "role": "pair", "ip": ip, "requested": False}
        if room.pc is None:
            await ws.send(json.dumps({"type": "pc_offline", "last_seen": room.last_seen}))
            await ws.close(4005, "pc_offline")
            room.conns.pop(conn, None)
            return
        await room.pc.send(json.dumps({"type": "pair_open", "conn": conn, "ip": ip}))
        await self._client_loop(ws, room, conn, "pair")

    async def _client_loop(self, ws, room, conn, role):
        try:
            async for message in ws:
                if message == '{"type":"ping"}':
                    await ws.send('{"type":"pong"}')
                    continue
                if not isinstance(message, str) or len(message.encode("utf-8")) > PHONE_FRAME_MAX:
                    await ws.close(4004, "bad frame")
                    return
                a = room.conns.get(conn)
                if role == "pair":
                    if a["requested"]:
                        await ws.close(4004, "one request per pairing socket")
                        return
                    a["requested"] = True
                if room.pc is None:
                    await ws.send(json.dumps({"type": "pc_offline", "last_seen": room.last_seen}))
                    continue
                await room.pc.send(json.dumps({"type": "from", "conn": conn, "data": message}))
        except Exception:
            pass
        finally:
            room.conns.pop(conn, None)
            if room.pc is not None:
                await _quiet_send(room.pc, json.dumps({"type": f"{role}_close", "conn": conn}))


async def _quiet_send(ws, text):
    try:
        await ws.send(text)
    except Exception:
        pass


# ── phone ──────────────────────────────────────────────────────────────────

class PhoneError(Exception):
    pass


class FakePhone:
    """The phone page's protocol in Python (relay/public/net.js + crypto.js)."""

    def __init__(self, relay: FakeRelay, name: str = "iPhone"):
        self.relay = relay
        self.name = name
        self.key = C.KeyPair.generate()
        self.pair_id = None
        self.pc_pub = None
        self.device_id = None
        self.token = None
        self.vapid_pub = None
        self.sas = None
        self.k_static = None
        self.ws = None
        self.channel: Optional[C.Channel] = None
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.pushes: asyncio.Queue = asyncio.Queue()
        self._pending: Dict[Any, dict] = {}
        self._reader = None
        self.seq = 0
        self.sent_frames: List[dict] = []
        self.close_code = None

    @staticmethod
    def parse_qr(url: str):
        frag = url.split("#", 1)[1]
        pair_id, pc_pub, secret = frag.split(".")
        return pair_id, C.from_b64u(pc_pub), C.from_b64u(secret)

    async def pair(self, qr_url: str, ip: Optional[str] = None, mac_ok: bool = True,
                   timeout: float = 10.0) -> dict:
        """Run one pairing socket; returns {"ok": payload} or {"reject": reason} or {"error": ...}.
        A PC that never answers is {"error": "timeout"}, never a hang."""
        try:
            return await asyncio.wait_for(self._pair(qr_url, ip, mac_ok), timeout)
        except asyncio.TimeoutError:
            return {"error": "timeout"}

    async def _pair(self, qr_url: str, ip: Optional[str], mac_ok: bool) -> dict:
        self.pair_id, self.pc_pub, secret = self.parse_qr(qr_url)
        self.k_static = self.key.ecdh(self.pc_pub)
        _, self.sas = C.sas(self.k_static, secret)
        k_pair = C.pair_key(self.k_static, secret)
        mac = C.pair_mac(secret, self.key.public_raw, self.name)
        if not mac_ok:
            mac = bytes(32)
        request = {"type": "pair_request", "phone_pub": C.b64u(self.key.public_raw),
                   "device_name": self.name, "mac": C.b64u(mac)}
        headers = {"X-Test-IP": ip} if ip else None
        async with connect(f"{self.relay.ws_url}/ws/pair/{self.pair_id}", subprotocols=[PROTOCOL],
                           origin=self.relay.url, additional_headers=headers, ping_interval=None) as ws:
            await ws.send(json.dumps(request))
            try:
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("type") == "pair_reject":
                        return {"reject": m.get("reason")}
                    if m.get("type") in ("no_room", "pc_offline"):
                        return {"error": m["type"]}
                    if m.get("type") == "pair_ok":
                        payload = json.loads(C.open_frame(k_pair, C.PC_TO_PHONE, m))
                        self.device_id = payload["device_id"]
                        self.token = payload["token"]
                        self.vapid_pub = payload.get("vapid_pub")
                        return {"ok": payload, "raw": m}
            except Exception as exc:
                return {"error": repr(exc)}
        return {"error": "closed"}

    async def connect(self):
        self.ws = await connect(f"{self.relay.ws_url}/ws/phone/{self.pair_id}",
                                subprotocols=[PROTOCOL, "tok." + self.token], origin=self.relay.url,
                                ping_interval=None, max_size=4 * 1024 * 1024)
        self._reader = asyncio.get_running_loop().create_task(self._read())

    async def _read(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                if "c" in m and "d" in m and "type" not in m:
                    obj = self.channel.open(m) if self.channel else None
                    if obj is None:
                        continue
                    if "id" in obj and obj["id"] in self._pending:
                        self._pending[obj["id"]]["parts"].append(obj)
                        self._pending[obj["id"]]["event"].set()
                    else:
                        await self.pushes.put(obj)
                else:
                    await self.inbox.put(m)
        except Exception:
            pass
        finally:
            self.close_code = self.ws.close_code
            await self.inbox.put({"type": "_closed", "code": self.ws.close_code})

    async def next_control(self, timeout=5.0) -> dict:
        while True:
            m = await asyncio.wait_for(self.inbox.get(), timeout)
            if m.get("type") != "pong":
                return m

    async def hello(self, t: Optional[int] = None, device_id: Optional[str] = None, timeout=5.0) -> dict:
        eph = C.KeyPair.generate()
        device_id = device_id or self.device_id
        t = int(time.time()) if t is None else t
        tag = C.hmac_sha256(self.k_static, C.hello_tag_input(device_id, eph.public_raw, t))
        self.channel = None
        await self.ws.send(json.dumps({"type": "hello", "device_id": device_id,
                                       "eph_phone_pub": C.b64u(eph.public_raw), "t": t, "tag": C.b64u(tag)}))
        while True:
            m = await self.next_control(timeout)
            if m.get("type") == "hello_reject" or m.get("type") == "_closed":
                return m
            if m.get("type") == "hello_ack":
                pc = C.from_b64u(m["eph_pc_pub"])
                expected = C.hmac_sha256(self.k_static, C.hello_ack_tag_input(eph.public_raw, pc))
                if not C.equal(expected, C.from_b64u(m["tag"])):
                    raise PhoneError("bad hello_ack tag")
                p2c, c2p = C.session_keys(eph.ecdh(pc), self.k_static)
                self.channel = C.Channel.for_phone(p2c, c2p)
                return m

    async def send_frame(self, frame: dict):
        await self.ws.send(json.dumps(frame))

    def seal(self, obj: dict) -> dict:
        frame = self.channel.seal(obj)
        self.sent_frames.append(frame)
        return frame

    async def request(self, rtype: str, timeout=5.0, **params) -> dict:
        """Reply of one request; multi-part replies are joined list by list."""
        self.seq += 1
        rid = self.seq
        slot = {"parts": [], "event": asyncio.Event()}
        self._pending[rid] = slot
        await self.send_frame(self.seal({**params, "id": rid, "type": rtype}))
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError(rtype)
                await asyncio.wait_for(slot["event"].wait(), remaining)
                slot["event"].clear()
                parts = slot["parts"]
                total = parts[0].get("parts", 1)
                if len(parts) >= total:
                    break
        finally:
            self._pending.pop(rid, None)
        if len(parts) == 1:
            return parts[0]
        joined = dict(parts[0])
        result = dict(parts[0]["result"])
        for part in parts[1:]:
            for k, v in part["result"].items():
                if isinstance(v, list):
                    result[k] = result[k] + v
        joined["result"] = result
        joined["_parts"] = len(parts)
        return joined

    async def next_push(self, match=lambda m: True, timeout=5.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            m = await asyncio.wait_for(self.pushes.get(), max(0.01, deadline - time.monotonic()))
            if match(m):
                return m

    async def close(self):
        if self._reader is not None:
            self._reader.cancel()
        if self.ws is not None:
            await self.ws.close()


# ── push service ───────────────────────────────────────────────────────────

class FakePushService:
    """HTTP on 127.0.0.1; records every POST and answers `status`."""

    def __init__(self):
        self.requests: List[dict] = []
        self.status = 201
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body,
                                       "at": time.monotonic()})
                self.send_response(outer.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def endpoint(self, name: str = "sub1") -> str:
        return f"http://127.0.0.1:{self.port}/push/{name}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
