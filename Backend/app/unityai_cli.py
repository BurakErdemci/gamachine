"""
unityai CLI — agy (Antigravity CLI) için MCP köprüsü.

NEDEN: agy `--print` modu stdio MCP server'larını YÜKLEMEZ, bu yüzden
`mcp__unityai__*` araçları agy'ye görünmez. agy'nin gördüğü tek yol
built-in `run_command` aracıdır. Bu CLI tam da onun için var: agy bunu
`run_command` ile çağırır, biz de MCP tool'larıyla BİREBİR aynı approval
köprüsünü kullanırız.

KULLANIM (agy run_command ile çağırır):
  unityai save-file   --path "Assets/Scripts/X.cs" --content "..."
  unityai save-file   --path "..." --content-stdin   < içerik stdin'den
  unityai delete-file --path "Assets/Scripts/X.cs"
  unityai read-file   --path "Assets/Scripts/X.cs"
  unityai list-dir    --path "Assets/Scripts/"
  unityai bash        --command "git status"

Her tehlikeli operasyon (save/delete/bash) approval_bridge üzerinden
IDE onayı ister — MCP tool'larıyla aynı davranış. Hangi komutun onaysız
geçebileceğine tek yerden karar verilir: agentic/command_safety.py.

Workspace ve backend bilgisi:
  WORKSPACE     env → yoksa cwd
  UNITYAI_URL   env → approval_bridge.BACKEND_URL (yoksa localhost:8000)
  LOCAL_APP_TOKEN env → approval header
(Bunlar wrapper script tarafından .unityai_cli.env'den de yüklenebilir.)
"""
import os
import sys
import argparse
import asyncio
import subprocess

# MCP tool'larıyla paylaşılan güvenlik/onay helper'ları — TEK KAYNAK.
# Bu importlar unity_ai_mcp/tools/ içindeki dosyaları DEĞİŞTİRMEZ, sadece kullanır.
from unity_ai_mcp.approval_bridge import request_approval
from unity_ai_mcp.tools.file_tools import _resolve
from unity_ai_mcp.tools.bash_tool import _auto_safe_argv, _is_safe, _parse_file_write
import unity_file_guard


def _workspace() -> str:
    return os.environ.get("WORKSPACE", "") or os.getcwd()


def cmd_read_file(args) -> int:
    workspace = _workspace()
    abs_path = _resolve(args.path, workspace)
    if not os.path.exists(abs_path):
        print(f"Hata: Dosya bulunamadı: {args.path}")
        return 1
    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    total = len(lines)
    content = "".join(lines[:500])
    suffix = f"\n... ({total - 500} satır daha)" if total > 500 else ""
    print(content + suffix)
    return 0


def cmd_list_dir(args) -> int:
    workspace = _workspace()
    abs_path = _resolve(args.path, workspace)
    if not os.path.isdir(abs_path):
        print(f"Hata: Klasör bulunamadı: {args.path}")
        return 1
    for entry in sorted(os.listdir(abs_path)):
        full = os.path.join(abs_path, entry)
        tag = "/" if os.path.isdir(full) else ""
        print(f"{entry}{tag}")
    return 0


def cmd_save_file(args) -> int:
    workspace = _workspace()
    abs_path = _resolve(args.path, workspace)
    refusal = unity_file_guard.check_write(abs_path, workspace)
    if refusal is not None:
        print(f"❌ {refusal.message}")
        return 1

    content = sys.stdin.read() if args.content_stdin else (args.content or "")

    original = ""
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                original = f.read()
            if original.strip() == content.strip():
                print("Dosya zaten aynı içerikte, değişiklik yok.")
                return 0
        except Exception:
            pass

    result = asyncio.run(request_approval(
        tool_name="write_file",
        params={"path": args.path, "content": content, "original": original},
        workspace_path=workspace,
    ))
    if result.get("approved") is not True:
        print(f"❌ Yazma reddedildi: {args.path}")
        return 1

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ Yazıldı: {args.path}")
    return 0


