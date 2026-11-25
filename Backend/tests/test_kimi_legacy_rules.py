"""Tests for C2 (Kimi legacy config whole-rule checks), T1 (write failure), and S2."""
import os
import re
from unittest.mock import patch

from providers.kimi_provider import KimiProvider


def _legacy_without(*missing_patterns, extra=""):
    lines = [
        '[providers.kimi]\napi_key = "keep-me"\n',
        KimiProvider._KIMI_LEGACY_MARKER,
        extra,
    ]
    for pattern in KimiProvider._KIMI_DENY_PATTERNS:
        if pattern not in missing_patterns:
            lines.append(f'[[permission.rules]]\ndecision = "deny"\npattern = "{pattern}"\n')
    for pattern in KimiProvider._KIMI_NEWER_ALLOW_PATTERNS:
        if pattern not in missing_patterns:
            lines.append(f'[[permission.rules]]\ndecision = "allow"\npattern = "{pattern}"\n')
    lines.append("# <<< unity-architect managed <<<\n")
    return "\n".join(line for line in lines if line)


def test_legacy_file_with_user_allow_for_agent_gets_deny_appended_and_confirmed_only_after(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    user_allow = '[[permission.rules]]\ndecision = "allow"\npattern = "Agent"\n'
    config.write_text(_legacy_without("Agent", extra=user_allow), encoding="utf-8")

    provider = KimiProvider(binary_name="kimi-k3")
    # Before fix: '"Agent"' matched the user allow rule and wrongly confirmed!
    assert provider._kimi_permissions_confirmed(str(config)) is False

    assert provider._write_kimi_permissions() is True
    updated = config.read_text(encoding="utf-8")
    assert user_allow in updated
    assert 'decision = "deny"\npattern = "Agent"' in updated
    assert provider._kimi_permissions_confirmed(str(config)) is True
    # Idempotent on a second call.
    assert provider._write_kimi_permissions() is True
    assert len(re.findall(r'decision = "deny"\npattern = "Agent"', updated)) == 1


def test_legacy_file_missing_one_deny_rule_is_not_confirmed_until_written(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text(_legacy_without("Bash"), encoding="utf-8")

    provider = KimiProvider(binary_name="kimi-k3")
    assert provider._kimi_permissions_confirmed(str(config)) is False
    assert provider._write_kimi_permissions() is True
    assert 'decision = "deny"\npattern = "Bash"' in config.read_text(encoding="utf-8")
    assert provider._kimi_permissions_confirmed(str(config)) is True


def test_write_failure_after_successful_read_returns_false(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text(_legacy_without("Bash"), encoding="utf-8")

    provider = KimiProvider(binary_name="kimi-k3")
    real_open = os.open

    def fail_append(path, flags, *args, **kwargs):
        if str(path) == str(config) and (flags & os.O_APPEND):
            raise PermissionError("read-only config.toml")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("providers.kimi_provider.os.open", fail_append)
    assert provider._write_kimi_permissions() is False
    assert provider._kimi_permissions_confirmed(str(config)) is False


def test_build_cmd_does_not_write_kimi_permissions_directly(tmp_path, monkeypatch):
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    provider = KimiProvider(binary_name="kimi-k3")
    monkeypatch.setattr(provider, "_ensure_exec", lambda path: None)
    with patch.object(provider, "_write_kimi_permissions") as write_perms:
        provider._build_cmd("hello", workspace=str(tmp_path))
    write_perms.assert_not_called()
