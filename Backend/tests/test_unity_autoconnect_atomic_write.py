import builtins

import pytest

from unity_ai_mcp import unity_mcp_manager as module


@pytest.mark.parametrize("failure", ["write", "flush", "fsync", "replace", "interrupt", None])
def test_autoconnect_write_preserves_original_and_cleans_stage(tmp_path, monkeypatch, failure):
    script = tmp_path / "Assets" / "Editor" / "UnityArchitectAIMCPSetup.cs"
    script.parent.mkdir(parents=True)
    original = b"// usable original setup\r\n"
    script.write_bytes(original)
    manager = module.UnityMCPManager()
    monkeypatch.setattr(manager, "_get_uvx", lambda: "uvx")
    real_open = builtins.open

    class Interrupted(BaseException):
        pass

    class FailingWriter:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def write(self, text):
            if failure == "write":
                self.handle.write(text[:20])
                raise OSError("disk full")
            return self.handle.write(text)

        def flush(self):
            if failure == "flush":
                raise OSError("flush failed")
            return self.handle.flush()

    def open_with_failure(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        return FailingWriter(handle) if "w" in mode else handle

    def denied(*args):
        if failure == "interrupt":
            raise Interrupted()
        raise OSError("denied")

    monkeypatch.setattr(builtins, "open", open_with_failure)
    if failure == "fsync":
        monkeypatch.setattr(module.os, "fsync", denied)
    elif failure in {"replace", "interrupt"}:
        monkeypatch.setattr(module.os, "replace", denied)
    if failure:
        with pytest.raises(Interrupted if failure == "interrupt" else OSError):
            manager._write_autoconnect_script(str(tmp_path), auto_start=True)
        assert script.read_bytes() == original
        assert str(tmp_path) not in manager._autoconnect_workspaces
    else:
        manager._write_autoconnect_script(str(tmp_path), auto_start=True)
        assert '"MCPForUnity.AutoStartOnLoad", true' in script.read_text(encoding="utf-8")
        assert str(tmp_path) in manager._autoconnect_workspaces
        manager._write_autoconnect_script(str(tmp_path), auto_start=False)
        assert '"MCPForUnity.AutoStartOnLoad", false' in script.read_text(encoding="utf-8")
        assert str(tmp_path) not in manager._autoconnect_workspaces
    assert list(script.parent.iterdir()) == [script]


def test_stop_server_keeps_workspace_when_autoconnect_off_write_fails(tmp_path, monkeypatch):
    script = tmp_path / "Assets" / "Editor" / "UnityArchitectAIMCPSetup.cs"
    manager = module.UnityMCPManager()
    monkeypatch.setattr(manager, "_get_uvx", lambda: "uvx")
    manager._write_autoconnect_script(str(tmp_path), auto_start=True)
    workspace = str(tmp_path)
    assert workspace in manager._autoconnect_workspaces

    real_replace = module.os.replace

    def denied(*args, **kwargs):
        raise PermissionError("sharing violation")

    monkeypatch.setattr(module.os, "replace", denied)
    manager.stop_server()
    assert workspace in manager._autoconnect_workspaces
    assert '"MCPForUnity.AutoStartOnLoad", true' in script.read_text(encoding="utf-8")

    monkeypatch.setattr(module.os, "replace", real_replace)
    manager.stop_server()
    assert workspace not in manager._autoconnect_workspaces
    assert '"MCPForUnity.AutoStartOnLoad", false' in script.read_text(encoding="utf-8")


def test_stop_server_resets_readable_workspaces_after_first_read_fails(tmp_path, monkeypatch):
    manager = module.UnityMCPManager()
    monkeypatch.setattr(manager, "_get_uvx", lambda: "uvx")
    monkeypatch.setattr(manager, "is_running", lambda: False)
    scripts = {}
    for index in range(3):
        workspace = tmp_path / f"workspace-{index}"
        manager._write_autoconnect_script(str(workspace), auto_start=True)
        scripts[str(workspace)] = workspace / "Assets" / "Editor" / "UnityArchitectAIMCPSetup.cs"

    unreadable_workspace = next(iter(manager._autoconnect_workspaces))
    unreadable_script = scripts[unreadable_workspace]
    real_open = builtins.open

    def open_with_failure(path, mode="r", *args, **kwargs):
        if module.os.fspath(path) == str(unreadable_script) and mode == "r":
            raise PermissionError("script cannot be read")
        return real_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module, "open", open_with_failure, raising=False)
        manager.stop_server()

    assert manager._autoconnect_workspaces == {unreadable_workspace}
    for workspace, script in scripts.items():
        expected = "true" if workspace == unreadable_workspace else "false"
        assert f'"MCPForUnity.AutoStartOnLoad", {expected}' in script.read_text(encoding="utf-8")


def test_stop_server_removes_workspace_with_missing_script(tmp_path, monkeypatch):
    manager = module.UnityMCPManager()
    monkeypatch.setattr(manager, "is_running", lambda: False)
    workspace = str(tmp_path)
    script = tmp_path / "Assets" / "Editor" / "UnityArchitectAIMCPSetup.cs"
    assert tmp_path.is_dir()
    assert not script.exists()
    manager._autoconnect_workspaces.add(workspace)

    manager.stop_server()

    assert workspace not in manager._autoconnect_workspaces


def test_stop_server_removes_workspace_with_missing_directory(tmp_path, monkeypatch):
    manager = module.UnityMCPManager()
    monkeypatch.setattr(manager, "is_running", lambda: False)
    workspace = tmp_path / "missing-workspace"
    assert not workspace.exists()
    manager._autoconnect_workspaces.add(str(workspace))

    manager.stop_server()

    assert str(workspace) not in manager._autoconnect_workspaces
