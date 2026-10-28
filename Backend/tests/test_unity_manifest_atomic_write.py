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


@pytest.mark.parametrize("continuous", [False, True])
@pytest.mark.parametrize("edit_at", ["serialize", "fsync"])
def test_concurrent_manifest_changes_are_merged_or_rejected(tmp_path, monkeypatch, caplog, continuous, edit_at):
    manifest = tmp_path / "Packages" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text('{"dependencies":{"game":"1.0"}}', encoding="utf-8")
    manager = module.UnityMCPManager()
    monkeypatch.setattr(manager, "_ensure_writable_resources", lambda: None)
    dump = module.json.dump
    fsync = module.os.fsync
    edits = []
    promoted = []
    replace = module.os.replace

    def concurrent_edit():
        if not edits or continuous:
            data = json.loads(manifest.read_bytes())
            data["dependencies"][f"external-{len(edits)}"] = "2.0"
            data["scopedRegistries"] = [{"name": "external"}]
            manifest.write_text(json.dumps(data), encoding="utf-8")
            edits.append(manifest.read_bytes())

    def serialize(data, handle, **kwargs):
        if edit_at == "serialize":
            concurrent_edit()
        return dump(data, handle, **kwargs)

    def sync(descriptor):
        if edit_at == "fsync":
            concurrent_edit()
        return fsync(descriptor)

    def promote(source, target):
        promoted.append(target)
        return replace(source, target)

    monkeypatch.setattr(module.json, "dump", serialize)
    monkeypatch.setattr(module.os, "fsync", sync)
    monkeypatch.setattr(module.os, "replace", promote)
    assert manager.install_package(str(tmp_path), write_autoconnect=False) is (not continuous)
    if continuous:
        assert len(edits) == 3
        assert promoted == []
        assert manifest.read_bytes() == edits[-1]
        assert "installation cancelled" in caplog.text
    else:
        assert len(edits) == 1
        assert len(promoted) == 1
        data = json.loads(manifest.read_bytes())
        assert data["dependencies"] == {
            "game": "1.0", "external-0": "2.0", "com.coplaydev.unity-mcp": manager.unity_mcp_repo,
        }
        assert data["scopedRegistries"] == [{"name": "external"}]
    assert list(manifest.parent.iterdir()) == [manifest]
