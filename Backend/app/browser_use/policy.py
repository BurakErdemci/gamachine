"""Navigation allow-list and the critical-step classifier for the agent browser."""
import ipaddress
import json
import os
import re
import socket
import sys
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit

from computer_use.policy import critical_name
from path_containment import is_within

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
    ".rpm", ".apk", ".command", ".url", ".msixbundle", ".appxbundle", ".appinstaller", ".application",
    ".gadget", ".vb", ".ws", ".sct", ".chm", ".inf", ".msp", ".mst", ".jnlp", ".xll", ".xla", ".xlam", ".scf",
    ".iso", ".img", ".vhd", ".vhdx", ".library-ms", ".settingcontent-ms", ".diagcab", ".website", ".wsc", ".pyz", ".pyc",
    ".appref-ms", ".shs", ".msh", ".msh1", ".msh2", ".mshxml", ".rdp", ".search-ms", ".theme", ".themepack",
    ".docm", ".xlsm", ".ade", ".adp", ".mde",
    ".pptm", ".dotm", ".xltm", ".potm", ".ppam", ".sldm", ".ppsm", ".xlsb", ".iqy", ".slk", ".psc1", ".psc2", ".msu", ".accde", ".accdr", ".mdb", ".ps2",
)
# What browser_download_to_project never puts into a project, whatever the download was: Unity code,
# compiler input and native plugins would be compiled or loaded by the editor. Shaders are allowed.
AGENT_BLOCKED_PROJECT_SUFFIXES = (
    ".cs", ".asmdef", ".asmref", ".rsp", ".dll", ".so", ".dylib", ".bundle", ".jslib", ".jspre", ".mm",
    ".m", ".cpp", ".c", ".h", ".hpp", ".aar", ".jar", ".java", ".kt", ".swift",
)
# Folders whose contents Unity loads as plugins or editor code.
_BLOCKED_PROJECT_FOLDERS = frozenset({"plugins", "editor"})
# Characters Windows rejects in a file or folder name.
_WINDOWS_REJECTS = re.compile(r'[<>:"|?*]')
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
    # Compared the way Windows stores the name: "Editor." and "Editor " are the Editor folder.
    if any(part.rstrip(". ").strip().casefold() in _BLOCKED_PROJECT_FOLDERS for part in parts):
        return "nothing is copied into a Plugins or Editor folder"
    return ""


def project_dest_refusal(parts) -> str:
    """Why a copy may not go to the project-relative path made of `parts`, or '': only under Assets/, where Unity
    imports, with plain names Windows keeps as written, never a hidden (dot) name, and with a file type."""
    if not parts or not (parts[0].isascii() and parts[0].lower() == "assets"):
        return "a download is copied into the Assets folder only, so dest must start with Assets/"
    for part in parts:
        if part.startswith("."):
            return "no file or folder name in dest may start with a dot"
        if (part.endswith((".", " ")) or _WINDOWS_REJECTS.search(part)
                or any(unicodedata.category(char) in {"Cc", "Cf"} for char in part)):
            return "dest has a name Windows would change or reject (a trailing dot or space, <>:\"|?* or an invisible character)"
    if not file_type(parts[-1]):
        return "dest must end in a file name with an extension"
    return ""


def file_type(name) -> str:
    """The last extension of a file name, lower case ('' for none): a copy keeps the download's type."""
    base = _file_name(name)
    dot = base.rfind(".")
    return base[dot:] if dot > 0 else ""


