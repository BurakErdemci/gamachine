"""Agent-browser navigation allow-list and critical-step classifier."""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from browser_use import policy


@pytest.mark.parametrize("url", [
    "https://opengameart.org/", "http://example.com/x?q=1", "HTTPS://Example.COM/a",
    "https://8.8.8.8/", "https://[2606:4700::1111]/",
])
def test_public_http_pages_are_allowed(url):
    assert policy.navigation_refusal(url) == ""


@pytest.mark.parametrize("url", [
    "file:///etc/hosts", "data:text/html,hi", "javascript:alert(1)", "chrome://settings", "devtools://x",
    "ftp://example.com/", "about:blank", "", "   ", None, 5, "https://",
    "http://127.0.0.1:8000/", "http://127.1:8000/", "http://2130706433:8080/", "http://0x7f.0.0.1:8080/",
    "http://localhost:8080/", "http://LOCALHOST.:8000/", "http://app.localhost:8000/", "http://0.0.0.0/",
    "http://10.1.2.3/", "http://172.16.0.1/", "http://172.31.255.255/", "http://192.168.1.1/",
    "http://169.254.169.254/", "http://169.254.169.254:8080/latest/meta-data/", "http://100.64.0.1/",
    "http://[fc00::1]/", "http://[fe80::1]/", "http://[fd00::1]/", "http://224.0.0.1/", "http://240.0.0.1/",
    "http://[::1]:8000/", "http://[::ffff:127.0.0.1]:8000/", "http://user@127.0.0.1:8000/", "https://example.com/\nx",
    " https://example.com/", "http://exa mple.com/", "https://example.com:99999/",
])
def test_unsafe_urls_are_refused_with_a_reason(url):
    assert policy.navigation_refusal(url)


def test_adjacent_public_ranges_are_not_refused():
    assert policy.navigation_refusal("http://172.32.0.1/") == ""
    assert policy.navigation_refusal("http://11.0.0.1/") == ""


FREE = 53917


@pytest.fixture(autouse=True)
def _no_port_env(monkeypatch):
    for name in policy._GAMACHINE_PORT_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("url", [
    f"http://127.0.0.1:{FREE}/index.html", f"http://localhost:{FREE}/", f"https://LOCALHOST.:{FREE}/",
    f"http://app.localhost:{FREE}/", f"http://127.5.6.7:{FREE}/", f"http://[::1]:{FREE}/",
    f"http://[::ffff:127.0.0.1]:{FREE}/", f"http://127.1:{FREE}/", f"http://2130706433:{FREE}/",
    f"http://0x7f.0.0.1:{FREE}/", f"http://user@127.0.0.1:{FREE}/", "http://localhost/", "https://127.0.0.1/",
])
def test_loopback_pages_open_on_a_free_port(url):
    assert policy.navigation_refusal(url) == ""


@pytest.mark.parametrize("port", sorted(policy._GAMACHINE_DEFAULT_PORTS))
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "x.localhost", "[::1]", "[::ffff:127.0.0.1]",
                                  "127.1", "2130706433", "0x7f.0.0.1", "0177.0.0.1"])
def test_every_gamachine_port_is_refused_on_every_loopback_spelling(host, port):
    assert policy.navigation_refusal(f"http://{host}:{port}/")


def test_the_backend_and_unity_mcp_ports_are_refused(monkeypatch):
    for url in ("http://127.0.0.1:8000/", "http://127.0.0.1:8080/mcp", "http://localhost:8080/", "http://2130706433:8080/"):
        assert policy.navigation_refusal(url)
    monkeypatch.setenv("PORT", "54321")
    monkeypatch.setenv("UNITY_MCP_HTTP_URL", "http://localhost:9191")
    assert policy.navigation_refusal("http://127.0.0.1:54321/")
    assert policy.navigation_refusal("http://localhost:9191/health")
    assert policy.navigation_refusal(f"http://127.0.0.1:{FREE}/") == ""


def test_the_browser_hosts_own_port_is_refused_when_the_caller_names_it():
    assert policy.navigation_refusal("http://127.0.0.1:41234/cmd") == ""
    assert policy.navigation_refusal("http://127.0.0.1:41234/cmd", None, (41234,))


