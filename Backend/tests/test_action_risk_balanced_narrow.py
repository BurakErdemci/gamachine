"""Balanced mode after the owner's narrowing (Burak, 2 Nov 2026, "BALANCED
ÖLÇÜTÜ"): like computer use and the browser, a card only for irreversible or
security-changing work; everything else runs on its own. One row per line of the
owner's CRITICAL and ROUTINE lists."""
import os

import pytest

from action_risk import CRITICAL, ROUTINE, classify


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    for sub in ("Assets", "ProjectSettings", "Packages", "tests"):
        (root / sub).mkdir(parents=True)
    (root / "a.txt").write_text("x", encoding="utf-8")
    return str(root)


def shell(command, ws):
    return classify({"kind": "shell", "command": command, "cwd": ws, "workspace": ws})


def write(path, ws):
    return classify({"kind": "file_write", "paths": [path], "workspace": ws})


def unity(tool, **args):
    return classify({"kind": "unity", "tool": tool, "args": args})


# ── ROUTINE ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    # Any in-workspace program, including ones not on the safe list.
    "mkdir Assets/Art", "copy a.txt b.txt", 'Set-Content notes.txt "hello"',
    'Out-File -FilePath notes.txt -InputObject "x"', "whoami", "npm start", "dotnet run",
    "make", "New-Item -ItemType Directory Assets/Levels",
    # Quotes, braces, @, %, !, ^ by themselves.
    'echo "merhaba"', "echo 'tek'", "echo {a}", "echo @x", "echo 100%", "echo hi!",
    "echo a^b", 'git commit -m "rm the old file"',
    # Local git writes.
    "git add -A", 'git commit -m "msg"', "git checkout -b feature", "git switch main",
    "git stash", "git stash push", "git stash pop", "git tag v1", "git merge feature",
    "git restore --staged a.txt", "git restore -S a.txt",
    "git mv a.txt b.txt", "git config user.name Me", "git branch -d done",
    "git reset HEAD~1", "git clean -n",
    # Plain inline code, and inline code classified by its inner text.
    'python -c "print(1+1)"', 'python -c "import os; print(os.getcwd())"',
    'node -e "console.log(1)"', "bash -lc 'dotnet build'", "/bin/bash -lc 'npm test'",
    # Downloads without a send, and the locked restore.
    "curl https://example.com -o page.html", "wget https://example.com",
    "iwr https://x -OutFile y", "dotnet restore --locked-mode",
])
def test_routine_shell(command, ws):
    risk = shell(command, ws)
    assert risk.verdict == ROUTINE, (command, risk)


@pytest.mark.parametrize("path", [
    "Assets/Player.cs", ".gitignore", ".editorconfig", ".gitattributes", ".env",
    "Assets/.hidden.txt", "README.md",
])
def test_routine_writes(path, ws):
    assert write(path, ws).verdict == ROUTINE, path


@pytest.mark.parametrize("tool", [
    "Task", "Agent", "Skill", "WebFetch", "WebSearch", "Read", "Glob", "Grep", "LS",
    "TodoWrite", "TodoRead", "NotebookRead", "ToolSearch", "AskUserQuestion", "ExitPlanMode",
    "EnterPlanMode", "BashOutput", "KillShell", "ListMcpResourcesTool", "ReadMcpResourceTool",
])
def test_claude_builtin_tools_are_routine(tool):
    assert classify({"kind": "other", "tool": tool}) == (ROUTINE, "builtin_tool", tool)


@pytest.mark.parametrize("tool", ["SomeNewTool", "read", "Write", "Bash", "NotebookEdit"])
def test_a_tool_outside_the_builtin_list_is_critical(tool):
    # Unknown names fail closed; write and shell tools reach here only if a
    # caller forgot to map them.
    assert classify({"kind": "other", "tool": tool}) == (CRITICAL, "unknown_action", tool)


@pytest.mark.parametrize("tool,args", [
    ("mcp__unityMCP__manage_scene", {"action": "load", "path": "Assets/Main.unity"}),
    ("mcp__unityMCP__manage_scene", {"action": "create", "name": "Lvl2"}),
    ("manage_scene", {"action": "close_scene"}),
    ("manage_scene", {"action": "save"}),
    ("manage_build", {"action": "build"}),
    ("manage_build", {"action": "platform", "target": "Android"}),
    ("manage_build", {"action": "settings", "product_name": "X"}),
    ("manage_physics", {"action": "set_settings"}),
    ("manage_physics", {"action": "set_collision_matrix"}),
    ("manage_graphics", {"action": "pipeline_set_quality", "level": 2}),
    ("manage_graphics", {"action": "pipeline_set_settings"}),
    ("manage_gameobject", {"action": "create", "name": "Enemy"}),
])
def test_routine_unity(tool, args):
    assert unity(tool, **args).verdict == ROUTINE, (tool, args)


