"""Agent-browser navigation allow-list and critical-step classifier."""
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
    "http://127.0.0.1:8000/", "http://127.1/", "http://2130706433/", "http://0x7f.0.0.1/",
    "http://localhost/", "http://LOCALHOST./", "http://app.localhost/", "http://0.0.0.0/",
    "http://10.1.2.3/", "http://172.16.0.1/", "http://172.31.255.255/", "http://192.168.1.1/",
    "http://169.254.169.254/", "http://[::1]/", "http://[fc00::1]/", "http://[fe80::1]/",
    "http://[::ffff:127.0.0.1]/", "http://user@127.0.0.1/", "https://example.com/\nx",
    " https://example.com/", "http://exa mple.com/", "https://example.com:99999/",
])
def test_unsafe_urls_are_refused_with_a_reason(url):
    assert policy.navigation_refusal(url)


def test_adjacent_public_ranges_are_not_refused():
    assert policy.navigation_refusal("http://172.32.0.1/") == ""
    assert policy.navigation_refusal("http://11.0.0.1/") == ""


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


def test_unidentified_element_is_critical():
    assert policy.critical_reason("click", None)


def test_fingerprint_is_normalised_and_distinguishes_targets():
    base = {"role": "Button", "name": "  Buy   Now ", "form_action": "/pay"}
    assert policy.fingerprint(base) == policy.fingerprint({"role": "button", "name": "buy now", "form_action": "/pay"})
    assert policy.fingerprint(base) != policy.fingerprint({**base, "form_action": "/other"})
    assert policy.fingerprint(base) != policy.fingerprint({**base, "name": "Buy later"})