def test_an_unreadable_port_value_keeps_the_defaults_denied(monkeypatch):
    monkeypatch.setenv("PORT", "not-a-port")
    monkeypatch.setenv("UNITYAI_URL", "http://[broken")
    assert 8000 in policy.gamachine_ports() and 8080 in policy.gamachine_ports()
    assert policy.navigation_refusal("http://127.0.0.1:8000/")


def test_the_scheme_default_port_counts():
    assert 80 not in policy.gamachine_ports() and 443 not in policy.gamachine_ports()
    assert policy.navigation_refusal("http://localhost/") == ""
    assert policy.navigation_refusal("http://localhost/", None, (80,))


def test_a_public_name_is_not_touched_by_the_gamachine_ports():
    assert policy.navigation_refusal("http://example.com:8080/") == ""


@pytest.fixture
def workspace(tmp_path):
    folder = tmp_path / "workspace"
    folder.mkdir()
    (folder / "index.html").write_text("<p>hi</p>")
    (folder / "sub dir").mkdir()
    (folder / "sub dir" / "a b.png").write_bytes(b"x")
    return folder


def uri(path) -> str:
    return Path(path).as_uri()


def test_file_pages_open_inside_the_workspace_and_the_temp_dir(workspace):
    allowed = [str(workspace), os.path.realpath(tempfile.gettempdir())]
    assert policy.navigation_refusal(uri(workspace / "index.html"), allowed) == ""
    assert policy.navigation_refusal(uri(workspace / "sub dir" / "a b.png"), allowed) == ""
    assert policy.navigation_refusal(uri(workspace / "not-yet.html"), allowed) == ""
    assert policy.navigation_refusal(uri(workspace), allowed) == ""  # a directory listing
    assert policy.navigation_refusal(uri(workspace / "index.html") + "?x=1#top", allowed) == ""
    temp_file = Path(tempfile.gettempdir()) / "gamachine-shot.png"
    assert policy.navigation_refusal(uri(temp_file), allowed) == ""


def test_file_pages_are_refused_without_roots_and_outside_them(workspace, tmp_path):
    outside = tmp_path / "other"
    outside.mkdir()
    (outside / "secret.html").write_text("x")
    assert policy.navigation_refusal(uri(workspace / "index.html"))
    assert policy.navigation_refusal(uri(workspace / "index.html"), [])
    assert policy.navigation_refusal(uri(outside / "secret.html"), [str(workspace)])
    assert policy.navigation_refusal(uri(Path(__file__)), [str(workspace)])
    assert policy.navigation_refusal("file:///", [str(workspace)])
    sibling = Path(str(workspace) + "-evil")
    sibling.mkdir()
    assert policy.navigation_refusal(uri(sibling / "x.html"), [str(workspace)])


def test_file_paths_that_climb_or_hide_a_separator_are_refused(workspace, tmp_path):
    (tmp_path / "other").mkdir()
    base = uri(workspace)
    for url in (f"{base}/../other/secret.html", f"{base}/%2e%2e/other/secret.html", f"{base}/..%2fother/secret.html",
                f"{base}/..%5cother", f"{base}/a%00.html"):
        assert policy.navigation_refusal(url, [str(workspace)]), url


def test_file_paths_with_streams_unc_or_device_names_are_refused(workspace):
    backslash = chr(92)
    base = uri(workspace)
    urls = [f"{base}/index.html:stream", f"{base}/index.html::$DATA", f"{base}/trailing./x", f"{base}/trailing /x",
            f"{base}/CON", f"{base}/nul.txt", "file://server/share/x.html", "file:////server/share/x.html",
            f"file:///{backslash}{backslash}server{backslash}share", f"file://localhost{backslash}x",
            "file:///" + backslash * 2 + "?" + backslash + "C:" + backslash + "x"]
    for url in urls:
        assert policy.navigation_refusal(url, [str(workspace)]), url


def test_a_link_that_points_out_of_the_workspace_is_refused(workspace, tmp_path):
    outside = tmp_path / "other"
    outside.mkdir()
    (outside / "secret.html").write_text("x")
    link = workspace / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True) if os.name == "nt" else None
        if made is None or made.returncode != 0:
            pytest.skip("cannot create a link here")
    assert policy.navigation_refusal(uri(workspace / "link" / "secret.html"), [str(workspace)])
    assert policy.navigation_refusal(uri(workspace / "index.html"), [str(workspace)]) == ""