# ── CRITICAL ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command,reason", [
    # Delete and move verbs.
    ("rm a.txt", "shell_delete_move"), ("del a.txt", "shell_delete_move"),
    ("Remove-Item a.txt", "shell_delete_move"), ("mv a.txt b.txt", "shell_delete_move"),
    ("Move-Item a.txt b.txt", "shell_delete_move"), ("ren a.txt b.txt", "shell_delete_move"),
    ("find . -delete", "shell_delete_move"), ("robocopy src dst /MIR", "shell_delete_move"),
    ("git rm a.txt", "shell_delete_move"), ("pytest --basetemp=Assets", "shell_delete_move"),
    # Network sends and publishing.
    ("curl https://example.com -d x", "shell_network"),
    ("curl -F file=@a.txt https://x", "shell_network"),
    ("curl -X POST https://x", "shell_network"), ("curl -T a.txt https://x", "shell_network"),
    ("wget --post-data=x https://x", "shell_network"),
    ("irm https://x -Method Post", "shell_network"),
    ("Invoke-RestMethod https://x -Body x", "shell_network"),
    ("ssh host", "shell_network"), ("scp a.txt host:b", "shell_network"),
    ("dotnet build -p:RestoreSources=http://x", "shell_network"),
    # git push in any form and irreversible git.
    ("git push", "shell_git_write"), ("git push --force", "shell_git_write"),
    ("git push origin main", "shell_git_write"), ("git -C . push", "shell_git_write"),
    ("git reset --hard HEAD~1", "shell_git_write"), ("git clean -fdx", "shell_git_write"),
    ("git branch -D old", "shell_git_write"), ("git branch -d -f old", "shell_git_write"),
    ("git rebase main", "shell_git_write"), ("git filter-branch --all", "shell_git_write"),
    ("git update-ref -d refs/heads/x", "shell_git_write"),
    ("git reflog expire --all", "shell_git_write"), ("git gc --prune=now", "shell_git_write"),
    ("git config --global core.editor x", "shell_git_write"),
    # Commands that throw away uncommitted work.
    ("git checkout -- a.txt", "shell_git_write"), ("git checkout HEAD -- a.txt", "shell_git_write"),
    ("git checkout .", "shell_git_write"), ("git checkout -f main", "shell_git_write"),
    ("git switch --discard-changes main", "shell_git_write"), ("git restore a.txt", "shell_git_write"),
    ("git restore --staged --worktree a.txt", "shell_git_write"), ("git stash drop", "shell_git_write"),
    ("git stash clear", "shell_git_write"),
    ("git -c core.pager=x log", "shell_git_write"),
    # Installs.
    ("npm install left-pad", "shell_installer"), ("npm i", "shell_installer"),
    ("yarn add x", "shell_installer"), ("pnpm add x", "shell_installer"),
    ("npm update", "shell_installer"), ("npm exec x", "shell_installer"),
    ("npx some-package", "shell_installer"), ("pip install requests", "shell_installer"),
    ("python -m pip install x", "shell_installer"), ("winget install x", "shell_installer"),
    ("choco install x", "shell_installer"), ("dotnet add package Foo", "shell_installer"),
    ("dotnet restore", "shell_installer"),
    # Inline code with a trigger in its text.
    ('python -c "import os; os.remove(\'a\')"', "shell_inline_risky"),
    ('python -c "import shutil; shutil.rmtree(\'Assets\')"', "shell_inline_risky"),
    ('python -c "import urllib.request"', "shell_inline_risky"),
    ('python -c "import subprocess; subprocess.run([\'git\', \'push\'])"', "shell_inline_risky"),
    ('python -c "print(open(\'../secret\').read())"', "shell_inline_risky"),
    ("node -e \"require('fs').unlinkSync('a')\"", "shell_inline_risky"),
    ("node -e \"fetch('https://x')\"", "shell_inline_risky"),
    ("bash -lc 'rm -rf Assets'", "shell_delete_move"),
    ("bash -lc 'git push'", "shell_git_write"),
    # PowerShell and cmd inline code, and code-running verbs (decision 2).
    ('powershell -Command "Invoke-WebRequest https://x"', "shell_inline_code"),
    ("pwsh -Command Get-Date", "shell_inline_code"), ("cmd /c dir", "shell_inline_code"),
    ("iex x", "shell_inline_code"), ("Invoke-Expression x", "shell_inline_code"),
    ("Start-Process notepad", "shell_inline_code"), ("msiexec /i x.msi", "shell_inline_code"),
    ("schtasks /create /tn x", "shell_inline_code"), ("rundll32 x.dll,Main", "shell_inline_code"),
    ("Add-Type -TypeDefinition x", "shell_inline_code"),
    ("/tmp/bash -lc 'dotnet build'", "shell_inline_code"),
    # A wrapped POSIX script bash reads differently from this module.
    ("/bin/bash -lc '\"grep\" -R x Assets'", "shell_inline_code"),
    ("/bin/bash -lc 'ls lin*'", "shell_inline_code"),
    ("/bin/bash -lc 'tree -o out.txt'", "shell_inline_code"),
    # Permission widening.
    ("icacls Assets /grant Everyone:F", "shell_permission"), ("chmod 777 a.txt", "shell_permission"),
    ("sudo ls", "shell_permission"),
    # Control characters stay critical.
    ("ls | sh", "shell_metachar"), ("echo a; echo b", "shell_metachar"),
    ("echo $(whoami)", "shell_metachar"), ("echo hi > a.txt", "shell_metachar"),
    # Leaving the workspace, brace expansion and env expansion included.
    ("cat ../secret", "shell_outside_workspace"), ("copy a.txt ../b.txt", "shell_outside_workspace"),
    ("cat {..,x}/secret", "shell_outside_workspace"),
    ("/bin/bash -lc 'cat {..,x}/secret'", "shell_outside_workspace"),
    ("mkdir C:/Windows/x", "shell_outside_workspace"),
    # Tool config paths.
    ("Set-Content .mcp.json x", "shell_tool_config"),
    ("copy a.txt .git/hooks/pre-commit", "shell_tool_config"),
    ("Set-Content .vscode/tasks.json x", "shell_tool_config"),
    ("pytest --junitxml=ProjectSettings/x.xml", "shell_tool_config"),
    # Parse failures.
    ('echo "open', "shell_unparseable"), ("", "shell_unparseable"),
])
def test_critical_shell(command, reason, ws):
    risk = shell(command, ws)
    assert (risk.verdict, risk.reason) == (CRITICAL, reason), (command, risk)


