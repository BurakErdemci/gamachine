"""Small user-facing fixes: Kimi legacy-config merge and the remote path's UI language header."""
import os

from providers.kimi_provider import KimiProvider
from tests.test_remote_ui_dispatch import call, ui  # noqa: F401  (fixture)

COMPUTER_RULE = 'pattern = "mcp__gamachineComputer__*"'
BROWSER_RULE = 'pattern = "mcp__gamachineBrowser__*"'


def _legacy_config(tmp_path, monkeypatch, extra=""):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    provider = KimiProvider(binary_name="kimi-k3")
    path = provider._kimi_config_path()
    os.makedirs(os.path.dirname(path))
    legacy = (
        'key = "secret"\n'
        f"{provider._KIMI_LEGACY_MARKER}\n"
        '[[permission.rules]]\ndecision = "deny"\npattern = "Write"\n\n'
        '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__unityai__*"\n'
        "# <<< unity-architect managed <<<\n" + extra
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(legacy)
    return provider, path, legacy


def test_kimi_legacy_config_gets_the_newer_allow_rules_once(tmp_path, monkeypatch):
    provider, path, legacy = _legacy_config(tmp_path, monkeypatch)
    provider._write_kimi_permissions()
    first = open(path, encoding="utf-8").read()
    assert first.startswith(legacy)
    assert first.count(COMPUTER_RULE) == 1 and first.count(BROWSER_RULE) == 1
    provider._write_kimi_permissions()
    assert open(path, encoding="utf-8").read() == first


def test_kimi_legacy_config_only_adds_the_missing_rule(tmp_path, monkeypatch):
    user_rule = f'\n[[permission.rules]]\ndecision = "allow"\n{COMPUTER_RULE}\n'
    provider, path, legacy = _legacy_config(tmp_path, monkeypatch, user_rule)
    provider._write_kimi_permissions()
    text = open(path, encoding="utf-8").read()
    assert text.startswith(legacy)
    assert text.count(COMPUTER_RULE) == 1 and text.count(BROWSER_RULE) == 1


async def test_remote_requests_carry_the_desktop_ui_language(ui):  # noqa: F811
    ui.bridge.current_desktop_ui = lambda: {"lang": "en", "theme": "arena"}
    r = await call(ui, type="ui_http", method="GET", path="/conversations/7/messages")
    assert r["ok"] is True
    assert ui.seen[0]["x-ui-lang"] == "en"


async def test_remote_requests_without_a_reported_language_send_no_header(ui):  # noqa: F811
    ui.bridge.current_desktop_ui = lambda: None
    await call(ui, type="ui_http", method="GET", path="/conversations/7/messages")
    assert "x-ui-lang" not in ui.seen[0]
