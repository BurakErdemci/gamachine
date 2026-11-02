"""Connector catalog: the shipped Blender entry and the validation rules every entry must pass."""
import json
import shutil

import pytest

from connectors import catalog


def _copy_blender(tmp_path):
    root = tmp_path / "catalog"
    shutil.copytree(catalog.catalog_dir() / "blender", root / "blender")
    return root, root / "blender" / "connector.json"


def _rewrite(path, change):
    entry = json.loads(path.read_text(encoding="utf-8"))
    change(entry)
    path.write_text(json.dumps(entry), encoding="utf-8")


def test_shipped_catalog_has_a_valid_hash_locked_blender_entry():
    entries = catalog.load()
    blender = entries["blender"]
    assert (blender["package"]["name"], blender["package"]["version"]) == ("mcp-for-blender", "2.2.6")
    assert {"get_scene_info", "get_addon_status", "execute_blender_code", "look"} == set(blender["tools"]["allow"])
    assert blender["env"] == {"DISABLE_TELEMETRY": "true", "BLENDER_MCP_DISABLE_TELEMETRY": "true",
                              "MCP_DISABLE_TELEMETRY": "true"}
    assert blender["package"]["approx_download_mb"] > 0 and blender["package"]["approx_install_s"] > 0
    assert catalog.lock_path(blender).is_file()


@pytest.mark.parametrize("tool", sorted(catalog.BANNED_TOOLS))
def test_entry_allowing_a_banned_tool_is_rejected(tmp_path, tool):
    root, path = _copy_blender(tmp_path)
    _rewrite(path, lambda e: e["tools"]["allow"].append(tool))
    with pytest.raises(catalog.CatalogError, match="banned"):
        catalog.load(root)


def test_unhashed_lock_line_is_rejected(tmp_path):
    root, _ = _copy_blender(tmp_path)
    lock = root / "blender" / "requirements.lock"
    lock.write_text(lock.read_text(encoding="utf-8") + "extra-package==1.0\n", encoding="utf-8")
    with pytest.raises(catalog.CatalogError, match="hash"):
        catalog.load(root)


def test_lock_without_the_pin_is_rejected(tmp_path):
    root, path = _copy_blender(tmp_path)
    _rewrite(path, lambda e: e["package"].update(version="9.9.9"))
    with pytest.raises(catalog.CatalogError, match="pin"):
        catalog.load(root)


@pytest.mark.parametrize("change,match", [
    (lambda e: e.update(id="other"), "folder"),
    (lambda e: e["summary"].pop("en"), "summary"),
    (lambda e: e["capabilities"].update(tr=[]), "capabilities"),
    (lambda e: e["package"].update(approx_download_mb=0), "approx_download_mb"),
    (lambda e: e["package"].update(approx_install_s=True), "approx_install_s"),
    (lambda e: e["package"].update(lock="../x.lock"), "lock"),
    (lambda e: e["app"].update(download_url="http://example.org"), "download_url"),
    (lambda e: e["env"].update(X=1), "env"),
    (lambda e: e["tools"].update(allow=[]), "tools.allow"),
    (lambda e: e["package"].pop("addon_protocol"), "addon_protocol"),
    (lambda e: e["package"].update(addon_protocol="15"), "addon_protocol"),
])
def test_schema_violations_are_rejected(tmp_path, change, match):
    root, path = _copy_blender(tmp_path)
    _rewrite(path, change)
    with pytest.raises(catalog.CatalogError, match=match):
        catalog.load(root)
