"""Navigation allow-list and the critical-step classifier for the agent browser."""
import ipaddress
import re
import socket
import unicodedata
from urllib.parse import urlsplit

from computer_use.policy import critical_name

_DOWNLOAD_SUFFIXES = (
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst",
    ".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".apk", ".appimage", ".iso",
    ".jar", ".bat", ".cmd", ".sh", ".unitypackage",
)
_NAME_LIMIT = 120
_FILLERS = "\u115f\u1160\u3164\uffa0"


def _blocked_address(ip) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified
            or ip.is_multicast or ip.is_reserved)


def navigation_refusal(url) -> str:
    """'' when the URL may be opened, otherwise the reason it is refused."""
    if not isinstance(url, str) or not url.strip():
        return "The URL is empty."
    if any(ord(char) < 0x20 or ord(char) == 0x7f for char in url) or url != url.strip():
        return "The URL contains control characters or surrounding whitespace."
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port
    except ValueError:
        return "The URL is not valid."
    if parts.scheme.lower() not in {"http", "https"}:
        return "Only http and https pages can be opened."
    if not host or re.search(r"\s", host):
        return "The URL has no valid host."
    host = host.rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        return "Local addresses cannot be opened in the agent browser."
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        try:
            # Legacy spellings such as 2130706433 or 0x7f.1 reach the same loopback address.
            ip = ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return ""
    if _blocked_address(ip):
        return "Local and private network addresses cannot be opened in the agent browser."
    return ""


def display_name(value, limit: int = _NAME_LIMIT) -> str:
    """Page-controlled text made safe to show on a card: no control or format characters, capped."""
    if not isinstance(value, str):
        return ""
    value = "".join(char for char in value
                    if unicodedata.category(char) not in {"Cf", "Cc"} and char not in _FILLERS)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _clean(value) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def fingerprint(element) -> str:
    """Role + name + form action, normalised: what a one-time grant is keyed on."""
    return "|".join(_clean(element.get(key)).casefold() for key in ("role", "name", "form_action"))


def _is_download(element) -> bool:
    if element.get("download") is True:
        return True
    href = element.get("href")
    if not isinstance(href, str):
        return False
    try:
        path = urlsplit(href).path.casefold()
    except ValueError:
        return False
    return path.endswith(_DOWNLOAD_SUFFIXES)


def _is_submit_control(element) -> bool:
    return (element.get("tag") == "button" or element.get("role") == "button"
            or (element.get("tag") == "input" and element.get("type") in {"submit", "image"}))


def critical_reason(op, element) -> str:
    """'' when the step may run without a card, otherwise what makes it critical."""
    if not isinstance(element, dict):
        return "the target element could not be identified"
    name = _clean(element.get("name"))
    shown = f'"{display_name(name)}"' if name else "an unnamed element"
    if element.get("is_password") is True or element.get("type") == "password":
        return f"{op} on the password field {shown}"
    for label in (name, _clean(element.get("form_submit_name"))):
        if label:
            keyword = critical_name(label)
            if keyword:
                return f'{op} on {shown} (matches "{keyword}")'
    if _is_download(element):
        return f"{op} on {shown} starts a download"
    # describe marks a text field submits_form too (Enter would submit it), but clicking one only
    # focuses it; Enter in an input has no submit button of its own, so the field's form decides.
    if ((op == "click" and element.get("submits_form") is True and _is_submit_control(element))
            or (op == "press" and (element.get("submits_form") is True
                                   or element.get("form_method") is not None))):
        if element.get("form_has_password") is True:
            return f"{op} on {shown} submits a form that contains a password field"
        if element.get("form_method") != "get":
            return f"{op} on {shown} submits a form"
    return ""
