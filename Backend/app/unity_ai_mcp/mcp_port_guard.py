"""8080 portundaki bir sürecin GERÇEKTEN bizim MCP sunucumuz olduğunu doğrular.

Arıza (kod denetimi, 2026-07-27): hem backend açılışı (`main.py` lifespan) hem
`UnityMCPManager.stop_server` 8080'i LISTEN eden **her** PID'e SIGTERM /
`taskkill /T /F` gönderiyordu. Oradaki `-sTCP:LISTEN` filtresi yalnızca daha
eski bir hatayı ("Unity Editor client olarak bağlı, onu da öldürüyoruz")
kapatıyordu; asıl ölçütü karşılamıyordu. 8080 çok yaygın bir port — kullanıcının
alakasız bir dev server'ı orada duruyorsa uygulama açılışta onu sessizce
öldürüyordu.

Ölçüt: **yalnız kendi başlattığımız süreci öldür.** Kimliği doğrulanamayan süreç
öldürülmez, kullanıcıya anlaşılır bir hata verilir. Port çakışmasını çözememek,
başkasının işini haber vermeden öldürmekten daha iyidir.

Kimlik doğrulaması neden **komut satırı** üzerinden (üç aday tartıldı):

* **PID dosyası** NİYETİ kaydeder, KİMLİĞİ değil. Çöküşten sonra kalan bir kayıt
  PID geri dönüşümüyle yabancı bir sürece işaret edebilir; buna karşı sürecin
  başlangıç zamanını da karşılaştırmak gerekir — o da zaten aşağıdaki `ps` /
  CIM sorgusunun ta kendisi. Yani PID dosyası, bayatlayabilen fazladan bir disk
  durumu karşılığında ek hiçbir kanıt satın almıyor.
* **`/health` cevabı** bir PID'e BAĞLANAMIYOR: `lsof`'un bildirdiği PID ile
  health'e cevap veren sürecin aynı olduğunun garantisi yok (arada yarış var) ve
  o uç bilerek kimliksiz bırakıldı (toggle'ın canlılık kontrolü ona bağlı) —
  taklit etmek bedava. `is_running()` onu "port dolu ama MCP değil" ayrımı için
  kullanmaya devam ediyor; **öldürme kararı** için yetersiz.
* **Sürecin komut satırı** PID → kimlik bağını doğrudan kurar, yeni bağımlılık
  istemez (`psutil` bu ortamda kurulu değil) ve `ps`/`tasklist` bu kod tabanında
  zaten kullanılıyor (`is_unity_running`).

Canlı doğrulandı (2026-07-27, macOS, çalışan sunucuyla): 8080'i dinleyen süreç
``.../archive-v0/.../bin/mcp-for-unity --transport http --http-url
http://127.0.0.1:8080 --project-scoped-tools`` ve ata zinciri
``dinleyici → uv tool uvx --from .../unity-mcp/Server mcp-for-unity → backend``.
Yani dinleyen süreç bizim `Popen` çocuğumuz değil, **torunumuz**.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Set, Union

logger = logging.getLogger(__name__)

DEFAULT_MCP_PORT = 8080

# Sunucumuzun komut satırında zorunlu olarak geçen belirteç. Hem uvx'in kendi
# argv'sinde (`uvx --from <Server> mcp-for-unity ...`) hem de asıl dinleyici
# torunun argv'sinde geçtiği için, port sahibinin ağacın hangi katmanı olduğunu
# bilmemize gerek kalmıyor.
SERVER_COMMAND_MARKER = "mcp-for-unity"

# Ata zinciri yürürken üst sınır: `ps` tutarsız bir ppid döndürürse (ya da bir
# döngü oluşursa) sonsuz döngüye girmeyelim.
_MAX_ANCESTRY_DEPTH = 16

_SUBPROCESS_TIMEOUT = 5


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: Optional[int]
    command: str


@dataclass
class PortCleanupResult:
    """`terminate_port_listeners` sonucu.

    `refused` boş değilse port hâlâ dolu ve çağıran kullanıcıya haber vermeli —
    sessizce devam etmek, düzeltilen arızanın kullanıcı gözündeki hâlini
    (açıklamasız port çakışması) geri getirir.
    """

    port: int
    killed: List[int] = field(default_factory=list)
    refused: List[int] = field(default_factory=list)

    @property
    def message(self) -> str:
        return port_busy_message(self.port, self.refused)


def port_busy_message(port: int, owners: Sequence[Union[int, ProcessInfo]]) -> str:
    """Kullanıcıya gösterilecek metin. Tek yerde: hem açılış logu, hem toggle
    hatası, hem stop_server uyarısı aynı cümleyi kullanmalı.

    `ProcessInfo` verilirse süreç ADI da yazılır. Sebebi ölçülmüş bir eksiklik:
    "PID 4321'i kapatın" bir kullanıcının uygulayabileceği talimat değil — hangi
    programı kapatacağını bilmiyor. Ad elde zaten vardı ve atılıyordu.
    """
    joined = ", ".join(_describe_owner(owner) for owner in owners)
    return (
        f"{port} portu bize ait olmayan bir süreç tarafından kullanılıyor "
        f"({joined}). O süreci kapatıp tekrar deneyin."
    )


def _describe_owner(owner: Union[int, ProcessInfo]) -> str:
    if not isinstance(owner, ProcessInfo):
        return f"PID {owner}"
    name = _executable_name(owner.command)
    return f"{name} · PID {owner.pid}" if name else f"PID {owner.pid}"


def _executable_name(command: str) -> str:
    """Komut satırından YALNIZ çalıştırılabilir adı.

    Tam komut satırı BİLEREK gösterilmiyor: başka bir programın argv'si onun
    sırrını taşıyabilir (bu depo kendi tarafında argv'nin `ps` ile herkese
    görünür olduğunu zaten bir gerekçe olarak kullanıyor). Kullanıcının ihtiyacı
    olan tek şey hangi programı kapatacağı.
    """
    first = command.strip().split(" ", 1)[0] if command else ""
    return os.path.basename(first)[:60]


# ─── Süreç sorgulama ────────────────────────────────────────────────────────


def _run(cmd: Sequence[str]) -> Optional[str]:
    """Komutu çalıştırır; araç yoksa/patlarsa None döner.

    None ile boş çıktı ayrımı önemli: araç yoksa 'port boş' sonucuna varmak
    yanlış olurdu, o yüzden çağıranlar None'ı 'bilinmiyor' olarak ele alıyor.
    """
    try:
        result = subprocess.run(
            list(cmd), capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f"[PortGuard] {cmd[0]} çalıştırılamadı: {e}")
        return None
    return result.stdout or ""


def list_listening_pids(port: int = DEFAULT_MCP_PORT) -> List[int]:
    """Porta LISTEN durumunda oturan PID'ler.

    LISTEN filtresi korunuyor: Unity Editor sunucuya CLIENT olarak bağlanıyor ve
    filtresiz sorgu onun PID'sini de listeliyor — onu öldürmek tüm Unity'yi
    kapatıyordu.
    """
    if sys.platform == "win32":
        out = _run(["netstat", "-ano", "-p", "TCP"])
        if out is None:
            return []
        pids: List[int] = []
        for line in out.splitlines():
            parts = line.split()
            # netstat sütunları: proto, local, foreign, state, pid
            if len(parts) < 5 or parts[3] != "LISTENING":
                continue
            # ":8080" ile bitme kontrolü ikinci noktayı da içeriyor; ":18080"
            # yanlışlıkla eşleşmesin diye ayıraç dahil karşılaştırılıyor.
            if not parts[1].endswith(f":{port}"):
                continue
            try:
                pid = int(parts[4])
            except ValueError:
                continue
            if pid not in pids:
                pids.append(pid)
        return pids

    out = _run(["lsof", "-ti", f":{port}", "-sTCP:LISTEN"])
    if out is None:
        return []
    pids = []
    for token in out.split():
        try:
            pid = int(token)
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def query_process(pid: int) -> Optional[ProcessInfo]:
    """PID'in ebeveyni ve komut satırı; süreç yoksa/sorgulanamazsa None."""
    if sys.platform == "win32":
        # `wmic` Windows 11 24H2'de kaldırıldı, o yüzden CIM kullanılıyor.
        # DİKKAT: bu dal macOS'ta doğrulanamadı (bkz. modül raporu).
        out = _run([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=%d\"; "
            "if ($p) { \"$($p.ParentProcessId)`t$($p.CommandLine)\" }" % pid,
        ])
        if not out or not out.strip():
            return None
        head, _, command = out.strip().partition("\t")
        try:
            ppid: Optional[int] = int(head.strip())
        except ValueError:
            ppid = None
        return ProcessInfo(pid=pid, ppid=ppid, command=command.strip())

    out = _run(["ps", "-o", "ppid=,command=", "-p", str(pid)])
    if not out or not out.strip():
        return None
    head, _, command = out.strip().partition(" ")
    try:
        ppid = int(head.strip())
    except ValueError:
        ppid = None
    return ProcessInfo(pid=pid, ppid=ppid, command=command.strip())