# Ports of Gamachine's own local listeners: a page opened in the agent browser must never reach them
# (the backend and the browser host carry tokens, the Unity MCP server drives the editor). Each default
# below is the port the named code listens on when nothing overrides it; gamachine_ports() adds the values
# this process knows at run time (the whisper STT server listens on a random loopback port).
_GAMACHINE_DEFAULT_PORTS = frozenset({
    8000,  # backend HTTP: main.py PORT default, docker-compose.yml, Docker backend mode
    8080,  # Unity MCP HTTP server: unity_mcp_manager.mcp_port, mcp_port_guard.DEFAULT_MCP_PORT
    *range(6400, 6500),  # Unity editor bridge: port_discovery.py DEFAULT_PORT, PortManager.cs falls back DefaultPort+1..+99
    9876,  # Blender connector bridge: connectors/views.py BRIDGE_PORTS
    8799,  # relay dev server: relay/package.json "wrangler dev --port 8799"
    8888,  # nextron renderer dev server (Electron main loads it in dev)
})
# Environment values that carry a port Gamachine listens on (an int, or a URL with a port).
_GAMACHINE_PORT_ENV = ("PORT", "UNITY_MCP_HTTP_PORT", "UNITY_MCP_HTTP_URL", "UNITYAI_URL", "ANTIGRAVITY_URL")
_DEFAULT_SCHEME_PORT = {"http": 80, "https": 443}
_FILE_RESERVED_NAMES = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$"}
                                 | {f"com{n}" for n in range(1, 10)} | {f"lpt{n}" for n in range(1, 10)})


