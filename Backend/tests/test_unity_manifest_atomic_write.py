import json

import pytest

from unity_ai_mcp import unity_mcp_manager as module

OURS = "file:C:\\Users\\someone\\Downloads\\Gamachine\\unity-mcp\\MCPForUnity"


def prepared(tmp_path, monkeypatch, text):
    manifest = tmp_path / "Packages" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_bytes(text.encode("utf-8"))
    manager = module.UnityMCPManager()
    monkeypatch.setattr(manager, "_ensure_writable_resources", lambda: None)
    monkeypatch.setattr(manager, "_get_uvx", lambda: "uvx")
    # The package copy has its own tests (test_unity_out_of_git.py); here only the manifest is under test.
    monkeypatch.setattr(manager, "_ensure_package_copy", lambda workspace: True)
    return manifest, manager


@pytest.mark.parametrize("failure", ["serialize", "fsync", "replace", None])
def test_manifest_migration_preserves_original_on_failure(tmp_path, monkeypatch, failure):
    original = json.dumps({"dependencies": {"com.example.game": "1.2.3", "com.coplaydev.unity-mcp": OURS},
                           "scopedRegistries": []})
    manifest, manager = prepared(tmp_path, monkeypatch, original)

    def interrupted_dumps(data, **kwargs):
        raise OSError("disk full")

    def denied(*args):
        raise OSError("denied")

    if failure == "serialize":
        monkeypatch.setattr(module.json, "dumps", interrupted_dumps)
    elif failure == "fsync":
        monkeypatch.setattr(module.os, "fsync", denied)
    elif failure == "replace":
        monkeypatch.setattr(module.os, "replace", denied)
    assert manager.install_package(str(tmp_path), write_autoconnect=False) is (failure is None)
    if failure:
        assert manifest.read_bytes() == original.encode("utf-8")
    else:
        data = json.loads(manifest.read_bytes())
        assert data["dependencies"] == {"com.example.game": "1.2.3"}
        assert data["scopedRegistries"] == []
    assert list(manifest.parent.iterdir()) == [manifest]


@pytest.mark.parametrize("continuous", [False, True])
@pytest.mark.parametrize("edit_at", ["serialize", "fsync"])
def test_concurrent_manifest_changes_are_merged_or_rejected(tmp_path, monkeypatch, caplog, continuous, edit_at):
    manifest, manager = prepared(
        tmp_path, monkeypatch, json.dumps({"dependencies": {"game": "1.0", "com.coplaydev.unity-mcp": OURS}}))
    dumps = module.json.dumps
    fsync = module.os.fsync
    edits = []
    promoted = []
    replace = module.os.replace

    def concurrent_edit():
        if not edits or continuous:
            data = json.loads(manifest.read_bytes())
            data["dependencies"][f"external-{len(edits)}"] = "2.0"
            data["scopedRegistries"] = [{"name": "external"}]
            manifest.write_text(dumps(data), encoding="utf-8")
            edits.append(manifest.read_bytes())

    def serialize(data, **kwargs):
        if edit_at == "serialize" and "dependencies" in data:
            concurrent_edit()
        return dumps(data, **kwargs)

    def sync(descriptor):
        if edit_at == "fsync" and not promoted:
            concurrent_edit()
        return fsync(descriptor)

    def promote(source, target):
        promoted.append(target)
        return replace(source, target)

    monkeypatch.setattr(module.json, "dumps", serialize)
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
        assert promoted.count(str(manifest)) == 1
        data = json.loads(manifest.read_bytes())
        assert data["dependencies"] == {"game": "1.0", "external-0": "2.0"}
        assert data["scopedRegistries"] == [{"name": "external"}]
    assert list(manifest.parent.iterdir()) == [manifest]


def test_manifest_without_our_entry_is_never_rewritten(tmp_path, monkeypatch):
    original = '{\n  "dependencies": {\n    "com.example.game": "1.2.3"\n  }\n}\n'
    manifest, manager = prepared(tmp_path, monkeypatch, original)
    before = manifest.stat().st_mtime_ns
    assert manager.install_package(str(tmp_path), write_autoconnect=False) is True
    assert manifest.read_bytes() == original.encode("utf-8")
    assert manifest.stat().st_mtime_ns == before
