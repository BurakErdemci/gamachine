"""run_mcp_server.sh must log to the user folder, never next to itself (signed app bundle)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash launcher")

LAUNCHER = Path(__file__).resolve().parents[1] / "run_mcp_server.sh"


def _run(tmp_path, extra_env=None):
    bundle = tmp_path / "Gamachine.app" / "Contents" / "Resources" / "Backend"
    bundle.mkdir(parents=True)
    shutil.copy(LAUNCHER, bundle / "run_mcp_server.sh")
    fake = bundle / "backend"
    fake.write_text("#!/bin/bash\necho fake-stderr >&2\n")
    fake.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "UNITYAI_MCP_LOG_FILE"}
    env["HOME"] = str(home)
    env.update(extra_env or {})
    subprocess.run(["bash", str(bundle / "run_mcp_server.sh")], env=env, timeout=10,
                   capture_output=True, cwd=str(tmp_path), check=True)
    return bundle, home


def test_default_log_is_in_user_folder(tmp_path):
    bundle, home = _run(tmp_path)
    assert not list(bundle.glob("*.log"))
    log = home / ".unity_architect_ai" / "mcp_server.log"
    assert "fake-stderr" in log.read_text()


def test_env_override_wins(tmp_path):
    custom = tmp_path / "elsewhere" / "custom.log"
    bundle, home = _run(tmp_path, {"UNITYAI_MCP_LOG_FILE": str(custom)})
    assert not list(bundle.glob("*.log"))
    assert "fake-stderr" in custom.read_text()
    assert not (home / ".unity_architect_ai").exists()