def _port_of(value) -> int | None:
    """The port in an int, a numeric string or a URL, or None."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if 0 < value < 65536 else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        port = int(text) if text.isdigit() else urlsplit(text).port
    except ValueError:
        return None
    return port if port and 0 < port < 65536 else None


def gamachine_ports(extra=()) -> frozenset:
    """Every port Gamachine's own services may listen on: the defaults above, the ports this process was
    configured with (environment), the Unity MCP manager's port when it is loaded, and `extra` (the browser
    host's command port, which only the caller knows). A value that cannot be read adds nothing and the
    defaults stay denied."""
    ports = set(_GAMACHINE_DEFAULT_PORTS)
    ports.update(port for name in _GAMACHINE_PORT_ENV if (port := _port_of(os.environ.get(name))))
    manager = getattr(sys.modules.get("unity_ai_mcp.unity_mcp_manager"), "unity_mcp_manager", None)
    ports.update(port for port in (_port_of(getattr(manager, "mcp_port", None)),) if port)
    # providers/stt_whisper.py _spawn(): the whisper server gets a random free port once it exists.
    whisper = getattr(getattr(sys.modules.get("providers.stt_whisper"), "_server", None), "_port", None)
    ports.update(port for port in (_port_of(whisper),) if port)
    ports.update(port for port in map(_port_of, extra or ()) if port)
    return frozenset(ports)


def _unmapped(ip):
    return getattr(ip, "ipv4_mapped", None) or ip


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


def _embeds_ipv4(ip) -> bool:
    """IPv6 forms that carry an IPv4 address (mapped, compatible, NAT64, 6to4, Teredo): refused whatever they
    carry, in both copies of the rule; loopback is judged before this."""
    return ip.version == 6 and bool(ip.ipv4_mapped or ip.sixtofour or ip.teredo
                                    or ip in _NAT64 or ip in _IPV4_COMPATIBLE)


def _blocked_address(ip) -> bool:
    """Private, link-local (metadata), CGNAT, unspecified, multicast and reserved addresses. Loopback is
    judged by the caller, which knows the port."""
    ip = _unmapped(ip)
    return (ip.is_private or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved
            or getattr(ip, "is_site_local", False) or not ip.is_global)


def _file_refusal(parts, roots) -> str:
    """'' when the file: URL names a path inside one of `roots` (the chat's workspace, the OS temp dir)."""
    if not roots:
        return "Only http and https pages can be opened."
    if parts.netloc.casefold() not in {"", "localhost"}:
        return "Network file shares cannot be opened in the agent browser."
    raw = parts.path
    if not raw.startswith("/"):
        return "The file address is not a plain local path."
    if re.search(r"%(2f|5c)", raw, re.I):
        return "The file address hides a path separator."
    path = unquote(raw)
    if "\\" in path or any(ord(char) < 0x20 or ord(char) == 0x7f for char in path) or path.startswith("//"):
        return "The file address is not a plain local path."
    if os.name == "nt":
        drive = re.match(r"/([A-Za-z]:)(?=/|$)", path)
        if not drive:
            return "The file address is not a plain local path."
        path = drive.group(1) + path[len(drive.group(0)):]
        rest = path[2:]
    else:
        rest = path
    names = [name for name in rest.split("/") if name not in ("", ".", "..")]
    if ":" in rest or any(name.endswith((".", " ")) or name.split(".")[0].casefold() in _FILE_RESERVED_NAMES
                          for name in names):
        return "The file address is not a plain local path."
    roots = [root for root in roots if isinstance(root, str) and root]
    if not any(is_within(path, root) for root in roots):
        return "Only files inside this chat's project or the temp folder can be opened."
    # Inside the temp folder only: Gamachine's own files (gamachine.log, its temp folders) stay closed.
    temp = os.path.realpath(tempfile.gettempdir())
    if not any(is_within(path, root) for root in roots if os.path.realpath(root) != temp):
        real = os.path.realpath(path)
        if is_within(real, temp) and any(name.casefold().startswith("gamachine")
                                         for name in os.path.relpath(real, temp).replace("\\", "/").split("/")):
            return "Gamachine's own files cannot be opened in the agent browser."
    return ""


def navigation_refusal(url, file_roots=None, extra_ports=()) -> str:
    """'' when the URL may be opened, otherwise the reason it is refused. Loopback pages open except on a
    Gamachine port; file: opens only inside `file_roots`; `extra_ports` are more Gamachine ports."""
    if not isinstance(url, str) or not url.strip():
        return "The URL is empty."
    if any(ord(char) < 0x20 or ord(char) == 0x7f for char in url) or url != url.strip():
        return "The URL contains control characters or surrounding whitespace."
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "The URL is not valid."
    scheme = parts.scheme.lower()
    if scheme == "file":
        return _file_refusal(parts, file_roots)
    if scheme not in _DEFAULT_SCHEME_PORT:
        return "Only http and https pages can be opened."
    # Spellings a browser reads differently from this parser: no slashes after the scheme, backslashes,
    # percent-encoded or non-ASCII (full-width dots) host.
    if (url[len(scheme):len(scheme) + 3] != "://" or "\\" in url
            or "%" in parts.netloc or not parts.netloc.isascii()):
        return "The URL is not written in a plain form."
    if not host or re.search(r"\s", host) or port == 0:
        return "The URL has no valid host or port."
    host = host.rstrip(".").casefold()
    ip = None
    if not (host == "localhost" or host.endswith(".localhost")):
        try:
            ip = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            try:
                # Legacy spellings such as 2130706433 or 0x7f.1 reach the same loopback address.
                ip = ipaddress.ip_address(socket.inet_aton(host))
            except OSError:
                return ""
        if not _unmapped(ip).is_loopback:
            return ("Private network addresses cannot be opened in the agent browser."
                    if _embeds_ipv4(ip) or _blocked_address(ip) else "")
    if (port or _DEFAULT_SCHEME_PORT[scheme]) in gamachine_ports(extra_ports):
        return "Gamachine's own local services cannot be opened in the agent browser."
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
    return _critical(op, element)[1]


def download_click(op, element) -> bool:
    """Whether the card for this click opens because it starts a download, and not for an earlier reason."""
    return op == "click" and _critical(op, element)[0] == "download"


def _critical(op, element) -> tuple:
    """(kind, reason): kind names the rule that made the step critical ('' when it is not)."""
    if not isinstance(element, dict):
        return "unknown", "the target element could not be identified"
    name = _clean(element.get("name"))
    shown = f'"{display_name(name)}"' if name else "an unnamed element"
    if element.get("is_password") is True or element.get("type") == "password":
        return "password", f"{op} on the password field {shown}"
    for label in (name, _clean(element.get("form_submit_name"))):
        if label:
            keyword = critical_name(label)
            if keyword:
                return "keyword", f'{op} on {shown} (matches "{keyword}")'
    if _is_download(element):
        return "download", f"{op} on {shown} starts a download"
    # describe marks a text field submits_form too (Enter would submit it), but clicking one only
    # focuses it; Enter in an input has no submit button of its own, so the field's form decides.
    if ((op == "click" and element.get("submits_form") is True and _is_submit_control(element))
            or (op == "press" and (element.get("submits_form") is True
                                   or element.get("form_method") is not None))):
        if element.get("form_has_password") is True:
            return "form", f"{op} on {shown} submits a form that contains a password field"
        if element.get("form_method") != "get":
            return "form", f"{op} on {shown} submits a form"
    return "", ""
