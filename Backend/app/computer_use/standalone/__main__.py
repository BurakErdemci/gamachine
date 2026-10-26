"""Independent, explicitly enabled computer-use MCP entry point."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile

from .. import platform
from ..platform import _win32
from ..server import create_server
from . import config
from .approvals import LocalApprovals
from .hotkey import HaltHotkey
from .runtime import StandaloneRuntime


def _startup_refusal(hotkey):
    if platform.current().name != "win32":
        return "Bu mod yalnız Windows'ta çalışır."
    if os.environ.get("GAMACHINE_CU_STANDALONE") != "1":
        return "Bağımsız bilgisayar kullanımı kapalı (GAMACHINE_CU_STANDALONE=1 gerekli)."
    if "GAMACHINE_CONVERSATION_ID" in os.environ:
        return "Bu mod Gamachine sohbetinde kullanılmaz."
    if _win32.self_elevated():
        return "Yönetici olarak çalışırken bilgisayar kullanımı başlatılmaz."
    cfg = config.load()
    if "error" in cfg:
        return cfg["error"]
    if cfg["enabled"] is not True:
        return ("Bağımsız bilgisayar kullanımı kapalı; config.json içinde enabled true yapın: "
                + str(config.STATE_DIR / "config.json"))
    hotkey.start()
    if not hotkey.registered:
        return "Durdurma kısayolu (Ctrl+Alt+Esc) alınamadı; bilgisayar kullanımı başlatılmadı."
    return None


def _install_driver():
    extracted = []
    try:
        cfg = config.load()
        if "error" in cfg:
            raise ValueError(cfg["error"])
        with tempfile.TemporaryFile() as wheel:
            with urllib.request.urlopen(config.WHEEL_URL, timeout=120) as response:
                shutil.copyfileobj(response, wheel)
            wheel.seek(0)
            if hashlib.file_digest(wheel, "sha256").hexdigest() != config.WHEEL_SHA256:
                raise ValueError("Driver wheel SHA-256 mismatch")
            wheel.seek(0)
            directory = config.STATE_DIR / "driver"
            with zipfile.ZipFile(wheel) as archive:
                # Verify before replacing an existing installation.
                binary = archive.read("cua_driver/bin/cua-driver.exe")
                helper = archive.read("cua_driver/bin/cua-driver-uia.exe")
                if hashlib.sha256(binary).hexdigest() != config.EXE_SHA256:
                    raise ValueError("Driver executable SHA-256 mismatch")
                directory.mkdir(parents=True, exist_ok=True)
                for name, content in (("cua-driver.exe", binary), ("cua-driver-uia.exe", helper)):
                    path = directory / name
                    extracted.append(path)
                    path.write_bytes(content)
            binary_path = (directory / "cua-driver.exe").resolve()
            if hashlib.sha256(binary_path.read_bytes()).hexdigest() != config.EXE_SHA256:
                raise ValueError("Extracted driver SHA-256 mismatch")
            # Preserve even keys that load() sanitizes, including the stored grants.
            path = config.STATE_DIR / "config.json"
            stored = json.loads(path.read_text(encoding="utf-8"))
            stored["driver_path"] = str(binary_path)
            path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
        print(binary_path)
        return 0
    except Exception as exc:
        for path in extracted:
            path.unlink(missing_ok=True)
        print(str(exc), file=sys.stderr)
        return 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "install-driver":
        raise SystemExit(_install_driver())
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    hotkey = HaltHotkey()
    with config.log_path().open("a", encoding="utf-8") as log:
        previous_stderr = sys.stderr
        sys.stderr = log
        try:
            refusal = _startup_refusal(hotkey)
            create_server(approvals=LocalApprovals(refusal, hotkey),
                          runtime=StandaloneRuntime(), name="gamachineComputerLocal").run(transport="stdio")
        finally:
            hotkey.stop()
            sys.stderr = previous_stderr


if __name__ == "__main__":
    main()