def cmd_delete_file(args) -> int:
    workspace = _workspace()
    abs_path = _resolve(args.path, workspace)
    refusal = unity_file_guard.check_delete(abs_path, workspace)
    if refusal is not None:
        print(f"❌ {refusal.message}")
        return 1
    if not os.path.exists(abs_path):
        print(f"Hata: Dosya bulunamadı: {args.path}")
        return 1

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            original = f.read()
    except Exception:
        original = ""

    result = asyncio.run(request_approval(
        tool_name="delete_file",
        params={"path": args.path, "original": original},
        workspace_path=workspace,
    ))
    if result.get("approved") is not True:
        print(f"❌ Silme reddedildi: {args.path}")
        return 1

    os.remove(abs_path)
    print(f"🗑️ Silindi: {args.path}")
    return 0


def cmd_bash(args) -> int:
    workspace = _workspace()
    command = args.command
    refusal = unity_file_guard.check_shell(command, workspace)
    if refusal is not None:
        print(f"❌ {refusal.message}")
        return 1

    # Terminalle dosya yazma girişimi → DiffViewer'a yönlendir (MCP bash_tool ile aynı)
    file_write = _parse_file_write(command)
    if file_write:
        path, new_content = file_write
        if not new_content.strip():
            print("❌ Boş dosya oluşturma reddedildi. İçerikle birlikte 'unityai save-file' kullan.")
            return 1
        abs_path = _resolve(path, workspace)
        refusal = unity_file_guard.check_write(abs_path, workspace)
        if refusal is not None:
            print(f"❌ {refusal.message}")
            return 1
        original = ""
        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    original = f.read()
            except Exception:
                pass
        result = asyncio.run(request_approval(
            tool_name="write_file",
            params={"path": path, "content": new_content, "original": original},
            workspace_path=workspace,
        ))
        if result.get("approved") is not True:
            print(f"❌ Reddedildi: {path}")
            return 1
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"✅ Yazıldı: {path}")
        return 0

    # Gerekçe bash_tool.py'deki ikiziyle aynı: karar ile token tek çağrıdan
    # geliyor, böylece denetlenen metin ile çalıştırılan metin ayrışamıyor.
    argv = _auto_safe_argv(command, workspace)
    if argv is None:
        result = asyncio.run(request_approval(
            tool_name="bash",
            params={"command": command},
            workspace_path=workspace,
        ))
        if result.get("approved") is not True:
            print(f"❌ Komut reddedildi: {command}")
            return 1

    hedef, kabuk_kullan = (command, True) if argv is None else (argv, False)

    try:
        proc = subprocess.run(
            hedef,
            shell=kabuk_kullan,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if len(output) > 8000:
            output = output[:8000] + "\n... (kısaltıldı)"
        print(output or "(çıktı yok)")
        return proc.returncode
    except subprocess.TimeoutExpired:
        print("❌ Zaman aşımı (300s)")
        return 1
    except Exception as e:
        print(f"❌ Hata: {str(e)}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="unityai", description="UnityAI CLI köprüsü (agy için)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_read = sub.add_parser("read-file", help="Dosya okur")
    p_read.add_argument("--path", required=True)
    p_read.set_defaults(func=cmd_read_file)

    p_list = sub.add_parser("list-dir", help="Klasör listeler")
    p_list.add_argument("--path", default=".")
    p_list.set_defaults(func=cmd_list_dir)

    p_save = sub.add_parser("save-file", help="Dosya oluşturur/düzenler (ONAY)")
    p_save.add_argument("--path", required=True)
    p_save.add_argument("--content", default=None, help="Dosya içeriği")
    p_save.add_argument("--content-stdin", action="store_true",
                        help="İçeriği stdin'den oku (çok satırlı/büyük içerik için)")
    p_save.set_defaults(func=cmd_save_file)

    p_del = sub.add_parser("delete-file", help="Dosya siler (ONAY)")
    p_del.add_argument("--path", required=True)
    p_del.set_defaults(func=cmd_delete_file)

    p_bash = sub.add_parser("bash", help="Terminal komutu çalıştırır (ONAY)")
    p_bash.add_argument("--command", required=True)
    p_bash.set_defaults(func=cmd_bash)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except PermissionError as e:
        # _resolve workspace dışı yolu reddetti — agy'ye traceback yerine temiz mesaj
        print(f"❌ {e}")
        return 1
    except Exception as e:
        print(f"❌ Hata: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
