import json

import pytest

from unity_ai_mcp import unity_mcp_manager as module


@pytest.mark.parametrize("failure", ["serialize", "fsync", "replace", None])
def test_manifest_install_preserves_original_on_failure(tmp_path, monkeypatch, failure):
    manifest = tmp_path / "Packages" / "manifest.json"
    manifest.parent.mkdir()
    original = b'{"dependencies":{"com.example.game":"1.2.3"},"scopedRegistries":[]}'
    manifest.write_bytes(original)
    manager = module.UnityMCPManager()
    monkeypatch.setattr(manager, "_ensure_writable_resources", lambda: None)

    def interrupted_dump(data, handle, **kwargs):
        handle.write('{"dependencies":')
        raise OSError("disk full")

    def denied(*args):
        raise OSError("denied")

    if failure == "serialize":
        monkeypatch.setattr(module.json, "dump", interrupted_dump)
    elif failure == "fsync":
        monkeypatch.setattr(module.os, "fsync", denied)
    elif failure == "replace":
        monkeypatch.setattr(module.os, "replace", denied)
    assert manager.install_package(str(tmp_path), write_autoconnect=False) is (failure is None)
    if failure:
        assert manifest.read_bytes() == original
    else:
        data = json.loads(manifest.read_bytes())
        assert data["dependencies"] == {
            "com.example.game": "1.2.3", "com.coplaydev.unity-mcp": manager.unity_mcp_repo,
        }
        assert data["scopedRegistries"] == []
    assert list(manifest.parent.iterdir()) == [manifest]
