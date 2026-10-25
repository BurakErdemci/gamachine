import pytest

from agentic.agent_runner import _urunun_kaydi_mi


@pytest.mark.parametrize("value, secret, expected", [
    ("şifre-🔑", "şifre-🔑", True),
    ("başka-🔑", "şifre-🔑", False),
    ("şifre-🔑", "ascii-token", False),
    ("ascii-token", "şifre-🔑", False),
    ("ascii-token", "ascii-token", True),
    ("other-token", "ascii-token", False),
    ("\ud800", "ascii-token", False),
    ("\udfff", "ascii-token", False),
    ("ascii-token", "\ud800", False),
    ("ascii-token", "\udfff", False),
])
@pytest.mark.parametrize("source", ["header", "env"])
def test_token_comparison_handles_unicode(monkeypatch, value, secret, expected, source):
    import local_token_file

    monkeypatch.setattr(local_token_file, "read_local_app_token", lambda: secret)
    entry = ({"headers": {"X-API-Key": value}} if source == "header"
             else {"env": {"LOCAL_APP_TOKEN": value}})

    assert _urunun_kaydi_mi("custom-server", entry, secret) is expected
