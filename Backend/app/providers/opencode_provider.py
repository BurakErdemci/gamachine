import os
import re
import json
import uuid
import logging
import subprocess
from typing import Optional, Tuple
from .cli_base import BaseCLIProvider, build_spawn_env, env_family
from .oneshot_cli import resolve_opencode_cmd, split_model_id
from .workspace_config import ensure_gitignored, guvenli_config_yaz

logger = logging.getLogger(__name__)


# ── bash shadow ───────────────────────────────────────────────────────────
# OpenCode's Zen server refuses its free models (403 FreeTierError) unless
# the request's tool list holds bash, glob, grep and read; with
# permission.bash "deny" the built-in bash is hidden and 7/8 free models were
# refused (measured 26 Sep 2026, opencode 1.18.25). Exposing the built-in with
# "ask" is unsafe on Windows: it runs PowerShell, the permission scanner parses
# bash grammar, and a PowerShell-only command yields no pattern, so the check
# is skipped (tool/shell.ts ~282) and 5/11 shapes wrote files.
#
# Instead a custom tool named `bash` (file name = tool id) replaces the
# built-in for model turns: tool/registry.ts scans `{tool,tools}/*.{js,ts}` of
# every config dir, and session/tools.ts keeps the last tool of a name. It
# lives in a Gamachine-owned dir handed over as OPENCODE_CONFIG_DIR, whose
# opencode.json is merged after the workspace one (config/config.ts ~438),
# so "bash": "ask" exists only in a spawn that also loads the shadow.
# "deny" would hide the shadow too (Permission.disabled matches by name).
# Measured with real turns: 8/8 free models answered, the model's bash calls
# returned this text and left nothing on disk.
#
# A config dir makes OpenCode npm-install @opencode-ai/plugin into it
# (config/config.ts ~452) - a runtime download. core/npm.ts install() skips
# when node_modules exists and every declared name is in the lock root, so
# the skeleton below pre-places exactly that. The shadow imports nothing.
_BASH_SHADOW_TS = """\
// Written by Gamachine; rewritten on every OpenCode turn. It replaces
// OpenCode's built-in bash tool for Gamachine turns and runs nothing.
export default {
  description:
    "Terminal is disabled in Gamachine. Calling this tool runs nothing. " +
    "Use the unityai MCP tools (run_terminal_command, save_file, read_file) instead.",
  args: {
    command: { type: "string", description: "Ignored; nothing is executed." },
  },
  async execute() {
    return "Terminal is disabled in Gamachine; nothing was run. Use the unityai MCP tools (run_terminal_command) instead."
  },
}
"""

_SHADOW_CONFIG = json.dumps({
    "$schema": "https://opencode.ai/config.json",
    "permission": {"edit": "deny", "bash": "ask"},
    # A denied or headless-rejected call ends the turn without this
    # (session/processor.ts ~633). Kept out of the workspace file: an older
    # OpenCode that does not know the key would reject the whole config.
    "experimental": {"continue_loop_on_deny": True},
}, indent=2) + "\n"

_SHADOW_FILES: Tuple[Tuple[str, str], ...] = (
    ("tool/bash.ts", _BASH_SHADOW_TS),
    ("opencode.json", _SHADOW_CONFIG),
    ("package.json", json.dumps(
        {"private": True, "dependencies": {"@opencode-ai/plugin": "*"}}, indent=2) + "\n"),
    ("package-lock.json", json.dumps({
        "name": "gamachine-opencode-config", "lockfileVersion": 3, "requires": True,
        "packages": {"": {"dependencies": {"@opencode-ai/plugin": "*"}}},
    }, indent=2) + "\n"),
    ("node_modules/@opencode-ai/plugin/package.json", json.dumps(
        {"name": "@opencode-ai/plugin", "version": "0.0.0-gamachine-stub", "private": True},
        indent=2) + "\n"),
)

# The loader and last-writer-wins behaviour were read and measured on
# 1.18.25-1.18.32. Outside this range bash stays "deny": free models may be
# refused again, but the real terminal is never exposed by a changed loader.
_SHADOW_VERSION_MIN = (1, 18, 25)
_SHADOW_VERSION_BELOW = (1, 19, 0)
_version_cache: dict = {}