def has_server_marker(pid: int) -> bool:
    """Sürecin komut satırı bizim sunucumuzu mu gösteriyor?"""
    info = query_process(pid)
    return bool(info and SERVER_COMMAND_MARKER in info.command)


def is_descendant_of(pid: int, ancestor_pid: int) -> bool:
    """`pid`, `ancestor_pid`'in süreç ağacında mı?

    Neden gerekli: asıl dinleyici bizim `Popen` çocuğumuz (uvx) değil, onun
    çocuğu. Yani "PID eşit mi" kontrolü hiçbir zaman tutmuyor.
    """
    current = pid
    for _ in range(_MAX_ANCESTRY_DEPTH):
        info = query_process(current)
        if info is None or info.ppid is None or info.ppid <= 1:
            return False
        if info.ppid == ancestor_pid:
            return True
        current = info.ppid
    return False


def collect_owned_listeners(
    port: int = DEFAULT_MCP_PORT, ancestor_pid: Optional[int] = None
) -> Set[int]:
    """Porta oturan, bizim süreç ağacımızdaki PID'ler.

    ZAMANLAMA KRİTİK: bu çağrı, aradaki uvx süreci öldürülmeden ÖNCE yapılmalı.
    uvx ölünce dinleyici torun init'e evlat ediniliyor (ppid=1) ve ata zinciri
    bir daha kurulamıyor; o noktadan sonra elde yalnız komut satırı belirteci
    kalıyor.
    """
    ancestor = os.getpid() if ancestor_pid is None else ancestor_pid
    return {pid for pid in list_listening_pids(port) if is_descendant_of(pid, ancestor)}


