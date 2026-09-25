"""
Unity Proje Dosya Araçları — Agentic AI'ın projede dosya okuma, yazma ve listeleme yapabilmesi için.
"""
import os
import logging
from pathlib import Path
from typing import Optional

import unity_file_guard

logger = logging.getLogger(__name__)

# Güvenlik: Sadece workspace içinde çalış
def _validate_path(file_path: str, workspace_path: str) -> str:
    """Dosya yolunun workspace içinde olduğunu doğrular."""
    workspace = Path(workspace_path).expanduser().resolve(strict=False)
    target = Path(file_path).expanduser()
    if not target.is_absolute():
        target = workspace / target
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(workspace)
    except ValueError:
        raise PermissionError(f"Güvenlik: {file_path} workspace dışında.")
    return str(resolved)


def read_file(file_path: str, workspace_path: str) -> dict:
    """
    Belirtilen dosyayı okur ve içeriğini döndürür.
    Uzun dosyalar için ilk 500 satırı döndürür.
    """
    try:
        abs_path = _validate_path(file_path, workspace_path)
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"Dosya bulunamadı: {file_path}"}

        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total_lines = len(lines)
        # Çok büyük dosyalarda ilk 500 satır
        if total_lines > 500:
            content = "".join(lines[:500])
            truncated = True
        else:
            content = "".join(lines)
            truncated = False

        return {
            "success": True,
            "content": content,
            "total_lines": total_lines,
            "truncated": truncated,
            "path": file_path,
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Dosya okuma hatası: {str(e)}"}


def write_file(file_path: str, content: str, workspace_path: str) -> dict:
    """Belirtilen dosyaya içerik yazar. Klasör yoksa oluşturur."""
    try:
        abs_path = _validate_path(file_path, workspace_path)
        refusal = unity_file_guard.check_write(abs_path, workspace_path)
        if refusal is not None:
            return {"success": False, "error": refusal.message}
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "path": abs_path, "bytes_written": len(content)}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Dosya yazma hatası: {str(e)}"}


def list_directory(dir_path: str, workspace_path: str, extensions: list = None) -> dict:
    """
    Klasör içeriğini listeler. Opsiyonel olarak uzantıya göre filtreler.
    extensions örneği: [".cs", ".shader"]
    """
    try:
        abs_path = _validate_path(dir_path, workspace_path)
        if not os.path.isdir(abs_path):
            return {"success": False, "error": f"Klasör bulunamadı: {dir_path}"}

        items = []
        for entry in sorted(os.listdir(abs_path)):
            full = os.path.join(abs_path, entry)
            is_dir = os.path.isdir(full)

            if extensions and not is_dir:
                if not any(entry.endswith(ext) for ext in extensions):
                    continue

            item = {
                "name": entry,
                "is_directory": is_dir,
                "path": os.path.join(dir_path, entry),
            }
            if not is_dir:
                item["size_bytes"] = os.path.getsize(full)
            else:
                # Alt dosya sayısını hesapla
                try:
                    item["child_count"] = len(os.listdir(full))
                except PermissionError:
                    item["child_count"] = -1

            items.append(item)

        return {"success": True, "path": dir_path, "items": items, "count": len(items)}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Listeleme hatası: {str(e)}"}


def delete_file(file_path: str, workspace_path: str) -> dict:
    """
    Unity projesindeki bir dosyayı siler.
    """
    try:
        abs_path = _validate_path(file_path, workspace_path)
        refusal = unity_file_guard.check_delete(abs_path, workspace_path)
        if refusal is not None:
            return {"success": False, "error": refusal.message}
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"Dosya bulunamadı: {file_path}"}
        
        # Gerçek silme işlemi
        os.remove(abs_path)
        
        return {
            "success": True, 
            "summary": f"'{file_path}' dosyası başarıyla silindi.",
            "path": file_path
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Dosya silme hatası: {str(e)}"}


import subprocess

from agentic.command_safety import auto_safe_argv as _auto_safe_argv


def run_command(command: str, workspace_path: str) -> dict:
    """
    Terminalde bir sistem komutu çalıştırır (örn: npm install, git status, unity build vb.).

    Onay kararı burada VERİLMİYOR: `agent_runner._approval_command_for` komutu
    `requires_approval`'a sorup gerekiyorsa kartı çıkarıyor ve onaylanmazsa bu
    fonksiyona hiç gelinmiyor. Burada yalnız ÇALIŞTIRMA kipi seçiliyor.

    ⚠️ Bu ayrım Faz 1'de gözden kaçtı ve düzeltme önce yalnız `bash_tool` ile
    `unityai_cli`'a uygulandı — karar kaynağı üçünde de ortaktı, ama çalıştırma
    yolu üç ayrı yerdeydi ve bu üçüncüsü `shell=True` ile kalmıştı. Yani
    ayrıştırıcı/çalıştırıcı uyuşmazlığı sınıfı bu yoldan hâlâ açıktı.
    """
    refusal = unity_file_guard.check_shell(command, workspace_path)
    if refusal is not None:
        return {"success": False, "error": refusal.message}
    try:
        # Git ve diğer araçların interaktif soru sormasını engelle
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        # Onaysız geçebilen komut kabuğa HİÇ verilmiyor: denetlenen token listesi
        # doğrudan çalışıyor. `None` dönmesi komutun onay gerektirdiği (ve
        # çağıran tarafından zaten onaylandığı) anlamına gelir — kullanıcı ham
        # dizgeyi gördüğü için orada kabuk kullanmak güvenli.
        argv = _auto_safe_argv(command, workspace_path)
        hedef, kabuk_kullan = (command, True) if argv is None else (argv, False)

        # Arka planda sessizce çalıştır
        process = subprocess.run(
            hedef,
            shell=kabuk_kullan,
            cwd=workspace_path,
            text=True,
            capture_output=True,
            timeout=300,
            env=env
        )
        
        output = process.stdout if process.stdout else ""
        error = process.stderr if process.stderr else ""
        
        # UI İyileştirmesi: Eğer bir çıktı (output veya error) alabildiysek, 
        # bu AI için başarılı bir bilgi toplama işlemidir. 
        # Sadece komut hiç çalışamazsa veya sistem hatası olursa success=False döner.
        any_output = bool(output.strip() or error.strip())
        
        return {
            "success": any_output or (process.returncode == 0),
            "stdout": output,
            "stderr": error,
            "exit_code": process.returncode,
            "summary": f"Komut çıktı verdi (Kod {process.returncode})" if any_output else f"Komut tamamlandı (Kod {process.returncode})"
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Komut zaman aşımına uğradı (300s)."}
    except Exception as e:
        return {"success": False, "error": f"Komut çalıştırma hatası: {str(e)}"}
