"""web_fetch: read a public web page as text for the API agent.

Read-only and card-free like read_file, so the address rules are strict: the agent
browser's URL rules (`browser_use.policy`) apply to the URL and to every redirect
hop, the host is resolved here and every address it resolves to must be public, and
the connection is pinned to the address that was checked (the request goes to the IP
with the original Host header and TLS name), so a second DNS answer cannot move it.
No cookies, credentials, netrc or proxy settings are ever used.
"""
import ipaddress
import socket
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from browser_use import policy

TIMEOUT_S = 20.0
# The whole fetch (every redirect hop and the body). The per-read timeout alone
# never fires on a server that sends one byte at a time (measured: held 6 s by a
# 12-byte drip, and open-ended with a longer one).
TOTAL_TIMEOUT_S = 30.0
MAX_REDIRECTS = 5
MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_CHARS = 20_000
MAX_CHARS_LIMIT = 200_000
_USER_AGENT = "Mozilla/5.0 (compatible; GamachineAgent/1.0)"
_TEXT_TYPES = ("application/json", "application/xml", "application/xhtml+xml", "application/javascript",
               "application/x-javascript", "application/rss+xml", "application/atom+xml")


class FetchError(Exception):
    """A refusal or failure the tool shows the model as is."""


def _resolve(host: str, port: int) -> list[str]:
    """Every address the name resolves to. Replaced by tests."""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except OSError as e:
        raise FetchError(f"Could not resolve {host}: {e}")


def _transport() -> httpx.BaseTransport | None:
    """None = the real network. Replaced by tests."""
    return None


def _public_address(host: str, port: int) -> str:
    """The address to connect to; FetchError when the host is, or resolves to, a non-public address."""
    if host == "localhost" or host.endswith(".localhost"):
        raise FetchError("Local addresses cannot be fetched.")
    try:
        candidates = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            # Legacy spellings (2130706433, 0x7f.1) are an address the resolver would read as one too.
            candidates = [str(ipaddress.ip_address(socket.inet_aton(host)))]
        except OSError:
            candidates = _resolve(host, port)
    if not candidates:
        raise FetchError(f"{host} has no address.")
    for text in candidates:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
        if policy._unmapped(ip).is_loopback or policy._embeds_ipv4(ip) or policy._blocked_address(ip):
            raise FetchError("Private or local network addresses cannot be fetched.")
    return candidates[0]


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    _BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "nav", "aside", "ul", "ol",
              "li", "tr", "table", "pre", "blockquote", "br", "hr", "form", "dd", "dt", "dl"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif len(tag) == 2 and tag[0] == "h" and tag[1] in "123456":
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag != "li" and (tag in self._BLOCK or (len(tag) == 2 and tag[0] == "h" and tag[1] in "123456")):
            self.parts.append("\n")
        elif tag == "a" and self._href is not None:
            href, self._href = self._href, None
            text = "".join(self._link_text).strip()
            if href and text and href.startswith(("http://", "https://")) and href != text:
                self.parts.append(f" ({href})")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip:
            return
        data = data.replace("\n", " ")
        self.parts.append(data)
        if self._href is not None:
            self._link_text.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    text = "\n".join(out).strip()
    title = " ".join(parser.title.split())
    return f"Title: {title}\n\n{text}" if title else text


def _remaining(deadline: float | None) -> float:
    """Seconds left before `deadline` (a time.monotonic() value); FetchError once it has passed."""
    if deadline is None:
        return TIMEOUT_S
    left = deadline - time.monotonic()
    if left <= 0:
        raise FetchError(f"The page took longer than {TOTAL_TIMEOUT_S:g} seconds to load.")
    return left


