"""Guards for the OpenCode bash shadow (free-model fix, 26 Sep 2026).

OpenCode's free models need a visible `bash` tool; the built-in one must never
be reachable, because on Windows its permission check is skipped for
PowerShell-only commands. Gamachine exposes a shadow `bash` that runs nothing,
and turns `bash` into "ask" only in a spawn that loads that shadow.

The effective-permission helper below mirrors OpenCode's merge order, read in
opencode 1.18.25 config/config.ts (~421 project files, ~438 config dirs, the
OPENCODE_CONFIG_DIR one last) and measured with `opencode debug config`.
"""
import json
import os
import shutil
import subprocess

import pytest

from providers.opencode_provider import _opencode_version as _real_opencode_version

MODEL = "opencode:opencode/ling-3.0-flash-fin-free"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    from unity_ai_mcp import unity_mcp_manager as um
    monkeypatch.setattr(um.unity_mcp_manager, "mcp_url", lambda: None)
    monkeypatch.setattr(um.unity_mcp_manager, "is_running", lambda: False)
    return home


@pytest.fixture
def shadow_root(tmp_path, monkeypatch):
    from providers import opencode_provider as op
    root = tmp_path / "shadow"
    monkeypatch.setattr(op, "bash_shadow_dir", lambda: str(root))
    monkeypatch.setattr(op, "resolve_opencode_cmd", lambda: ["opencode"])
    monkeypatch.setattr(op, "_opencode_version", lambda base: (1, 18, 25))
    return root


def _register(ws, existing=None, provider=None):
    from providers.opencode_provider import OpenCodeProvider
    ws.mkdir(exist_ok=True)
    if existing is not None:
        (ws / "opencode.json").write_text(json.dumps(existing), encoding="utf-8")
    p = provider or OpenCodeProvider(MODEL)
    p._register_mcp("unityai-launcher", str(ws), "http://localhost:8000")
    cfg = json.loads((ws / "opencode.json").read_text(encoding="utf-8"))
    return p, cfg


def _effective_permission(cfg, env):
    perm = dict(cfg["permission"])
    cfg_dir = env.get("OPENCODE_CONFIG_DIR")
    if cfg_dir:
        with open(os.path.join(cfg_dir, "opencode.json"), encoding="utf-8") as f:
            perm.update(json.load(f).get("permission", {}))
    return perm