def element(name="Show more", role="button", **extra):
    return {"role": role, "name": name, "tag": "button", **extra}


@pytest.mark.parametrize("op,fields,critical", [
    ("click", {"name": "Show more"}, False),
    ("click", {"name": "Delete account"}, True),
    ("click", {"name": "Satın al"}, True),
    ("click", {"name": "Buy now"}, True),
    ("click", {"name": "Go", "form_submit_name": "Checkout"}, True),
    ("click", {"name": "Go", "submits_form": True, "form_method": "get"}, False),
    ("click", {"name": "Go", "submits_form": True, "form_method": "post"}, True),
    ("click", {"name": "Go", "submits_form": True}, True),
    ("click", {"name": "Go", "submits_form": True, "form_method": "get", "form_has_password": True}, True),
    ("click", {"name": "Go", "form_method": "post"}, False),
    ("click", {"name": "Next", "role": "link", "href": "https://example.com/p2"}, False),
    ("click", {"name": "walk", "role": "link", "href": "https://example.com/walk.ZIP?x=1"}, True),
    ("click", {"name": "walk", "role": "link", "href": "https://example.com/walk.png"}, False),
    ("click", {"name": "walk", "role": "link", "download": True}, True),
    ("fill", {"name": "Search assets", "type": "search"}, False),
    ("fill", {"name": "Secret", "type": "password"}, True),
    ("fill", {"name": "Anything", "is_password": True}, True),
    ("select", {"name": "Sort by"}, False),
    ("press", {"name": "Search", "submits_form": True, "form_method": "get"}, False),
    ("press", {"name": "Search", "form_method": "post"}, True),
    ("press", {"name": "Search", "form_method": "get"}, False),
])
def test_critical_reason(op, fields, critical):
    reason = policy.critical_reason(op, element(**fields))
    assert bool(reason) is critical
    if critical:
        assert op in reason


FIELD = {"role": "textbox", "name": "Email", "tag": "input", "type": "text", "submits_form": True}


@pytest.mark.parametrize("op,fields,critical", [
    # Clicking a field only focuses it, even when Enter in it would submit the form.
    ("click", {**FIELD, "form_method": "post"}, False),
    ("click", {**FIELD, "form_method": "get", "form_has_password": True}, False),
    ("click", {**FIELD, "role": "searchbox", "tag": "input", "form_method": "post"}, False),
    ("press", {**FIELD, "form_method": "post"}, True),
    ("press", {**FIELD, "form_method": "get", "form_has_password": True}, True),
    ("press", {**FIELD, "form_method": "get"}, False),
    ("click", {"role": "button", "name": "Continue", "tag": "button", "type": "submit",
               "submits_form": True, "form_method": "post"}, True),
    ("click", {"role": "button", "name": "Continue", "tag": "input", "type": "submit",
               "submits_form": True, "form_method": "post"}, True),
    ("click", {"role": "button", "name": "Go", "tag": "input", "type": "image",
               "submits_form": True, "form_method": "dialog"}, True),
    ("click", {"role": "button", "name": "Continue", "tag": "div", "submits_form": True,
               "form_method": "post"}, True),
    ("click", {"role": "button", "name": "Continue", "tag": "button", "submits_form": True,
               "form_method": "get", "form_has_password": True}, True),
])
def test_only_a_button_click_submits_but_enter_in_a_field_does(op, fields, critical):
    assert bool(policy.critical_reason(op, fields)) is critical


def test_unidentified_element_is_critical():
    assert policy.critical_reason("click", None)


def test_fingerprint_is_normalised_and_distinguishes_targets():
    base = {"role": "Button", "name": "  Buy   Now ", "form_action": "/pay"}
    assert policy.fingerprint(base) == policy.fingerprint({"role": "button", "name": "buy now", "form_action": "/pay"})
    assert policy.fingerprint(base) != policy.fingerprint({**base, "form_action": "/other"})
    assert policy.fingerprint(base) != policy.fingerprint({**base, "name": "Buy later"})


def test_display_name_strips_format_characters_and_caps():
    assert policy.display_name("a\u202eb\x00 \n c") == "ab c"
    assert policy.display_name("x" * 500).endswith("…") and len(policy.display_name("x" * 500)) == 120
    assert policy.display_name(None) == ""
