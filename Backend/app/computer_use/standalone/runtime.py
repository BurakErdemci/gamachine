"""Own and verify the independent Windows driver daemon."""
import asyncio
import hashlib
import os
from pathlib import Path

from .. import platform
from ..driver_client import DriverUnavailable
from . import config


_VERIFY_ERROR = ("Bilgisayar kullanımı sürücüsü doğrulanamadı; "
                 "`python -m computer_use.standalone install-driver` çalıştırın.")
_START_ERROR = "Bilgisayar kullanımı sürücüsü başlatılamadı."


def _pipe_ready(pipe):
    # os.path.exists stayed False for 10 s on a listening named pipe (measured
    # 2026-10-26, cua-driver 0.35.4); the pipe directory listing shows it at once.
    try:
        return pipe.rpartition("\\")[2] in os.listdir(r"\\.\pipe" + "\\")
    except OSError:
        return False


class StandaloneRuntime:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._daemon = None
        self._binary = None
        self._log = None
        self._pipe = r"\\.\pipe\gamachine-cu-standalone-" + str(os.getpid())

    def _verified_binary(self):
        value = config.load()["driver_path"]
        try:
            path = Path(value) if isinstance(value, str) else None
            if (path is None or not path.is_absolute() or path.name != "cua-driver.exe"
                    or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != config.EXE_SHA256):
                raise ValueError("Unverified driver")
        except (OSError, ValueError):
            raise DriverUnavailable(_VERIFY_ERROR) from None
        return str(path)

    async def __call__(self):
        async with self._lock:
            binary = self._verified_binary()
            if (self._daemon is not None and self._daemon.returncode is None
                    and self._binary == binary):
                return binary, self._pipe
            await self._close_daemon()
            env = platform.current().driver_command(binary, self._pipe)[1]
            config.STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._log = config.log_path().open("a", encoding="utf-8")
            try:
                self._daemon = await asyncio.create_subprocess_exec(
                    binary, "serve", "--embedded", "--socket", self._pipe, "--no-overlay",
                    env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                    stderr=self._log)
                self._binary = binary
                deadline = asyncio.get_running_loop().time() + 10
                while asyncio.get_running_loop().time() < deadline:
                    if self._daemon.returncode is not None:
                        break
                    if _pipe_ready(self._pipe):
                        return binary, self._pipe
                    await asyncio.sleep(0.1)
            except OSError:
                pass
            except BaseException:
                await self._close_daemon()
                raise
            await self._close_daemon(kill=True)
            raise DriverUnavailable(_START_ERROR)

    async def _close_daemon(self, *, kill=False):
        try:
            if self._daemon is not None and self._daemon.returncode is None:
                if kill:
                    self._daemon.kill()
                else:
                    self._daemon.terminate()
                try:
                    await asyncio.wait_for(self._daemon.wait(), 5)
                except asyncio.TimeoutError:
                    self._daemon.kill()
                    await self._daemon.wait()
        finally:
            self._daemon = None
            self._binary = None
            if self._log is not None:
                self._log.close()
                self._log = None

    async def aclose(self):
        async with self._lock:
            await self._close_daemon()