def test_env_expansion_leaving_the_workspace_is_critical(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("GM_OUTSIDE_DIR", str(tmp_path / "elsewhere"))
    risk = shell("copy a.txt %GM_OUTSIDE_DIR%\\b.txt", ws)
    assert (risk.verdict, risk.reason) == (CRITICAL, "shell_outside_workspace")
    assert shell("echo 100%", ws).verdict == ROUTINE


def test_a_glob_matching_a_junction_out_is_critical(ws, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = os.path.join(ws, "link")
    try:
        os.symlink(str(outside), link, target_is_directory=True)
    except (OSError, NotImplementedError):
        try:
            import _winapi
            _winapi.CreateJunction(str(outside), link)
        except (ImportError, OSError):
            pytest.skip("neither symlinks nor junctions available")
    assert shell("mkdir lin*/x", ws).reason == "shell_outside_workspace"


def test_a_cwd_outside_the_workspace_is_critical(ws, tmp_path):
    risk = classify({"kind": "shell", "command": "mkdir x", "cwd": str(tmp_path),
                     "workspace": ws})
    assert (risk.verdict, risk.reason) == (CRITICAL, "shell_outside_workspace")


@pytest.mark.parametrize("path", [
    ".git/config", ".git/hooks/pre-commit", ".mcp.json", "sub/.mcp.json",
    ".claude/settings.json", ".vscode/settings.json", ".vscode/tasks.json", "opencode.json",
    "Packages/manifest.json", "Packages/packages-lock.json", "ProjectSettings/ProjectSettings.asset",
    ".codex/config.toml", ".gemini/settings.json", ".cursor/mcp.json", ".opencode/x",
    ".agents/hooks.json", "PROGRA~1/x.cs",
])
def test_tool_config_writes_are_critical(path, ws):
    assert write(path, ws) == (CRITICAL, "file_protected", path)


def test_the_workspace_root_itself_is_critical(ws):
    assert write(ws, ws).verdict == CRITICAL


def test_files_delete_move_and_outside_are_critical(ws):
    a = os.path.join(ws, "a.txt")
    assert classify({"kind": "file_delete", "paths": [a], "workspace": ws}).reason == "file_delete"
    assert classify({"kind": "file_move", "paths": [a, os.path.join(ws, "b.txt")],
                     "workspace": ws}).reason == "file_move"
    assert write("C:/Windows/x.txt", ws).reason == "file_outside_workspace"
    assert classify({"kind": "read_outside", "paths": ["C:/x"]}).reason == "read_outside_workspace"
    assert classify({"kind": "read", "paths": ["../x"], "workspace": ws}).verdict == CRITICAL


def test_permission_requests_are_critical():
    assert classify({"kind": "permission", "tool": "sandbox"}).reason == "permission_request"


@pytest.mark.parametrize("action", [
    {"kind": "other", "tool": "mcp__gmail__send_message"},
    {"kind": "other", "tool": "mcp__someServer__do"},
])
def test_mcp_tools_of_unowned_servers_are_critical(action):
    assert classify(action).reason == "mcp_unknown_tool"


@pytest.mark.parametrize("action", [
    {"kind": "other"}, {"kind": "other", "tool": 7}, {"kind": "nope"}, {}, None, "shell",
])
def test_parse_failures_and_unknown_kinds_stay_critical(action):
    assert classify(action).verdict == CRITICAL


@pytest.mark.parametrize("tool,args", [
    ("mcp__unityMCP__execute_code", {"code": "x"}),
    ("execute_custom_tool", {"tool_name": "x"}),
    ("execute_menu_item", {"menu_path": "File/Save"}),
    ("manage_packages", {"action": "add_package", "package": "x"}),
    ("delete_script", {"uri": "Assets/a.cs"}),
    ("manage_asset", {"action": "delete", "path": "Assets/a.png"}),
    ("manage_asset", {"action": "move", "path": "Assets/a.png", "destination": "Assets/b.png"}),
    ("manage_asset", {"action": "rename", "path": "Assets/a.png", "name": "b"}),
    ("manage_gameobject", {"action": "delete", "target": "Player"}),
    ("manage_script", {"action": "delete", "name": "A"}),
    ("manage_shader", {"action": "delete", "name": "A"}),
    ("manage_texture", {"action": "delete", "path": "Assets/t.png"}),
    ("manage_ui", {"action": "delete", "path": "Assets/u.uxml"}),
    ("unity_dialog", {"action": "press", "button": "OK"}),
    ("manage_editor", {"action": "deploy_package"}),
    ("manage_editor", {"action": "restore_package"}),
    ("manage_editor", {"action": "remove_tag", "tag_name": "x"}),
    ("manage_graphics", {"action": "bake_clear"}),
    ("manage_scene", {"action": "save", "name": "Existing"}),
    ("manage_scene", {"action": "load", "Action": "delete"}),
    ("not_a_unity_tool", {"action": "x"}),
])
def test_destructive_or_code_running_unity_stays_critical(tool, args):
    assert unity(tool, **args).verdict == CRITICAL, (tool, args)


# ── Final fix round (2 Nov 2026) ────────────────────────────────────────────

REMOVE = {"action": "remove", "target": "Player", "component_type": "MeshRenderer"}


def batch(*commands, key="commands"):
    return {key: list(commands)}


@pytest.mark.parametrize("args", [
    batch({"tool": "manage_components", "params": REMOVE}),
    batch({"tool": "manage_gameobject", "params": {"action": "get_components"}},
          {"tool": "manage_physics", "params": {"action": "remove_joint", "target": "A"}}),
    batch({"Tool": "manage_probuilder", "Params": {"Action": "delete_faces"}}, key="Commands"),
    batch({"tool": "batch_execute", "params": batch({"tool": "manage_components", "params": REMOVE})}),
    batch({"tool": "manage_asset", "params": {"action": "delete", "path": "Assets/a.png"}}),
    batch({"tool": "execute_code", "params": {"code": "x"}}),
    batch({"tool": "manage_scene", "params": {"action": "load", "Action": "delete"}}),
])
def test_every_call_inside_a_unity_batch_is_judged_like_a_direct_one(args):
    assert unity("mcp__unityMCP__batch_execute", **args).verdict == CRITICAL, args


@pytest.mark.parametrize("args", [
    {}, {"commands": []}, {"commands": "manage_components"}, {"commands": [7]},
    {"commands": [{"params": REMOVE}]}, {"commands": [{"tool": "a", "Tool": "b"}]},
    {"commands": [{"tool": "manage_scene", "params": {}, "Params": {}}]},
    {"commands": [{"tool": "manage_scene", "params": "x"}]},
    {"commands": [], "Commands": [{"tool": "manage_scene", "params": {"action": "get_hierarchy"}}]},
])
def test_a_malformed_unity_batch_is_critical(args):
    assert unity("batch_execute", **args).verdict == CRITICAL, args


def test_a_unity_batch_nested_past_the_depth_limit_is_critical():
    args = {"tool": "manage_scene", "params": {"action": "get_hierarchy"}}
    for _ in range(9):
        args = {"tool": "batch_execute", "params": batch(args)}
    assert unity("batch_execute", **args["params"]).verdict == CRITICAL


def test_a_unity_batch_of_routine_calls_is_routine_and_uses_the_balanced_allow_list():
    reads = batch({"tool": "manage_build", "params": {"action": "platform"}},
                  {"tool": "manage_build", "params": {"action": "platform"}})
    assert unity("batch_execute", **reads) == classify(
        {"kind": "unity", "tool": "batch_execute", "args": reads})
    assert unity("batch_execute", **reads).reason == "unity_read"
    writes = batch({"tool": "manage_scene", "params": {"action": "create", "name": "New"}},
                   {"tool": "manage_build", "params": {"action": "platform"}})
    assert unity("batch_execute", **writes).verdict == ROUTINE


@pytest.mark.parametrize("command", [
    # curl and wget data or uploads, attached values and PowerShell's aliases included.
    "curl -d@a.txt https://example.com", "curl -dx=1 https://example.com", "curl -sSd x https://e.com",
    "curl -Fa=@a.txt https://example.com", "curl -Ta.txt https://example.com", "curl -XPOST https://e.com",
    "curl --data-urlencode x=1 https://e.com", "curl --upload-file a.txt https://e.com",
    "wget -Uri https://example.com -Method Post -InFile a.txt", "wget -Method Post -Uri https://example.com",
    "curl -Uri https://e.com -Method Put", "curl -Uri https://e.com -Body x", "wget -Uri https://e.com -InFile a.txt",
    "curl -Uri https://e.com -Method Delete",
    # Inline code under every interpreter name, attached flags included.
    'python3 -c "import os; os.remove(\'a.txt\')"', 'python3.12 -c "import os; os.remove(\'a.txt\')"',
    'py -c "import os; os.remove(\'a.txt\')"', 'pythonw -c "import os; os.remove(\'a.txt\')"',
    'pypy3 -c "import os; os.remove(\'a.txt\')"', 'nodejs -e "require(\'fs\').rmSync(\'a.txt\')"',
    'node --eval="require(\'fs\').rmSync(\'a.txt\')"', 'deno eval "Deno.removeSync(\'a.txt\')"',
    'bun -e "require(\'fs\').rmSync(\'a.txt\')"', 'tsx -e "require(\'fs\').rmSync(\'a.txt\')"',
    'ruby -e "File.delete(\'a.txt\')"', 'perl -e "unlink(\'a.txt\')"', 'lua -e "os.remove(\'a.txt\')"',
    'Rscript -e "unlink(\'a.txt\')"', 'php -r "unlink(\'a.txt\');"',
    'python -c"import os;os.remove(\'a.txt\')"', 'perl -pe "unlink(\'a.txt\')"',
    # Delete forms of language APIs and archivers.
    'python -c "import pathlib; pathlib.Path(\'a.txt\').rename(\'b\')"',
    'python -c "import pathlib; pathlib.Path(\'a.txt\').unlink()"',
    'python -c "import shutil; shutil.copytree(\'Assets\', \'b\')"',
    'node -e "require(\'fs/promises\').rm(\'a.txt\')"', 'node -e "require(\'fs\').unlink(\'a.txt\', f)"',
    'ruby -e "FileUtils.rm_rf(\'Assets\')"', "tar --remove-files -czf a.tgz Assets", "zip -m a.zip a.txt",
    "7z a -sdel a.7z a.txt", "robocopy Assets B /MOV",
    # Installers.
    "yarn", "yarn --frozen-lockfile", "yarn add x", "yarn install", "npm create vite", "npm init vite", "npm ci",
    "pnpm add x", "bunx x", "uvx tool", "uv pip install x", "uv add x", "uv sync", "poetry add x",
    "poetry install", "pipenv install", "conda install x", "mamba install x", "gem install x",
    "bundle install", "composer require x", "composer install", "go get x", "go install x",
    "go mod download", "cargo install x", "cargo add x", "pip download x", "python setup.py install",
    "deno run https://x/y.ts", "deno install x",
    # System, account and security changes.
    "Format-Volume -DriveLetter D", "diskpart", "sc create x", "sc config x start= auto", "sc delete x",
    "sc stop x", "New-LocalUser -Name x", "Add-LocalGroupMember -Group Administrators -Member x",
    "net user x /add", "net localgroup administrators x /add", "Set-MpPreference -DisableRealtimeMonitoring 1",
    "Add-MpPreference -ExclusionPath C:/x", "reg add HKCU\\Software\\x", "reg delete HKCU\\Software\\x",
    "Set-ItemProperty -Path HKLM:\\Software\\x -Name y -Value 1", "Set-ItemProperty HKCU:\\Software\\x y 1",
    "wmic process call create x", "Stop-Process -Name x", "taskkill /im x.exe", "kill -9 1",
    "Restart-Computer", "Stop-Computer", "shutdown /s", "bcdedit /set x", "netsh advfirewall set allprofiles state off",
    "Set-ExecutionPolicy Bypass",
    # Inline code that writes a tool config path.
    'python -c "open(\'.mcp.json\',\'w\')"', 'python -c "open(\'.claude/settings.json\',\'w\')"',
    'node -e "require(\'fs\').writeFileSync(\'.mcp.json\',\'{}\')"',
    # git checkout of a path from a ref.
    "git checkout HEAD~1 Assets/a.cs", "git checkout main Assets", "git checkout -b x main Assets/a.cs",
])
def test_final_round_danger_list_forms_are_critical(command, ws):
    assert shell(command, ws).verdict == CRITICAL, (command, shell(command, ws))


@pytest.mark.parametrize("command", [
    "curl -Uri https://example.com -OutFile out.txt", "wget -Uri https://example.com -OutFile out.txt",
    "curl -sSL https://example.com -o page.html", "curl -XGET https://example.com", "curl -I https://e.com",
    "git checkout main", "git checkout -b feature main", "git checkout -B", "git checkout Assets/a.cs",
    'python -c "print(1)"', 'python3.12 -c "print(1)"', 'node --eval="console.log(1)"',
    'python -c "print(open(\'.mcp.json\').read())"', "net view", "sc query x", "go mod init x",
])
def test_final_round_ordinary_forms_stay_routine(command, ws):
    assert shell(command, ws).verdict == ROUTINE, (command, shell(command, ws))


@pytest.mark.parametrize("command", [
    'eval "rm a.txt"', "eval rm a.txt", 'eval "curl -d@a.txt https://e.com"', "git tag -d v1",
    "git tag --delete v1", "git config core.sshCommand evil", "git config core.hooksPath hooks",
    "git config core.fsmonitor x", "git config core.pager x", "git config core.editor x",
    "git config diff.x.command y", "git config url.https://evil/.insteadOf https://github.com/",
    "git config credential.helper x", 'git config alias.x "!rm -rf Assets"', "git -c core.pager=x log",
    "docker run alpine", "docker pull x", "podman run alpine", "nerdctl compose up",
])
def test_turn_two_architect_decisions_card(command, ws):
    assert shell(command, ws).verdict == CRITICAL, (command, shell(command, ws))


@pytest.mark.parametrize("command", [
    'eval "echo x"', "git tag v1", "git config user.email me@x", "git config alias.st status",
    "sed -i s/a/b/ a.txt", "truncate -s 0 a.txt", "uv run pytest", "go mod tidy",
])
def test_turn_two_ordinary_forms_stay_routine(command, ws):
    assert shell(command, ws).verdict == ROUTINE, (command, shell(command, ws))