def _terminate(pid: int) -> bool:
    """Tek bir PID'i durdurur. Süreç çoktan gitmişse de True (hedefe ulaşıldı)."""
    from process_tree import note_kill
    note_kill("mcp_port_guard._terminate", [pid])
    if sys.platform == "win32":
        # /T: uvx ara süreci ile asıl dinleyici ayrı PID'ler; ağacı birlikte kapat.
        return _run(["taskkill", "/PID", str(pid), "/T", "/F"]) is not None
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError as e:
        logger.warning(f"[PortGuard] PID {pid} durdurulamadı: {e}")
        return False
    return True


def terminate_port_listeners(
    port: int = DEFAULT_MCP_PORT,
    owned_pids: Iterable[int] = (),
) -> PortCleanupResult:
    """Porttaki dinleyicilerden **yalnız kimliği doğrulananları** durdurur.

    Bir PID üç yoldan biriyle "bizim" sayılır:
      1. `owned_pids` — çağıran onu kendisi başlattığını biliyor (en güçlü kanıt),
      2. bu sürecin ağacında bir torun,
      3. komut satırında `mcp-for-unity` belirteci var (önceki oturumdan kalan
         yetim için tek geçerli yol).

    Hiçbiri tutmuyorsa süreç **öldürülmez**, `refused` listesine yazılır.
    """
    owned = set(owned_pids)
    result = PortCleanupResult(port=port)
    self_pid = os.getpid()

    for pid in list_listening_pids(port):
        # Kendimizi öldürmeyelim: backend başka bir portta dinliyor ama bu
        # koruma bedava ve yanlış yapılandırmada felaketi önlüyor.
        if pid == self_pid:
            continue
        if pid in owned or is_descendant_of(pid, self_pid) or has_server_marker(pid):
            if _terminate(pid):
                result.killed.append(pid)
            else:
                result.refused.append(pid)
        else:
            result.refused.append(pid)
            logger.warning(
                f"[PortGuard] PID {pid} {port} portunu tutuyor ama bizim sunucumuz "
                f"değil — DOKUNULMADI."
            )
    return result


def foreign_port_owner_infos(port: int = DEFAULT_MCP_PORT) -> List[ProcessInfo]:
    """Portu tutan ve bize ait OLMAYAN süreçler — PID **ve** komut satırıyla.

    `foreign_port_owners` bunun yalnız PID'lerini veren ince sarmalayıcısı.
    Ayrı bir fonksiyon olmasının sebebi: komut satırı `has_server_marker`
    içinde zaten sorgulanıp atılıyordu, ve kullanıcıya gösterilecek mesajın
    ona ihtiyacı var. Ek sistem çağrısı doğurmuyor.

    Süreç tablosu okunamayan PID **yabancı sayılır** (fail-closed) — mevcut
    davranış korunuyor. Gerekçesi: bilinmeyen bir sürecin portu tuttuğu
    durumda başlatmayı denemek uvx'in sessizce ölmesiyle sonuçlanıyor.
    """
    self_pid = os.getpid()
    infos: List[ProcessInfo] = []
    for pid in list_listening_pids(port):
        if pid == self_pid or is_descendant_of(pid, self_pid):
            continue
        info = query_process(pid)
        if info is not None and SERVER_COMMAND_MARKER in info.command:
            continue
        infos.append(info if info is not None else ProcessInfo(pid=pid, ppid=None, command=""))
    return infos


def foreign_port_owners(port: int = DEFAULT_MCP_PORT) -> List[int]:
    """Portu tutan ve bize ait OLMAYAN PID'ler (hiçbir şey öldürmez).

    `start_server` bunu ön kontrol olarak kullanıyor: aksi halde uvx bind
    edemeyip sessizce ölüyor, toggle sonsuza kadar sarıda kalıyor ve kullanıcı
    sebebi hiçbir yerde göremiyor.
    """
    return [info.pid for info in foreign_port_owner_infos(port)]