def _grants(node):
    """Every (key, value) pair in a JSON tree that opens bash/edit or '*'."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("bash", "edit", "*") and (v == "allow" or (
                    isinstance(v, dict) and "allow" in v.values())):
                found.append((k, v))
            found += _grants(v)
    elif isinstance(node, list):
        for item in node:
            found += _grants(item)
    return found


EXISTING = [
    None,
    {"permission": {"bash": "allow", "*": "allow"}},
    {"permission": "allow"},
    {"permission": {"*": {"*": "allow"}}},
    {"permission": {"edit": "allow", "webfetch": "allow"}},
    {"agent": {"build": {"permission": {"bash": "allow", "edit": "allow", "*": "allow"}}},
     "mode": {"plan": {"permission": {"bash": {"*": "allow"}}}}},
]


# ── (1) generated config ─────────────────────────────────────────────────


@pytest.mark.parametrize("existing", EXISTING)
@pytest.mark.parametrize("shadow", [False, True])
def test_generated_config_never_grants_bash_or_edit(tmp_path, monkeypatch, existing, shadow):
    if shadow:
        from providers import opencode_provider as op
        monkeypatch.setattr(op, "bash_shadow_dir", lambda: str(tmp_path / "shadow"))
        monkeypatch.setattr(op, "resolve_opencode_cmd", lambda: ["opencode"])
        monkeypatch.setattr(op, "_opencode_version", lambda base: (1, 18, 32))
    p, cfg = _register(tmp_path / "ws", existing)

    perm = cfg["permission"]
    # Last-match-wins in key order: Gamachine's rules must come last.
    assert list(perm)[-2:] == ["edit", "bash"]
    assert perm["edit"] == "deny" and perm["bash"] == "deny"
    assert _grants(cfg) == [], cfg
    for section in ("agent", "mode"):
        for entry in (cfg.get(section) or {}).values():
            assert "bash" not in entry.get("permission", {})
            assert "edit" not in entry.get("permission", {})

    env = p._turn_spawn_env()
    if shadow:
        with open(os.path.join(env["OPENCODE_CONFIG_DIR"], "opencode.json"), encoding="utf-8") as f:
            assert _grants(json.load(f)) == []
    effective = _effective_permission(cfg, env)
    assert effective["edit"] == "deny"
    assert effective["bash"] == ("ask" if shadow else "deny")


def test_bash_is_ask_only_with_the_verified_shadow(tmp_path, shadow_root):
    p, cfg = _register(tmp_path / "ws")
    env = p._turn_spawn_env()
    assert env["OPENCODE_CONFIG_DIR"] == str(shadow_root)
    assert (shadow_root / "tool" / "bash.ts").is_file()
    assert _effective_permission(cfg, env)["bash"] == "ask"
    # The workspace file itself never opens bash, so a concurrent spawn
    # without the shadow reads "deny" from it.
    assert cfg["permission"]["bash"] == "deny"


def test_write_failure_keeps_bash_denied(tmp_path, shadow_root, monkeypatch):
    from providers import opencode_provider as op

    def refuse(path, body):
        raise OSError(13, "simulated write failure")

    monkeypatch.setattr(op, "_write_text", refuse)
    p, cfg = _register(tmp_path / "ws")
    env = p._turn_spawn_env()
    assert "OPENCODE_CONFIG_DIR" not in env
    assert _effective_permission(cfg, env)["bash"] == "deny"
    assert not (shadow_root / "tool" / "bash.ts").exists()


def test_tampered_shadow_that_does_not_read_back_keeps_bash_denied(tmp_path, shadow_root, monkeypatch):
    from providers import opencode_provider as op
    tool = shadow_root / "tool"
    tool.mkdir(parents=True)
    (tool / "bash.ts").write_text("export default {}", encoding="utf-8")
    # A write that reports success but does not land.
    monkeypatch.setattr(op, "_write_text", lambda path, body: None)
    p, cfg = _register(tmp_path / "ws")
    assert "OPENCODE_CONFIG_DIR" not in p._turn_spawn_env()
    assert _effective_permission(cfg, p._turn_spawn_env())["bash"] == "deny"


def test_a_later_turn_without_the_shadow_drops_it(tmp_path, shadow_root, monkeypatch):
    from providers import opencode_provider as op
    p, _ = _register(tmp_path / "ws")
    assert "OPENCODE_CONFIG_DIR" in p._turn_spawn_env()
    monkeypatch.setattr(op, "_opencode_version", lambda base: (1, 19, 0))
    _register(tmp_path / "ws", provider=p)
    assert "OPENCODE_CONFIG_DIR" not in p._turn_spawn_env()


def test_turn_that_never_reaches_register_mcp_carries_no_stale_shadow(tmp_path, shadow_root, monkeypatch):
    from providers.cli_base import BaseCLIProvider
    p, _ = _register(tmp_path / "ws")
    assert "OPENCODE_CONFIG_DIR" in p._turn_spawn_env()
    # The base writer returns before calling _register_mcp (e.g. .mcp.json failed).
    monkeypatch.setattr(BaseCLIProvider, "_write_mcp_config", lambda self, ws: "")
    p._write_mcp_config(str(tmp_path / "ws"))
    assert "OPENCODE_CONFIG_DIR" not in p._turn_spawn_env()


@pytest.mark.parametrize("version,ok", [
    ((1, 18, 25), True), ((1, 18, 32), True), ((1, 18, 24), False),
    ((1, 19, 0), False), ((2, 0, 0), False), (None, False),
])
def test_shadow_only_on_a_measured_opencode_version(monkeypatch, version, ok):
    from providers import opencode_provider as op
    monkeypatch.setattr(op, "resolve_opencode_cmd", lambda: ["opencode"])
    monkeypatch.setattr(op, "_opencode_version", lambda base: version)
    assert op.shadow_supported() is ok


def test_no_opencode_binary_means_no_shadow(monkeypatch):
    from providers import opencode_provider as op
    monkeypatch.setattr(op, "resolve_opencode_cmd", lambda: None)
    assert op.shadow_supported() is False


def test_version_probe_parses_and_caches(tmp_path, monkeypatch):
    from providers import opencode_provider as op
    exe = tmp_path / "opencode.exe"
    exe.write_bytes(b"x")
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="1.18.25\n", stderr="")

    monkeypatch.setattr(op.subprocess, "run", fake_run)
    monkeypatch.setattr(op, "_version_cache", {})
    assert _real_opencode_version([str(exe)]) == (1, 18, 25)
    assert _real_opencode_version([str(exe)]) == (1, 18, 25)
    assert calls == [[str(exe), "--version"]]


# ── (2) spawn command ────────────────────────────────────────────────────


BANNED_FLAGS = ("--auto", "--yolo", "--dangerously-skip-permissions")


@pytest.mark.parametrize("unity_running", [False, True])
def test_spawn_command_never_skips_permissions(monkeypatch, shadow_root, unity_running, tmp_path):
    from providers import opencode_provider as op
    from unity_ai_mcp import unity_mcp_manager as um
    monkeypatch.setattr(um.unity_mcp_manager, "is_running", lambda: unity_running)
    p = op.OpenCodeProvider(MODEL)
    p.resume_session_id = "ses_abc"
    p._approval_turn_token = "tok"
    _register(tmp_path / "ws", provider=p)
    cmd = p._build_cmd("prompt", workspace=str(tmp_path / "ws"))

    flags = [a for a in cmd[1:] if a.startswith("-")]
    assert set(flags) <= {"--format", "-m", "-s"}, cmd
    for banned in BANNED_FLAGS:
        assert not any(a == banned or a.startswith(banned + "=") for a in cmd)
    env = p._turn_spawn_env()
    assert set(env) <= {"UNITYAI_APPROVAL_TURN_TOKEN", "OPENCODE_CONFIG_DIR"}


def test_provider_source_names_no_permission_skipping_flag():
    import providers.opencode_provider as op
    with open(op.__file__, encoding="utf-8") as f:
        source = f.read()
    for banned in BANNED_FLAGS + ("OPENCODE_PERMISSION", "OPENCODE_CONFIG_CONTENT"):
        assert banned not in source


# ── (3) shadow content ───────────────────────────────────────────────────


def test_shadow_source_imports_nothing_and_executes_nothing():
    from providers.opencode_provider import _BASH_SHADOW_TS as src
    for needle in ("import", "require(", "child_process", "Bun.", "spawn", "exec(",
                   "Deno.", "process.", "fetch(", "eval(", "Function(", "$`"):
        assert needle not in src, needle
    assert "export default {" in src
    assert 'return "Terminal is disabled in Gamachine' in src


def test_shadow_module_returns_fixed_text_when_run(tmp_path, shadow_root):
    """Loads the written file with Node the way OpenCode's registry does
    (default export with description/args/execute) and calls execute."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    from providers.opencode_provider import install_bash_shadow
    root = install_bash_shadow()
    mod = tmp_path / "bash.mjs"
    shutil.copyfile(os.path.join(root, "tool", "bash.ts"), mod)
    work = tmp_path / "work"
    work.mkdir()
    script = (
        f"const m = await import({json.dumps(mod.as_uri())});"
        "const d = m.default;"
        "const out = await d.execute({command: 'echo pwned > marker.txt'}, {});"
        "console.log(JSON.stringify({keys: Object.keys(m), fields: Object.keys(d).sort(), out}));"
    )
    res = subprocess.run([node, "--input-type=module", "-e", script], cwd=work,
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["keys"] == ["default"]
    assert data["fields"] == ["args", "description", "execute"]
    assert data["out"].startswith("Terminal is disabled in Gamachine")
    assert os.listdir(work) == []


# ── (4) no runtime download ──────────────────────────────────────────────


def _npm_would_install(d):
    """Mirror of opencode 1.18.25 packages/core/src/npm.ts install() (~146-189):
    reify when node_modules is missing, or when a declared name (package.json
    dependency kinds + the added @opencode-ai/plugin) is not in the lock root."""
    if not os.path.isdir(os.path.join(d, "node_modules")):
        return True

    def load(name):
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    kinds = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
    pkg, lock = load("package.json"), load("package-lock.json")
    declared = {n for k in kinds for n in (pkg.get(k) or {})} | {"@opencode-ai/plugin"}
    root = (lock.get("packages") or {}).get("") or {}
    locked = {n for k in kinds for n in (root.get(k) or {})}
    return not declared <= locked


def test_skeleton_blocks_the_plugin_install(shadow_root):
    from providers.opencode_provider import install_bash_shadow
    assert _npm_would_install(str(shadow_root)) is True  # the mirror detects a bare dir
    root = install_bash_shadow()
    assert root == str(shadow_root)
    assert (shadow_root / "node_modules" / "@opencode-ai" / "plugin" / "package.json").is_file()
    assert _npm_would_install(root) is False


def test_install_is_idempotent(shadow_root):
    from providers.opencode_provider import install_bash_shadow, _SHADOW_FILES
    install_bash_shadow()
    stamps = {rel: os.stat(os.path.join(shadow_root, *rel.split("/"))).st_mtime_ns
              for rel, _ in _SHADOW_FILES}
    assert install_bash_shadow() == str(shadow_root)
    for rel, _ in _SHADOW_FILES:
        assert os.stat(os.path.join(shadow_root, *rel.split("/"))).st_mtime_ns == stamps[rel]
    assert not [n for n in os.listdir(shadow_root / "tool") if n.endswith(".tmp")]


def test_foreign_tools_and_plugins_are_removed(shadow_root):
    """Every config dir's tool/ and plugin/ run in each turn; anything
    Gamachine did not ship must not survive an install."""
    from providers.opencode_provider import install_bash_shadow
    install_bash_shadow()
    (shadow_root / "tool" / "evil.ts").write_text("export default {}", encoding="utf-8")
    (shadow_root / "plugin").mkdir()
    (shadow_root / "plugin" / "p.js").write_text("x", encoding="utf-8")
    (shadow_root / "node_modules" / "other").mkdir()
    (shadow_root / ".gitignore").write_text("node_modules\n", encoding="utf-8")
    assert install_bash_shadow() == str(shadow_root)
    assert os.listdir(shadow_root / "tool") == ["bash.ts"]
    assert not (shadow_root / "plugin").exists()
    assert os.listdir(shadow_root / "node_modules") == ["@opencode-ai"]
    assert (shadow_root / ".gitignore").exists()


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
def test_foreign_junction_is_unlinked_without_touching_its_target(shadow_root, tmp_path):
    import _winapi
    from providers.opencode_provider import install_bash_shadow
    install_bash_shadow()
    target = tmp_path / "precious"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    _winapi.CreateJunction(str(target), str(shadow_root / "plugin"))
    assert install_bash_shadow() == str(shadow_root)
    assert not (shadow_root / "plugin").exists()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
def test_a_kept_dir_turned_into_a_junction_keeps_bash_denied(shadow_root, tmp_path):
    import _winapi
    from providers.opencode_provider import install_bash_shadow, _BASH_SHADOW_TS
    install_bash_shadow()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "bash.ts").write_text(_BASH_SHADOW_TS, encoding="utf-8", newline="")
    (elsewhere / "evil.ts").write_text("export default {}", encoding="utf-8")
    shutil.rmtree(shadow_root / "tool")
    _winapi.CreateJunction(str(elsewhere), str(shadow_root / "tool"))
    assert install_bash_shadow() is None
    assert (elsewhere / "evil.ts").exists()


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
@pytest.mark.parametrize("linked", ["", "tool"])
def test_a_linked_shadow_dir_is_refused_before_any_write(shadow_root, tmp_path, linked):
    """Codex ocshadow: the link check ran after the writes had gone through
    the junction into its target."""
    import _winapi
    from providers.opencode_provider import install_bash_shadow
    target = tmp_path / "target"
    target.mkdir()
    if linked:
        shadow_root.mkdir()
        _winapi.CreateJunction(str(target), str(shadow_root / linked))
    else:
        _winapi.CreateJunction(str(target), str(shadow_root))
    assert install_bash_shadow() is None
    assert os.listdir(target) == []


