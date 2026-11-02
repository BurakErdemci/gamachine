"""Navigation allow-list and the critical-step classifier for the agent browser."""
import ipaddress
import json
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
# Types an agent-started download never saves and browser_download_to_project never copies, with or
# without approval cards. The host keeps its own copy of this list (main/helpers/browser-host.ts).
AGENT_BLOCKED_DOWNLOAD_SUFFIXES = (
    ".exe", ".msi", ".msix", ".appx", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".scr", ".pif", ".cpl", ".msc", ".hta", ".reg", ".lnk", ".dll", ".sys",
    ".jar", ".py", ".pyw", ".sh", ".bash", ".zsh", ".dmg", ".pkg", ".app", ".appimage", ".deb",
    ".rpm", ".apk", ".command",
)
# What browser_download_to_project never puts into a project, whatever the download was: Unity code,
# compiler input and native plugins would be compiled or loaded by the editor. Shaders are allowed.
AGENT_BLOCKED_PROJECT_SUFFIXES = (
    ".cs", ".asmdef", ".asmref", ".rsp", ".dll", ".so", ".dylib", ".bundle", ".jslib", ".jspre", ".mm",
    ".m", ".cpp", ".c", ".h", ".hpp", ".aar", ".jar", ".java", ".kt", ".swift",
)
# Folders whose contents Unity loads as plugins or editor code.
_BLOCKED_PROJECT_FOLDERS = frozenset({"plugins", "editor"})
_NAME_LIMIT = 120
_FILLERS = "\u115f\u1160\u3164\uffa0"


def _file_name(name) -> str:
    # Windows drops trailing dots and spaces, so "setup.exe. " is an .exe.
    return re.split(r"[\\/]", name)[-1].rstrip(". ").casefold() if isinstance(name, str) else ""


def _ends_in(name, suffixes) -> str:
    base = _file_name(name)
    return next((suffix for suffix in suffixes if base.endswith(suffix)), "")


def agent_blocked_suffix(name) -> str:
    """The blocked type `name` ends in ('.exe' for "song.pdf.exe"), or '' when an agent may save it."""
    return _ends_in(name, AGENT_BLOCKED_DOWNLOAD_SUFFIXES)


def project_copy_refusal(path) -> str:
    """Why a download may not be copied to `path` (a file name or a project-relative path), or ''."""
    blocked = _ends_in(path, AGENT_BLOCKED_DOWNLOAD_SUFFIXES + AGENT_BLOCKED_PROJECT_SUFFIXES)
    if blocked:
        return f"{blocked} files are never copied into the project"
    parts = re.split(r"[\\/]", path) if isinstance(path, str) else []
    if any(part.strip().casefold() in _BLOCKED_PROJECT_FOLDERS for part in parts):
        return "nothing is copied into a Plugins or Editor folder"
    return ""


def file_type(name) -> str:
    """The last extension of a file name, lower case ('' for none): a copy keeps the download's type."""
    base = _file_name(name)
    dot = base.rfind(".")
    return base[dot:] if dot > 0 else ""


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
    """Role + name + form action + a link's href: what a one-time grant is keyed on. The
    human-facing role and name are case-folded; URLs stay exact, a changed path is another target."""
    # JSON, not a joined string: a page-controlled name holding "|" must not shift text between fields.
    return json.dumps([_clean(element.get("role")).casefold(), _clean(element.get("name")).casefold(),
                       _clean(element.get("form_action")), _clean(element.get("href"))],
                      ensure_ascii=False, separators=(",", ":"))


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