def bash_shadow_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".unity_architect_ai", "opencode-config")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        # A file replaced with non-UTF-8 bytes is simply "not ours".
        return None


def _write_text(path: str, body: str) -> None:
    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def install_bash_shadow(root: Optional[str] = None) -> Optional[str]:
    """Writes the shadow and its skeleton; returns the dir only when every
    file reads back exactly as shipped, else None (the caller keeps bash
    denied)."""
    root = root or bash_shadow_dir()
    _, keep_dirs = _shadow_paths(root)
    # Checked before writing: a junction here would redirect the writes into
    # its target (Codex ocshadow audit).
    linked = [d for d in keep_dirs if _is_link(d)]
    if linked:
        logger.warning("[OpenCodeProvider] %s is a link; bash stays denied.", linked[0])
        return None
    try:
        for rel, body in _SHADOW_FILES:
            path = os.path.join(root, *rel.split("/"))
            if _read_text(path) == body:
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _write_text(path, body)
    except OSError as e:
        logger.warning("[OpenCodeProvider] bash shadow not written (%s); bash stays denied.", e)
        return None
    for rel, body in _SHADOW_FILES:
        if _read_text(os.path.join(root, *rel.split("/"))) != body:
            logger.warning("[OpenCodeProvider] bash shadow %s did not read back; "
                           "bash stays denied.", rel)
            return None
    if not _prune_foreign(root):
        return None
    return root


# OpenCode writes this one itself into a config dir (measured).
_SHADOW_TOLERATED = {".gitignore"}


def _is_link(path: str) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return os.path.islink(path) or bool(isjunction and isjunction(path))


def _shadow_paths(root: str) -> Tuple[set, set]:
    """normcase'd (files, dirs) Gamachine ships into the shadow dir, root included."""
    keep = {os.path.normcase(os.path.join(root, *rel.split("/"))) for rel, _ in _SHADOW_FILES}
    keep |= {os.path.normcase(os.path.join(root, name)) for name in _SHADOW_TOLERATED}
    keep_dirs = {os.path.normcase(root)}
    for path in keep:
        parent = os.path.dirname(path)
        while len(parent) > len(os.path.normcase(root)):
            keep_dirs.add(parent)
            parent = os.path.dirname(parent)
    return keep, keep_dirs


def shadow_intact(root: str) -> bool:
    """Read-only recheck right before a spawn: every shipped file as shipped,
    nothing foreign, no links. Narrows the window between install and the
    OpenCode load in which another process could swap the tool."""
    keep, keep_dirs = _shadow_paths(root)
    if any(_is_link(d) for d in keep_dirs):
        return False
    for rel, body in _SHADOW_FILES:
        if _read_text(os.path.join(root, *rel.split("/"))) != body:
            return False
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames:
                if os.path.normcase(os.path.join(dirpath, name)) not in keep_dirs:
                    return False
            for name in filenames:
                if os.path.normcase(os.path.join(dirpath, name)) not in keep:
                    return False
    except OSError:
        return False
    return True


def _prune_foreign(root: str) -> bool:
    """Removes every entry of the shadow dir that Gamachine did not ship.

    OpenCode loads tool/, plugin/, agent/ ... from every config dir, and the
    dir is writable by other processes of this account (a Codex sandbox has
    modify rights on ~/.unity_architect_ai, measured with icacls), so a file
    dropped here would run in every OpenCode turn. False (bash stays denied)
    when something foreign cannot be removed.
    """
    import shutil

    keep, keep_dirs = _shadow_paths(root)
    if _is_link(root):
        logger.warning("[OpenCodeProvider] bash shadow dir is a link; bash stays denied.")
        return False
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            for name in list(dirnames):
                path = os.path.join(dirpath, name)
                if _is_link(path):
                    # Unlink only: rmtree through a junction deletes its target.
                    if os.path.normcase(path) in keep_dirs:
                        logger.warning("[OpenCodeProvider] %s is a link; bash stays denied.", path)
                        return False
                    os.rmdir(path) if os.path.isdir(path) and not os.path.islink(path) else os.unlink(path)
                    dirnames.remove(name)
                elif os.path.normcase(path) not in keep_dirs:
                    shutil.rmtree(path)
                    dirnames.remove(name)
            for name in filenames:
                path = os.path.join(dirpath, name)
                if os.path.normcase(path) not in keep:
                    os.remove(path)
    except OSError as e:
        logger.warning("[OpenCodeProvider] foreign file in bash shadow dir not removed "
                       "(%s); bash stays denied.", e)
        return False
    return True