def _fetch_once(url: str, deadline: float | None = None) -> tuple[httpx.Response, bytes]:
    """One GET to a checked, pinned address; redirects are not followed. Returns the response and its body."""
    refusal = policy.navigation_refusal(url)
    if refusal:
        raise FetchError(refusal)
    parts = urlsplit(url)
    host = (parts.hostname or "").rstrip(".").casefold()
    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    address = _public_address(host, port)
    shown_host = f"[{address}]" if ":" in address else address
    target = parts._replace(netloc=f"{shown_host}:{port}").geturl()
    host_header = host if parts.port is None else f"{host}:{parts.port}"
    # A fresh client per hop: no cookie jar carries a Set-Cookie to the next request.
    with httpx.Client(transport=_transport(), timeout=min(TIMEOUT_S, _remaining(deadline)), follow_redirects=False, trust_env=False,
                      headers={"User-Agent": _USER_AGENT, "Accept": "text/html,text/plain,*/*;q=0.5",
                               "Host": host_header}) as client:
        request = client.build_request("GET", target, extensions={"sni_hostname": host})
        response = client.send(request, stream=True)
        try:
            if 300 <= response.status_code < 400:
                return response, b""
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                _remaining(deadline)
                if len(body) >= MAX_BODY_BYTES:
                    del body[MAX_BODY_BYTES:]
                    break
            return response, bytes(body)
        finally:
            response.close()


def _readable_type(content_type: str, body: bytes) -> bool:
    kind = content_type.split(";")[0].strip().lower()
    if not kind:
        return b"\x00" not in body[:1024]
    return kind.startswith("text/") or kind in _TEXT_TYPES or kind.endswith(("+json", "+xml"))


def web_fetch(url: str, max_chars=DEFAULT_MAX_CHARS) -> dict:
    """Fetches a public http(s) page and returns it as text (HTML is converted, other text kept as is).

    The answer is back within TOTAL_TIMEOUT_S: the work runs in a helper thread
    that is left behind when the deadline passes (it ends at its next read, which
    checks the same deadline)."""
    deadline = time.monotonic() + TOTAL_TIMEOUT_S
    box: list = []

    def work():
        try:
            box.append(_web_fetch(url, max_chars, deadline))
        except Exception as e:  # noqa: BLE001 - the model gets a refusal, never a stack trace
            box.append({"success": False, "error": f"Fetch failed: {type(e).__name__}: {e}"})

    worker = threading.Thread(target=work, name="web-fetch", daemon=True)
    worker.start()
    worker.join(TOTAL_TIMEOUT_S)
    if box:
        return box[0]
    return {"success": False, "error": f"The page took longer than {TOTAL_TIMEOUT_S:g} seconds to load."}


def _web_fetch(url: str, max_chars, deadline: float) -> dict:
    try:
        limit = max(1, min(int(max_chars), MAX_CHARS_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_CHARS
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            response, body = _fetch_once(current, deadline)
            if 300 <= response.status_code < 400:
                location = response.headers.get("location")
                if not location:
                    raise FetchError(f"HTTP {response.status_code} without a Location.")
                current = urljoin(current, location)
                continue
            break
        else:
            raise FetchError(f"More than {MAX_REDIRECTS} redirects.")
        if response.status_code >= 400:
            raise FetchError(f"HTTP {response.status_code} from {current}")
        content_type = response.headers.get("content-type", "")
        if not _readable_type(content_type, body):
            raise FetchError(f"Not a text page ({content_type or 'unknown type'}).")
        text = body.decode(response.charset_encoding or "utf-8", errors="replace")
        if content_type.split(";")[0].strip().lower() in ("text/html", "application/xhtml+xml") or \
                text.lstrip()[:15].lower().startswith(("<!doctype html", "<html")):
            text = html_to_text(text)
    except FetchError as e:
        return {"success": False, "error": str(e)}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"Request failed: {type(e).__name__}: {e}"}
    except LookupError:
        return {"success": False, "error": "The page uses an unknown text encoding."}
    note = ""
    if len(text) > limit:
        text = text[:limit]
        note = f"\n\n[Truncated to {limit} characters.]"
    elif len(body) >= MAX_BODY_BYTES:
        note = f"\n\n[Page cut at {MAX_BODY_BYTES // 1024} KB.]"
    return {"success": True, "url": current, "content": text + note}