@pytest.mark.parametrize("tamper", ["edit", "foreign"])
def test_spawn_env_drops_a_shadow_changed_after_install(tmp_path, shadow_root, tamper):
    """Codex ocshadow: the dir was verified at registration only."""
    p, _ = _register(tmp_path / "ws")
    assert p._turn_spawn_env().get("OPENCODE_CONFIG_DIR") == str(shadow_root)
    if tamper == "edit":
        (shadow_root / "tool" / "bash.ts").write_text("export default {}", encoding="utf-8")
    else:
        (shadow_root / "tool" / "run.ts").write_text("export default {}", encoding="utf-8")
    assert "OPENCODE_CONFIG_DIR" not in p._turn_spawn_env()


def test_non_utf8_shadow_file_denies_bash_instead_of_raising(tmp_path, shadow_root):
    """Codex ocverify: UnicodeDecodeError escaped and failed the turn."""
    p, _ = _register(tmp_path / "ws")
    (shadow_root / "tool" / "bash.ts").write_bytes(b"\xff\xfe\xff")
    assert "OPENCODE_CONFIG_DIR" not in p._turn_spawn_env()
    from providers.opencode_provider import install_bash_shadow
    assert install_bash_shadow() == str(shadow_root)


def test_an_unreadable_dir_during_the_scan_keeps_bash_denied(tmp_path, shadow_root, monkeypatch):
    """Codex ocverify2: os.walk skipped a failing scandir silently, so a
    foreign tool passed both the prune and the spawn recheck."""
    p, _ = _register(tmp_path / "ws")
    (shadow_root / "tool" / "foreign.ts").write_text("export default {}", encoding="utf-8")
    real_scandir = os.scandir

    def failing_scandir(path=".", *a, **kw):
        if os.path.normcase(os.fspath(path)) == os.path.normcase(str(shadow_root / "tool")):
            raise PermissionError("denied")
        return real_scandir(path, *a, **kw)

    monkeypatch.setattr(os, "scandir", failing_scandir)
    assert "OPENCODE_CONFIG_DIR" not in p._turn_spawn_env()
    from providers.opencode_provider import install_bash_shadow
    assert install_bash_shadow() is None