def _opencode_version(base: list) -> Optional[tuple]:
    """(major, minor, patch) of the installed OpenCode, cached per binary."""
    try:
        st = os.stat(base[0])
        key = (base[0], st.st_mtime_ns, st.st_size)
    except OSError:
        key = (base[0], None, None)
    if key in _version_cache:
        return _version_cache[key]
    version = None
    try:
        out = subprocess.run(
            [*base, "--version"], capture_output=True, text=True, timeout=20,
            env=build_spawn_env(env_family("opencode")),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
        if m:
            version = tuple(int(x) for x in m.groups())
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("[OpenCodeProvider] opencode --version failed: %s", e)
    if version is not None:
        _version_cache[key] = version
    return version


def shadow_supported() -> bool:
    base = resolve_opencode_cmd()
    if not base:
        return False
    version = _opencode_version(base)
    return version is not None and _SHADOW_VERSION_MIN <= version < _SHADOW_VERSION_BELOW


def _without_grants(perm) -> dict:
    """A permission block minus every rule Gamachine sets itself.

    Rules are evaluated last-match-wins in key order (permission/index.ts
    evaluate/disabled use findLast), so the caller appends its own edit/bash
    rules AFTER what is left; a string form ("allow") means {"*": "allow"}.
    """
    if isinstance(perm, str):
        perm = {"*": perm}
    if not isinstance(perm, dict):
        return {}
    def _grants_all(k, v):
        return k == "*" and (v == "allow" or (isinstance(v, dict) and "allow" in v.values()))

    return {k: v for k, v in perm.items()
            if k not in ("edit", "bash") and not _grants_all(k, v)}


class OpenCodeProvider(BaseCLIProvider):
    """OpenCode — `opencode run` + resmi -s/--session resume.

    • Model: `opencode:<provider/model>` bizim ID'si → `-m provider/model`.
      OpenCode'un opencode/* modelleri auth'suz ÜCRETSİZ çalışır (canlı
      doğrulandı) → uygulamamıza sıfır-kurulum "bedava mod" kazandırır.
    • Çıktı: --format json → event akışı (text / tool_use / step_start /
      step_finish; sessionID her event'te) — cli_base'de işlenir.
    • İzinler: workspace opencode.json'da edit/bash "deny" → dosya/shell
      yalnız unityai MCP'den (onaylı) geçebilir. Kullanıcının mevcut
      opencode.json'ı varsa merge edilir, diğer ayarlarına dokunulmaz.
      Free models need a visible bash: a verified shadow (see
      _BASH_SHADOW_TS) turns it into "ask" for that spawn only.
    """

    resume_session_id = None   # önceki turun sessionID'si (-s)

    # CANLI ÖLÇÜLDÜ 2026-08-01 (yardım metninde YOK): mesaj argümanı boşken
    # `opencode run` prompt'u stdin'den okuyor.
    prompt_via_stdin = True

    def _build_cmd(self, prompt: str, thinking_level: str = "medium", workspace: str = None) -> list:
        base = resolve_opencode_cmd()
        if not base:
            return ["opencode-not-found", prompt]

        _, model = split_model_id(self.binary_name)

        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        unity_running = unity_mcp_manager.is_running()
        unity_section = ""
        if unity_running:
            unity_section = (
                "\nUNITY EDITOR — unityMCP tools (use for ALL Unity scene/UI operations):\n"
                "- Scene hierarchy:            unityMCP manage_scene action=get_hierarchy\n"
                "- Create/modify GameObjects:  unityMCP manage_gameobject\n"
                "- Components:                 unityMCP manage_components\n"
                "- UI (Canvas/Button/Text):    unityMCP manage_ui\n"
                "- Console logs:               unityMCP read_console\n"
                "RULE: NEVER read .unity/.prefab/.asset files to answer Unity questions —\n"
                "      always query the live editor via unityMCP.\n"
            )

        mcp_hint = (
            "IMPORTANT — follow exactly:\n"
            "- Respond in Turkish (Türkçe).\n"
            + unity_section +
            "\nFILE & TERMINAL operations — your built-in edit and bash tools are\n"
            "DENIED by workspace policy. Use ONLY the unityai MCP tools:\n"
            "- Create/edit files:  unityai save_file\n"
            "- Delete files:       unityai delete_file\n"
            "- Shell commands:     unityai run_terminal_command\n"
            "- Read file:          unityai read_file  |  List dir: unityai list_directory\n"
            "Be concise. Never claim you cannot do something — use the tools.\n\n"
        )

        cmd = [*base, "run", "--format", "json"]
        if model:
            cmd += ["-m", model]
        if self.resume_session_id:
            cmd += ["-s", self.resume_session_id]
        # Mesaj son pozisyonel arg (opencode.exe native → çok satırlı argv güvenli)
        yuk = mcp_hint + prompt
        if self.prompt_via_stdin:
            # `[message..]` pozisyoneli hiç verilmiyor; opencode boş kalınca
            # mesajı stdin'den okuyor. Bu yardım metninde YAZMIYOR — canlı
            # ölçüldü 2026-08-01 (`echo ... | opencode run` cevap döndürdü),
            # o yüzden opencode sürümü yükseltilirken bu tur tekrarlanmalı.
            self._stdin_payload = yuk
        else:
            cmd.append(yuk)
        return cmd

    def _write_mcp_config(self, workspace: str) -> str:
        # A shadow verified on an earlier turn of this instance must not
        # carry over if this turn never reaches _register_mcp.
        self._bash_shadow_dir = None
        return super()._write_mcp_config(workspace)

    def _turn_spawn_env(self) -> dict:
        # opencode 1.18.25 hands its whole env to local MCP children
        # (measured 26 Sep 2026), so the unityai bridge reads the token there.
        token = getattr(self, "_approval_turn_token", "")
        env = {"UNITYAI_APPROVAL_TURN_TOKEN": token} if token else {}
        shadow = getattr(self, "_bash_shadow_dir", None)
        if shadow and shadow_intact(shadow):
            env["OPENCODE_CONFIG_DIR"] = shadow
        elif shadow:
            logger.warning("[OpenCodeProvider] bash shadow changed after install; "
                           "this turn runs with bash denied.")
        return env

    def _prepare_bash_shadow(self) -> Optional[str]:
        if not shadow_supported():
            return None
        return install_bash_shadow()

    def _register_mcp(self, launcher: str, workspace: str, backend_url: str):
        """Workspace opencode.json'a unityai/unityMCP kaydı + izin politikası yazar."""
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

        self._bash_shadow_dir = None
        try:
            cfg_path = os.path.join(workspace, "opencode.json")
            unityai_env = {"UNITYAI_URL": backend_url, "WORKSPACE": workspace}
            # Token config dosyasına yazılmıyor — 0600 dosyadan okunuyor
            # (bkz. local_token_file). Bu dosya model tarafından okunabilir.
            # The per-turn approval token is NOT written here: this file is
            # shared by every chat of the workspace, so concurrent turns
            # overwrote each other's token. It rides in the process env
            # (_turn_spawn_env). Rewriting the entry also drops a stale token
            # an older build left in the file.

            mcp = {
                "unityai": {
                    "type": "local",
                    "command": [launcher, "--workspace", workspace],
                    "environment": unityai_env,
                    "enabled": True,
                }
            }
            # K3: unityMCP artık `headers` taşıyan bir `remote` kayıt DEĞİL,
            # stdio köprüsü. Sebep ölçülmüş bir sızıntıydı: `X-API-Key` bu
            # dosyaya düz metin yazılıyordu, dosya MODELİN kendi okuyabildiği
            # yerde duruyor ve 29 Tem'de gerçek bir projede git tarafından
            # İZLENİYOR bulundu. ACL sertleştirmek bunu çözmez — model zaten
            # aynı kullanıcı olarak koşuyor.
            #
            # Köprü sırrı kendi okuyor (`~/.unity-mcp/local-api-token`), yani
            # sır ne config'e ne argv'ye giriyor. URL sır TAŞIMIYOR ama yine de
            # `environment` ile veriliyor; `unityai` da aynı dosyada aynı
            # `type: "local"` şeklinde kayıtlı, yani desen bu dosyada zaten
            # kanıtlı.
            unity_mcp_url = unity_mcp_manager.mcp_url()
            if unity_mcp_url:
                from .codex_unitymcp_bridge import bridge_argv
                mcp["unityMCP"] = {
                    "type": "local",
                    "command": bridge_argv(),
                    "environment": {"UNITY_MCP_URL": unity_mcp_url},
                    "enabled": True,
                }

            existing = {}
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        existing = json.load(f) or {}
                except Exception:
                    existing = {}

            # Yazma/shell CLI içinde kapalı → unityai MCP (onaylı) tek yol.
            # This file never opens bash: "ask" comes only from the shadow
            # dir (see _SHADOW_CONFIG), so a spawn without the shadow keeps
            # "deny" even when another chat's turn wrote this file.
            permission = {**_without_grants(existing.get("permission", {})),
                          "edit": "deny", "bash": "deny"}
            merged = {
                "$schema": existing.get("$schema", "https://opencode.ai/config.json"),
                **existing,
                "mcp": {**existing.get("mcp", {}), **mcp},
                "permission": permission,
            }
            # An agent's own permission is merged after the top-level one
            # (agent/agent.ts ~293), so a user's agent.build.permission.bash
            # "allow" would override the deny above.
            for section in ("agent", "mode"):
                entries = merged.get(section)
                if isinstance(entries, dict):
                    merged[section] = {
                        name: ({**entry, "permission": _without_grants(entry["permission"])}
                               if isinstance(entry, dict) and "permission" in entry else entry)
                        for name, entry in entries.items()
                    }
            if not unity_mcp_manager.is_running():
                merged["mcp"].pop("unityMCP", None)

            # Reasoning effort: opencode CLI flag sunmaz — model options'a yazılır
            # (provider.<id>.models.<model>.options.reasoningEffort). auto → dokunma.
            try:
                from .oneshot_cli import split_model_id
                from .effort_caps import map_effort
                _, _mid = split_model_id(self.binary_name)
                _lvl = getattr(self, "_effort_level", "auto")
                _r = map_effort("subscription", f"opencode:{_mid or ''}", _lvl).get("opencode_reasoning")
                if _r and _mid and "/" in _mid:
                    _pid, _m = _mid.split("/", 1)
                    node = (merged.setdefault("provider", {})
                                  .setdefault(_pid, {})
                                  .setdefault("models", {})
                                  .setdefault(_m, {}))
                    node.setdefault("options", {})["reasoningEffort"] = _r
            except Exception:
                pass

            # ⚠️ Düz `open` DEĞİL: yol yönlendirmesine kapalı yazım (K4).
            # `sir_tasiyor` artık FALSE — K3'ten sonra bu dosya BİZİM sırrımızı
            # taşımıyor (unityMCP stdio köprüsüyle kayıtlı). Bayrağı True
            # bırakmak, sertleştirme başarısız olduğunda unityMCP'yi gereksiz
            # yere düşürürdü: korunacak bir sır yokken işlev kaybı.
            #
            # Sertleştirme yine de HER ZAMAN deneniyor (bkz. `guvenli_config_yaz`):
            # dosya kullanıcının KENDİ üçüncü-parti MCP kayıtlarını da taşıyor
            # ve `os.replace` kaynağın ACL'ini taşıdığı için, koşullu bir
            # sertleştirme önceki bir sertleştirmeyi geri alabiliyordu.
            if not guvenli_config_yaz(workspace, "opencode.json",
                                      json.dumps(merged, indent=2)):
                logger.error("[OpenCodeProvider] %s güvenli yazılamadı; "
                             "MCP kaydı UYGULANMADI.", cfg_path)
                return
            logger.info("[OpenCodeProvider] opencode.json yazıldı (unityMCP stdio köprüsü).")
            # Dosyayı yazan nokta girdisini de yazar (bkz. workspace_config).
            ensure_gitignored(workspace, ["opencode.json"])
            self._bash_shadow_dir = self._prepare_bash_shadow()
        except Exception as e:
            logger.warning(f"[OpenCodeProvider] opencode.json yazılamadı: {e}")
