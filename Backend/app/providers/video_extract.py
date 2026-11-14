"""Video → kareler (data-URI) + transkript. Kendi ffmpeg/yt-dlp sarmalayıcımız;
harici repo/skill YOK. Kareler mevcut görsel hattına (self.images) katılır.

stdlib-only: kare dedup için PIL yerine ffmpeg'in ürettiği 16x16 gri ham baytları
karşılaştırırız (bkz. _frame_signature)."""
import os
import re
import sys
import glob
import time
import uuid
import base64
import shutil
import logging
import tempfile
import threading
import contextvars
import subprocess
from urllib.parse import urlparse
from typing import List, Optional

from providers.video_bin import ffmpeg_path, ytdlp_path, missing_binaries

# `spawn_env` app KÖKÜNDE duruyor, `providers` paketinin içinde değil (bkz. o
# dosyanın başındaki gerekçe). Üretimde `Backend/app` zaten sys.path'te (main.py
# ve testler koyuyor), yani aşağıdaki ekleme orada işlemsizdir; modülün kendi
# yolunu bilmesi, onu paket ağacının nasıl kurulduğundan bağımsız kılıyor —
# `unity_ai_mcp/unity_mcp_manager.py:389` aynı deseni aynı sebeple kullanıyor.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
from spawn_env import build_spawn_env  # noqa: E402  (yukarıdaki yol kurulumundan sonra)

logger = logging.getLogger(__name__)

# Windows: ffmpeg/yt-dlp konsol penceresi flash'ını engelle (non-Windows'ta 0 = etkisiz).
# _frame_signature kare-başına ffmpeg çağırdığı için bu olmadan onlarca pencere yanıp sönerdi.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_TMP_SUBDIR = os.path.join(".gamachine_tmp", "video")
_FRAME_CAP = 60

# ── Kaynak tavanları. Hepsi bir denetim bulgusundan doğdu (30 Ağu 2026): bu
#    boru hattının tek girdisi kullanıcının yapıştırdığı bir URL, ve o URL'nin
#    host'u yanıt gövdesinin BOYUTUNU seçen taraf. Tek sınır 600 sn'lik duvar
#    saatiydi; hızlı bir host o süre içinde diski doldurabiliyordu.

# İndirilen video için bayt tavanı. 1 GiB seçildi çünkü yt-dlp'nin ≤1080p
# seçimi sahada ~5 Mbit/s civarında geliyor, yani 1 GiB kabaca yarım saatlik
# video demek — bu boru hattının kare bütçesi (≤60 kare) zaten bundan uzun
# videoları seyrekleştirerek örnekliyor. BEDELİ açık: daha uzun/daha yüksek
# bit hızlı bir video hiç indirilmez ve tur metne düşer; karşılığında bir URL
# ile diski doldurmak imkânsız hale gelir.
_MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024

# Altyazı dosyasından okunacak KARAKTER tavanı. 512 Ki karakter, yoğun bir
# WebVTT'de birkaç saatlik konuşmaya denk; bunun ötesi transkript değil yüktür.
# BEDELİ: çok uzun bir videonun altyazısı sondan kırpılır (kareler etkilenmez).
_SUBTITLE_CHAR_CAP = 512 * 1024

# Modele giden transkript tavanı. Ayrı bir sayı, çünkü ayrı bir bütçeyi
# koruyor: dosya okuması diski/belleği, bu ise İSTEM bağlamını sınırlıyor.
# 200 Ki karakter ≈ 50 K token, yani en dar bağlam pencerelerinde bile turu
# tek başına doldurmaz. BEDELİ: çok uzun videoda transkriptin kuyruğu kesilir.
_TRANSCRIPT_CHAR_CAP = 200 * 1024

# Süpürmenin "artık kimse kullanmıyor" eşiği. 6 saat, en uzun tek çağrının
# (600 sn indirme + 300 sn kare çıkarma) bir düzine katı: canlı bir turun
# dizinini silmemek için geniş bırakıldı. BEDELİ: çöken bir süreçten kalan
# dizin en fazla 6 saat + bir sonraki video turuna kadar diskte durur.
_STALE_TEMP_AGE_S = 6 * 3600

# Poll interval of the download size guard (see `_DownloadSizeGuard`). The
# number IS the bound's precision: the guard can only notice a breach on a tick,
# so peak disk = cap + interval × line rate. At 0.5 s that is ~62 MiB on a
# saturated 1 Gbit/s link and ~6 MiB on a typical 100 Mbit/s one — a few percent
# of the 1 GiB cap, i.e. the overshoot does not change the order of the bound.
# COST of going lower: every tick walks the output directory (a stat per file,
# and the frame stage puts up to 180 files there), so the wakeups are not free;
# COST of going higher: the overshoot grows linearly with it.
_DOWNLOAD_POLL_INTERVAL_S = 0.5

# How many failed kills the guard tolerates before it declares the kill LOST —
# logs at error level and starts reclaiming blocks every tick as well. It does
# NOT stop killing at this point; the number only says when a retry stops being
# "probably a race" and becomes "this child is not going away".
# WHY 10: at `_DOWNLOAD_POLL_INTERVAL_S` that is 5 seconds of retries. The
# realistic failure is transient — a race with the OS reaping the child, or a
# momentary handle error — and those clear within a tick or two; ten ticks is an
# order of magnitude more patience than that, while still 1/120 of the 600-second
# download timeout that used to be the only backstop.
# COST of raising it: the loud log and the block reclaim start later, so the
# pathological case spends `attempts × interval × line rate` more disk before
# anything is written down (at 10 that is ~62 MiB on a saturated 1 Gbit/s link).
# COST of lowering it: an error line and a truncate pass for a kill that was
# only a tick away from succeeding — noise in the log and a file truncated under
# a child that was about to die anyway.
_KILL_ATTEMPT_CAP = 10

# Directories a `extract()` in THIS process is currently using, normalised for
# comparison. The sweep consults it so it can never delete live data; see
# `sweep_stale_temp` for what this does NOT cover (other processes).
_LIVE_ROOTS = set()
_LIVE_ROOTS_LOCK = threading.Lock()


def _norm(path: str) -> str:
    """Path in the one form the live-root set compares: absolute + normcase.

    `normcase` matters on Windows, where the same directory reaches us as
    `C:\\Users\\...` from `extract()` and `c:/users/...` from a test or a saved
    workspace; a set keyed on the raw string would then hold two entries and the
    sweep would match neither.
    """
    return os.path.normcase(os.path.abspath(path))


class VideoPipelineError(RuntimeError):
    """Carries WHICH STAGE of the pipeline failed and WHY.

    Why a plain `RuntimeError` was not enough: `_prepare_videos` turned every
    exception into the single text "a video could not be processed", so a
    missing binary and a dead link looked the same to the user — while their
    fixes are entirely different. `code` is the machine-readable name the
    frontend reads; `stage` is the diagnostic step that goes to the log.
    """

    def __init__(self, code: str, message: str, stage: str, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.detail = detail or message

    @property
    def client_detail(self) -> str:
        """The detail that may leave the backend — stage and error kind only.

        `detail` carries whatever the failing step raised, which for a
        subprocess is the whole argv: the resolved binary path, the Windows home
        directory (so the account name), the workspace path, the per-turn temp
        path, and the target URL WITH its query string — an access token in a
        share link lands in the renderer and in the chat history.

        The route layer already refuses to send raw exception text to the client
        for the same reason (`/chat-stream`'s error branch); an audit found this
        new channel going around that decision, so the raw text stays in the log
        and only this reaches the user.
        """
        kind = self.detail.split(":", 1)[0].strip() if ":" in self.detail else ""
        # Only an exception class name is kept — anything else can carry a path.
        if not kind.isidentifier():
            kind = ""
        return f"aşama: {self.stage}" + (f" · {kind}" if kind else "")


class ExtractionCancelled(RuntimeError):
    """The caller abandoned this extraction; the worker unwinds instead of
    finishing work whose result nobody is waiting for."""


class ExtractionCancel:
    """One in-flight `extract()`'s stop switch, held by the ASYNC caller.

    Why the caller needs one (audit, 30 Aug 2026): extraction is blocking work
    handed to `asyncio.to_thread`, and cancelling the awaiting coroutine does
    not reach the thread. The worker stayed parked inside `subprocess.run`
    with its temp directory live until yt-dlp's own 600-second timeout expired,
    so a user who stopped the chat kept paying for the download; repeated
    cancelled turns pile up executor threads, bandwidth and temp trees.

    The handle carries the LIVE child processes, and that is the whole point:
    a cancellation that returns to the caller while an orphaned yt-dlp keeps
    downloading has moved the leak, not closed it.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._procs = set()
        self._tmp_root: Optional[str] = None

    @property
    def tmp_root(self) -> Optional[str]:
        return self._tmp_root

    @tmp_root.setter
    def tmp_root(self, path: Optional[str]) -> None:
        """Assignment also PUBLISHES the directory as live (audit, 30 Aug 2026).

        The stale sweep decided liveness from directory mtime alone, so a run
        that was still downloading but had started more than `_STALE_TEMP_AGE_S`
        ago had its directory deleted out from under its own worker — a cleanup
        step that destroys live data is worse than the leak it was added to fix.

        The registration hangs off the setter rather than off `extract()` so
        that there is no second call site that can forget it: this handle is by
        construction the one object that knows which directory a live run owns,
        and it learns it exactly here.
        """
        with _LIVE_ROOTS_LOCK:
            if self._tmp_root:
                _LIVE_ROOTS.discard(_norm(self._tmp_root))
            self._tmp_root = path
            if path:
                _LIVE_ROOTS.add(_norm(path))

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def _attach_process(self, proc) -> bool:
        """Registers a just-started child; False means cancel already won."""
        with self._lock:
            if self._cancelled:
                return False
            self._procs.add(proc)
            return True

    def _detach_process(self, proc) -> None:
        with self._lock:
            self._procs.discard(proc)

    def raise_if_cancelled(self, stage: str) -> None:
        if self.cancelled:
            raise ExtractionCancelled(f"video çıkarımı iptal edildi (aşama: {stage})")

    def kill_running(self, log_failures: bool = True) -> int:
        """Kills the live children WITHOUT declaring the turn cancelled.

        The size guard needs exactly this and nothing more: a download that
        broke the byte cap is a FAILURE of that download, not the user's stop,
        and marking the handle cancelled would report it as `ExtractionCancelled`
        — the pipeline would then skip the video silently instead of telling the
        user their link was too large.

        Returns how many children the kill did NOT reach, and logs each one.
        Both halves come from a verification finding (30 Aug 2026): the body was
        `try: p.kill() except Exception: pass`, so a failed kill was invisible
        AND indistinguishable from a successful one. Its only caller that cares
        (`_DownloadSizeGuard`) then retired after the single attempt, and the
        download it was supposed to bound kept writing until yt-dlp's own
        600-second timeout. A caller cannot react to a failure it cannot see.

        Still swallowing the exception rather than raising it: the two callers
        are a watcher thread and `cancel()` on the event-loop thread, and
        neither can do anything useful with a raised error — but the count lets
        the watcher retry and the log says what happened.

        `log_failures=False` keeps the COUNT and drops the per-child line. The
        retrying caller sets it once it has already written the situation down:
        a retry every `_DOWNLOAD_POLL_INTERVAL_S` for the 600-second download
        timeout is 1200 identical error lines, which buries the one line that
        carries the diagnosis. Nothing is lost — the counters still reach the
        error raised at the end of the download.
        """
        with self._lock:
            procs = list(self._procs)
        unreached = 0
        for p in procs:
            try:
                p.kill()
            except Exception as e:
                unreached += 1
                if log_failures:
                    # pid and exception type are the two facts that separate the
                    # diagnoses here (already-reaped race vs. permission vs. a
                    # wedged handle); the bare `pass` gave neither.
                    logger.error(
                        "[video_extract] alt süreç öldürülemedi (pid=%s): %s: %s",
                        getattr(p, "pid", "?"), type(e).__name__, e)
        return unreached

    def cancel(self) -> None:
        """Stops the extraction. Safe to call from the event-loop thread."""
        with self._lock:
            self._cancelled = True
            tmp_root = self._tmp_root
        self.kill_running()
        # The directory is dropped HERE and not left to the worker's own
        # `finally`: that block only runs once the killed child has been
        # reaped and the stack unwound, and on a stuck child that is the very
        # wait we are cancelling out of. `ignore_errors` covers the Windows
        # case where a dying child still holds a file open — the worker's
        # `finally` is the second, idempotent pass over the same path.
        if tmp_root:
            shutil.rmtree(tmp_root, ignore_errors=True)


# The handle of the extraction running on THIS thread. A ContextVar rather than
# a parameter threaded through `_run`: `_run`'s signature is monkeypatched by
# several tests, and `asyncio.to_thread` copies the context per call, so a
# worker cannot see a previous turn's handle through the reused pool thread.
_CANCEL: "contextvars.ContextVar[Optional[ExtractionCancel]]" = contextvars.ContextVar(
    "video_extract_cancel", default=None)


class ExtractResult:
    def __init__(self, frame_data_uris: List[str], transcript: str, meta: dict):
        self.frame_data_uris = frame_data_uris
        self.transcript = transcript
        self.meta = meta


# ── Saf yardımcılar (subprocess'siz, kolay test edilir) ─────────────────────
def budget_fps(duration_s: float):
    """(fps, cap) — süreye göre hedef kare yoğunluğu (claude-video mantığının sadeleştirmesi)."""
    d = max(1.0, float(duration_s or 1.0))
    if d <= 30:
        return (1.0, _FRAME_CAP)
    if d <= 180:
        return (0.5, _FRAME_CAP)
    if d <= 600:
        return (0.2, _FRAME_CAP)
    return (max(0.01, min(0.05, _FRAME_CAP / d)), _FRAME_CAP)


def parse_vtt(vtt_text: str) -> str:
    """WebVTT → düz, zaman-damgalı transkript. Boş/duplike/tag'li satırlar temizlenir."""
    if not vtt_text:
        return ""
    out, last, cur_ts = [], None, None
    for ln in vtt_text.splitlines():
        s = ln.strip()
        if not s or s == "WEBVTT" or s.startswith(("NOTE", "Kind:", "Language:")):
            continue
        m = re.match(r"(\d{2}:\d{2}:\d{2})[.,]\d{3}\s*-->", s)
        if m:
            cur_ts = m.group(1)
            continue
        if "-->" in s:
            continue
        txt = re.sub(r"<[^>]+>", "", s).strip()
        if txt and txt != last:
            out.append(f"[{cur_ts}] {txt}" if cur_ts else txt)
            last = txt
    return "\n".join(out)


def _mad(a: bytes, b: bytes) -> float:
    """Ortalama-mutlak-fark (0-255). Uzunluk uymazsa 'çok farklı' say."""
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(a[i] - b[i]) for i in range(len(a))) / len(a)


def dedup_indices(signatures: List[bytes], threshold: float = 2.0) -> List[int]:
    """Ardışık neredeyse-aynı imzaları eler; TUTULACAK index listesi döner."""
    kept, prev = [], None
    for i, sig in enumerate(signatures):
        if prev is None or _mad(prev, sig) >= threshold:
            kept.append(i)
            prev = sig
    return kept


def frames_to_data_uris(paths: List[str]) -> List[str]:
    """JPEG dosyalarını 'data:image/jpeg;base64,...' listesine çevirir (okunamayan atlanır)."""
    uris = []
    for p in paths:
        try:
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            uris.append(f"data:image/jpeg;base64,{b64}")
        except Exception as e:
            logger.warning(f"[video_extract] kare okunamadı ({p}): {e}")
    return uris


def build_video_block(meta: dict, transcript: str) -> str:
    """Ajana 'bunlar bir videonun kronolojik kareleri' diyen bağlam bloğu."""
    name = meta.get("name") or "video"
    n = meta.get("frame_count", 0)
    dur = meta.get("duration_s")
    dropped = meta.get("dropped", 0)
    head = (f"\n\n[VİDEO] Kullanıcı bir video ekledi ('{name}'"
            + (f", ~{int(dur)} sn" if dur else "")
            + f"). Aşağıda bu videodan çıkarılmış {n} kare GÖRSEL olarak kronolojik "
            "sırada ekli — bunları tek tek resim değil, bir video dizisi gibi yorumla.")
    if dropped:
        head += f" (Not: {dropped} benzer kare token tasarrufu için atlandı.)"
    if transcript:
        head += f"\n\n[VİDEO TRANSKRİPTİ]\n{transcript}"
    return head + "\n"


# ── Mesajdan video URL'si otomatik yakalama (ayrı UI yok) ───────────────────
_VIDEO_HOSTS = (
    "youtube.com", "youtu.be", "vimeo.com", "loom.com", "tiktok.com",
    "twitter.com", "x.com", "instagram.com", "dailymotion.com",
    "twitch.tv", "streamable.com", "facebook.com", "reddit.com", "bilibili.com",
)
_URL_RE = re.compile(r"https?://[^\s<>\"')]+")


def detect_video_urls(text: str, cap: int = 4) -> List[str]:
    """Mesaj metnindeki BİLİNEN video-host URL'lerini bulur (kullanıcı linki doğrudan
    chate yapıştırınca çekilsin diye). Rastgele/bilinmeyen host'lar yok sayılır →
    gereksiz indirme denemesi olmaz. Dedup + en fazla `cap` URL."""
    if not text:
        return []
    seen, out = set(), []
    for raw in _URL_RE.findall(text):
        url = raw.rstrip(".,);]'\"")
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            continue
        if host.startswith("www."):
            host = host[4:]
        if any(host == h or host.endswith("." + h) for h in _VIDEO_HOSTS):
            if url not in seen:
                seen.add(url)
                out.append(url)
                if len(out) >= cap:
                    break
    return out


# ── Orkestrasyon (ffmpeg / yt-dlp subprocess) ───────────────────────────────
def _attach_root(workspace: Optional[str]) -> str:
    """Per-video temp files go under the workspace; the fallback is the OS temp
    directory, never the process current directory.

    Audit finding (30 Aug 2026): the fallback used to be the literal `"."`.
    With a saved workspace that is empty, gone or no longer a directory, a
    pasted URL therefore wrote into whatever directory the backend happened to
    be started from — the install directory in a packaged build (often
    unwritable, so the video path failed for a reason the message never named)
    and the source tree in development. The OS temp directory is the one
    location that is writable by definition and that the OS itself sweeps.

    The rejection is logged rather than swallowed: silently writing somewhere
    else than the caller asked for is what made this hard to see.
    """
    if workspace and os.path.isdir(workspace):
        return os.path.join(workspace, _TMP_SUBDIR)
    if workspace:
        logger.warning(
            "[video_extract] çalışma alanı kullanılamadı (%r bir dizin değil); "
            "geçici dosyalar işletim sistemi temp dizinine yazılıyor", workspace)
    return os.path.join(tempfile.gettempdir(), _TMP_SUBDIR)


def sweep_stale_temp(workspace: Optional[str] = None,
                     max_age_s: float = _STALE_TEMP_AGE_S) -> int:
    """Removes per-video temp directories an EARLIER run left behind.

    Audit finding (30 Aug 2026): cleanup lived only in `extract()`'s `finally`,
    which a crash, a `kill`, or a power loss walks straight past — the measured
    result was a `<workspace>/.gamachine_tmp/video/<tag>_<uuid>` tree, with
    whatever media had already been written into it, surviving the backend
    forever. Nothing swept it, so repeated crashes consumed workspace disk with
    no upper bound.

    Age-gated instead of "delete everything in there": a second backend process
    (or another turn in this one) may be mid-extraction, and taking its
    directory away would break a live turn to reclaim a few megabytes.
    Returns how many directories were removed, so callers can log it.

    Age alone was NOT enough, and this is the second audit finding on this
    function (30 Aug 2026): a long extraction older than the threshold was
    deleted while its own worker was still writing into it. Two guards answer
    that, and they cover different halves:

      1. `_LIVE_ROOTS` — every directory an `ExtractionCancel` in this process
         owns. Exact, not a heuristic: no live run in this process is touchable.
      2. The newest mtime of the directory's CONTENTS, not just of the directory
         itself. A directory's own mtime only moves when an ENTRY is added or
         removed, so a run that spent an hour growing `dl.mp4.part` still
         carried its creation time — while the file inside was seconds old.
         This is what makes a live run in ANOTHER backend process survive.

    NOT covered, deliberately stated rather than implied closed: a run in
    another process that has written nothing for `max_age_s` (a child hung on a
    dead socket, or a stall between the download and the frame stage) is still
    indistinguishable from a crashed run's leftovers and will be swept. Closing
    that needs a cross-process liveness marker (a pid/heartbeat file the sweep
    validates), which is not in this change. The residual risk is bounded by the
    threshold: at 6 hours, a run must be completely idle for six hours to be
    mistaken for garbage, while a single extraction's own timeouts total 15
    minutes.
    """
    root = _attach_root(workspace)
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    with _LIVE_ROOTS_LOCK:
        live = set(_LIVE_ROOTS)
    now, removed = time.time(), 0
    for name in names:
        path = os.path.join(root, name)
        try:
            if not os.path.isdir(path) or _norm(path) in live:
                continue
            if (now - _newest_mtime(path)) < max_age_s:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    return removed


def _stat_live(path: str) -> os.stat_result:
    """`os.stat` on a path, NEVER `os.DirEntry.stat()`. Measured 30 Aug 2026.

    On Windows `scandir` hands back the size and timestamps cached in the
    directory ENTRY, and Windows does not refresh that entry while a file is
    open — a download that had already written 1.6 MB read as 0 bytes, so the
    size guard below never fired. `os.stat` on the path queries the file itself
    and reports the truth. The cost is a real syscall per file instead of a free
    field read; these directories hold a couple of hundred files at most.
    """
    return os.stat(path)


def _newest_mtime(path: str) -> float:
    """Most recent mtime of `path` or of anything directly inside it.

    Entries are flat here by construction (`dl.*`, `dl*.vtt`, `frame_*.jpg`), so
    one listing sees everything; no recursive walk is needed and the cost stays
    one stat per file.
    """
    newest = os.path.getmtime(path)
    try:
        names = os.listdir(path)
    except OSError:
        return newest
    for name in names:
        try:
            newest = max(newest, _stat_live(os.path.join(path, name)).st_mtime)
        except OSError:
            continue
    return newest


def _dir_size(path: str) -> int:
    """Bytes currently on disk directly under `path` (missing path → 0).

    Counts EVERY entry, not just the finished outputs: yt-dlp writes into
    `dl.mp4.part`, and a size check that only looked at the final file measured
    zero for the whole download — which is precisely when the disk is filling.
    """
    total = 0
    try:
        names = os.listdir(path)
    except OSError:
        return 0
    for name in names:
        try:
            total += _stat_live(os.path.join(path, name)).st_size
        except OSError:
            continue
    return total


def _reclaim_dir(path: str) -> int:
    """Truncates everything under `path` to zero; returns how many files.

    Called when a download is rejected for size. The tree itself is removed by
    `extract()`'s `finally` (and by the sweep after a crash), but that happens
    only once the stack unwinds — while the point of the byte cap is that the
    blocks come back NOW, before the caller does anything else.

    Truncate rather than unlink because of the Windows case this pipeline hits
    constantly: the yt-dlp we just killed may still hold the file open for a
    moment, and `os.remove` on an open file raises `PermissionError` there while
    a truncate is at least attempted on the same handle. Both are best-effort —
    the removal in `finally` is the backstop for whatever this could not touch.
    """
    reclaimed = 0
    try:
        with os.scandir(path) as it:
            entries = [e.path for e in it if e.is_file()]
    except OSError:
        return 0
    for file_path in entries:
        try:
            os.truncate(file_path, 0)
            reclaimed += 1
        except OSError:
            continue
    return reclaimed


class _DownloadSizeGuard:
    """Kills the downloader once the bytes it has WRITTEN pass the cap.

    Audit finding (30 Aug 2026): the byte cap was only summed after `_run`
    returned, so it bounded what was KEPT, not what was written, and
    `--max-filesize` is advisory — it needs a length the host announced, and a
    chunked response announces none. That is exactly the abuse shape the
    original finding described (a pasted link filling the disk): for the full
    600-second download timeout nothing looked at the disk at all.

    The guard watches the output directory from a side thread and kills the live
    child through the run's `ExtractionCancel`, the same handle the cancellation
    work already established. It does NOT mark the run cancelled — see
    `kill_running` for why that distinction matters to the user's message.

    The bound it gives is `cap + _DOWNLOAD_POLL_INTERVAL_S × line rate`; the
    overshoot per interval is stated at that constant.

    THAT BOUND HOLDS ONLY WHILE THE KILL WORKS, and the first version assumed it
    always does (verification round, 30 Aug 2026): `_watch` killed once and
    `return`ed, while `kill_running` swallowed the failure. One raising `kill()`
    therefore retired the watcher silently and nothing looked at the disk again
    until the 600-second subprocess timeout — measured at 204800 bytes written
    against a 4096-byte cap. The watcher now never retires early:

      · every tick past the cap kills AGAIN, so a kill that failed for a
        transient reason is simply retried a tick later;
      · after `_KILL_ATTEMPT_CAP` failures the situation is written to the log at
        error level (once) and `_reclaim_dir` runs on every tick as well, so the
        blocks the doomed download is holding keep being handed back;
      · `kill_attempts` / `kill_failures` / `kill_lost` are readable by the
        caller, which logs them and puts them in the failure it raises.

    What that leaves UNCOVERED, stated rather than implied closed: against a
    child that can never be killed at all, killing is the only lever we own, and
    the truncation is best-effort — a writer holding the file open can zero-fill
    straight back to its old offset on its next write, so per-tick reclaim
    returns blocks repeatedly instead of capping the logical size. The remaining
    ceiling in that case is `_run`'s own 600-second timeout × line rate, exactly
    as before, but it is now LOUD (an error line per tick-batch and a diagnosis
    in the raised error) instead of silent.
    """

    def __init__(self, out_dir: str, cap: int,
                 interval: float = _DOWNLOAD_POLL_INTERVAL_S):
        self._out_dir = out_dir
        self._cap = cap
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Captured on the calling thread: `_CANCEL` is a ContextVar and the
        # watcher thread would see the default (None) if it read it itself.
        self._handle = _CANCEL.get()
        self.exceeded = False
        self.peak = 0
        # Kill bookkeeping, read by `_download_url` so a lost kill reaches the
        # log with numbers instead of only as a missing side effect.
        self.kill_attempts = 0
        self.kill_failures = 0
        self.kill_lost = False

    def __enter__(self) -> "_DownloadSizeGuard":
        self._thread = threading.Thread(target=self._watch, daemon=True,
                                        name="video-download-size-guard")
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> bool:
        self._stop.set()
        if self._thread is not None:
            # The watcher only ever waits on `_stop` or does one scandir, so a
            # short join is generous; it is bounded so a wedged filesystem
            # cannot hold the turn open.
            self._thread.join(timeout=5)
        return False

    def _watch(self) -> None:
        # `wait` first, not last: at entry the directory is empty by definition
        # (the download has not started), so an immediate scan measures nothing.
        while not self._stop.wait(self._interval):
            size = _dir_size(self._out_dir)
            self.peak = max(self.peak, size)
            if size <= self._cap:
                continue
            if not self.exceeded:
                self.exceeded = True
                logger.warning(
                    "[video_extract] indirme bayt tavanını aştı (%s > %s); "
                    "alt süreç öldürülüyor", size, self._cap)
            if self._handle is None:
                # No handle means no child to kill (a direct construction in a
                # test, or a run without a cancel context). Keep looping anyway:
                # `peak` is then the only record of how far it went, and the
                # post-run gate still refuses the download.
                continue
            self.kill_attempts += 1
            if self._handle.kill_running(log_failures=not self.kill_lost) == 0:
                # The child is either dead or on its way out; stay in the loop
                # so that a download which somehow keeps growing is killed again
                # instead of running unwatched.
                continue
            self.kill_failures += 1
            if self.kill_attempts >= _KILL_ATTEMPT_CAP:
                if not self.kill_lost:
                    self.kill_lost = True
                    logger.error(
                        "[video_extract] boyut tavanını aşan indirme %s denemede "
                        "öldürülemedi (dizin=%r, %s bayt > %s); bloklar her "
                        "turda geri alınmaya çalışılacak, indirme reddedilecek",
                        self.kill_attempts, self._out_dir, size, self._cap)
                _reclaim_dir(self._out_dir)


def _run(cmd: list, timeout: int, check: bool = True):
    """ffmpeg / yt-dlp'yi SÜZÜLMÜŞ bir ortamla başlatır.

    Neden env= zorunlu (dış denetim, 2026-07-29): burası `env=` almadan
    çalışıyordu, yani her ffmpeg karesi ve her yt-dlp indirmesi backend'in
    ortamının TAMAMINI devralıyordu — `LOCAL_APP_TOKEN` (backend'in tek yetki
    kanıtı), `API_KEY_ENCRYPTION_KEY` (DB'deki kullanıcı anahtarlarının
    şifreleme anahtarı) ve export edilmiş vendor anahtarları dahil.
    Kapı testindeki eski muafiyetin gerekçesi "sabit argv, vendor anahtarı
    TÜKETMİYOR" idi; bu yanlış türden bir gerekçe — bir ikilinin anahtarı
    tüketmemesi onu ALMASINI engellemiyor, ve yt-dlp ağa çıkan üçüncü taraf
    bir araç (uzaktaki siteye bakan kodu biz yazmıyoruz).

    Neden aile YOK (`build_spawn_env()` çıplak, `family=` verilmiyor): aile
    katmanı bir sağlayıcının KENDİ kimlik değişkenlerini geçirmek için var
    (ANTHROPIC_API_KEY, OPENAI_API_KEY…). ffmpeg/yt-dlp'nin böyle bir kimliği
    yok; onlara bir aile vermek tam da kapattığımız sızıntıyı geri açardı.
    Taban katman (`_BASE_ENV_ALLOWLIST`) bu iki ikilinin okuduğu işletimsel
    adların hepsini zaten taşıyor ve her biri gerekli:
      · PATH        — yt-dlp altyazıyı vtt'ye çevirmek için (`--convert-subs`)
                      ffmpeg'i kendisi arıyor; ayrıca frozen olmayan kurulumda
                      ikililerin kendisi de PATH'ten çözülüyor (video_bin).
                      (Eskiden burada "video+ses BİRLEŞTİRMEK için" yazıyordu;
                      artık ses hiç indirilmiyor, birleştirme adımı yok.)
      · HOME/XDG_*  — yt-dlp'nin config ve cache dizini (~/.config/yt-dlp,
                      ~/.cache/yt-dlp); Windows'ta USERPROFILE/APPDATA.
      · TMPDIR/TMP/TEMP — yt-dlp parça dosyalarını, PyInstaller ile paketlenmiş
                      ikili de kendini oraya açıyor; düşerse indirme kırılır.
      · HTTP(S)_PROXY / ALL_PROXY / NO_PROXY (+ küçük harfli biçimleri)
                    — kurumsal ağda yt-dlp'nin ağa çıkabildiği TEK yol.
      · SSL_CERT_FILE / SSL_CERT_DIR / REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE
                    — TLS'i kesen ağlarda düşerse her indirme sertifika
                      hatasıyla ölür ve kullanıcının gördüğü mesaj sebebi gizler.
      · LANG/LC_*   — çıktı ve dosya adı kodlaması (`Duration:` ayrıştırması
                      ffmpeg'in stderr'inden okunuyor).
    Bilerek KESİLENLER: LOCAL_APP_TOKEN, API_KEY_ENCRYPTION_KEY ve tüm
    `*_API_KEY`/`*_TOKEN`/`*_SECRET` adları (bkz. spawn_env.py'deki kütük).
    Kırılma yönü ölçüldü: tests/test_video_extract_integration.py hem sızıntıyı
    hem de PATH/HOME'un GEÇTİĞİNİ aynı çağrıda ölçüyor.

    Neden `subprocess.run` DEĞİL (30 Ağu 2026 denetimi, iptal bulgusu): `run`
    kendi `Popen`'ını gizliyor, yani çağıran turu iptal ettiğinde öldürülecek
    bir tutamak KALMIYORDU. Semantik birebir korunuyor (timeout → öldür ve
    `TimeoutExpired`, check → `CalledProcessError`, dönüş `CompletedProcess`);
    tek fark, çocuğun `ExtractionCancel`'a kaydedilmesi.
    """
    handle = _CANCEL.get()
    if handle is not None:
        handle.raise_if_cancelled("alt süreç başlatma")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=_NO_WINDOW, env=build_spawn_env())
    if handle is not None and not handle._attach_process(proc):
        # İptal, Popen ile kayıt arasına sıkıştı: bu çocuğu kimse öldürmeyecek.
        proc.kill()
        proc.communicate()
        handle.raise_if_cancelled("alt süreç başlatma")
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    finally:
        if handle is not None:
            handle._detach_process(proc)
    if handle is not None:
        # İptal çocuğu öldürmüş olabilir; o zaman çıkış kodu bir ARIZA değil,
        # kullanıcının kararıdır ve "indirilemedi" diye raporlanmamalı.
        handle.raise_if_cancelled("alt süreç bitişi")
    if check and proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=out, stderr=err)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _subprocess_stderr(e: Exception) -> str:
    """The failing tool's own stderr, or ``""``.

    `CalledProcessError` and `TimeoutExpired` both carry it; anything else does
    not, and an absent stream must read as empty rather than as a reason.
    """
    raw = getattr(e, "stderr", None)
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    return raw if isinstance(raw, str) else ""


def _tool_error_detail(e: Exception) -> str:
    """`Type: message` plus the tail of the tool's own stderr — FOR THE LOG.

    Measured 30 Aug 2026, and this is the whole reason the function exists:
    `_run` captures stderr, so `CalledProcessError`'s text is only "Command
    '[...]' returned non-zero exit status 1." — argv and nothing else. A real
    field failure ("HTTP Error 403: Forbidden" on the audio stream) therefore
    left NO trace of its cause anywhere; diagnosing it meant reproducing the
    download by hand. The tool knew the answer and we were throwing it away.

    Only the last few lines are kept: yt-dlp writes a progress line per chunk,
    so the whole stream is thousands of lines of noise ending in the reason.
    This string never leaves the backend — `client_detail` still ships only the
    exception class name.
    """
    detail = f"{type(e).__name__}: {e}"
    lines = [ln.strip() for ln in _subprocess_stderr(e).splitlines() if ln.strip()]
    if lines:
        detail += " | stderr: " + " ⏎ ".join(lines[-3:])[:600]
    return detail


# Reason classes worth telling apart, because the advice differs. Everything
# else keeps the generic message — a wrong specific reason is worse than an
# honest vague one, which is exactly what the single old message risked: it
# blamed "dead link / geo-block / firewall" for a 403 that was none of those.
_REFUSED_RE = re.compile(r"http error 40[13]|forbidden|sign in to confirm", re.I)
_GONE_RE = re.compile(r"private video|members[- ]only|video unavailable|"
                      r"removed by the uploader|has been terminated", re.I)
# "available in your country" bilerek geniş: yt-dlp'nin gerçek cümlesi
# "has not made this video available in your country" ve ilk yazılan dar desen
# ("not available in your country") onu KAÇIRIYORDU — testte yakalandı.
_GEO_RE = re.compile(r"available in your country|blocked it in your country|geo[- ]restrict", re.I)


def pick_text(language: str, tr: str, en: str) -> str:
    """The Turkish or English twin of a user-facing video message."""
    return en if language == "en" else tr


def download_failure_message(stderr_text: str, language: str = "tr") -> str:
    """User-facing sentence for a failed download, chosen from the tool's stderr."""
    if _GEO_RE.search(stderr_text):
        return pick_text(
            language,
            "Video bu ülkeden izlenemiyor (yayıncı bölgesel kısıtlama koymuş); "
            "video atlandı, sohbet metinle sürüyor.",
            "The video cannot be watched from this country (the publisher set a regional "
            "restriction); the video was skipped and the chat continues with text.")
    if _GONE_RE.search(stderr_text):
        return pick_text(
            language,
            "Video artık açık değil (özel, üyelere özel ya da kaldırılmış); "
            "video atlandı, sohbet metinle sürüyor.",
            "The video is no longer available (private, members-only or removed); "
            "the video was skipped and the chat continues with text.")
    if _REFUSED_RE.search(stderr_text):
        return pick_text(
            language,
            "Video sitesi indirmeyi reddetti (403) — genelde geçici bir kısıtlama, "
            "biraz sonra yeniden denemek işe yarayabilir; video atlandı, "
            "sohbet metinle sürüyor.",
            "The video site refused the download (403) - usually a temporary restriction, "
            "trying again a little later may work; the video was skipped and the chat "
            "continues with text.")
    return pick_text(
        language,
        "Videonun linki indirilemedi (link kapalı, bölgesel kısıtlı ya da "
        "ağ engelli olabilir); video atlandı, sohbet metinle sürüyor.",
        "The video link could not be downloaded (it may be private, region-locked or "
        "blocked); the video was skipped and the chat continues with text.")


def _probe_duration(video_path: str) -> float:
    """ffprobe'a gerek kalmadan 'ffmpeg -i' stderr'inden süreyi ayıklar."""
    try:
        r = _run([ffmpeg_path(), "-hide_banner", "-i", video_path], timeout=30, check=False)
        err = (r.stderr or b"").decode("utf-8", "ignore")
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", err)
        if m:
            h, mi, s, cs = map(int, m.groups())
            return h * 3600 + mi * 60 + s + cs / 100.0
    except Exception:
        pass
    return 0.0


def _extract_frames(video_path: str, out_dir: str, fps: float, cap: int) -> List[str]:
    """fps-örnekleme + 640px ölçekle. Dedup sonrası cap'e ineceğimiz için cap*3 ham çeker."""
    out_pat = os.path.join(out_dir, "frame_%04d.jpg")
    _run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", video_path,
          "-vf", f"fps={fps},scale=640:-2", "-frames:v", str(cap * 3), "-q:v", "4", out_pat],
         timeout=300)
    return sorted(glob.glob(os.path.join(out_dir, "frame_*.jpg")))


def _frame_signature(frame_path: str) -> bytes:
    """ffmpeg ile 16x16 gri ham bayt (256B) imza — PIL'siz dedup."""
    try:
        r = _run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", frame_path,
                  "-vf", "scale=16:16,format=gray", "-f", "rawvideo", "-"], timeout=20)
        return (r.stdout or b"")[:256]
    except Exception:
        return b""


def _download_url(url: str, out_dir: str):
    """yt-dlp: ≤720p video + altyazı (varsa). (video_path, transcript, name) döner.
    GÜVENLİK: url kullanıcıdan gelir → önce http/https şeması doğrulanır, sonra '--' ile
    yt-dlp seçenek-ayrıştırması bitirilir. Bu, '-'/'--exec' gibi bir 'URL'nin flag olarak
    sızıp komut çalıştırmasını (argv flag-smuggling / RCE) engeller."""
    p = urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("desteklenmeyen video URL'si (yalnız http/https)")
    logger.info(f"[video_extract] aşama=indirme başlıyor ({p.netloc})")
    # Dizin BURADA açılıyor, `extract`'in başında değil: ilk baytın yazılacağı
    # ana kadar ortada silinecek bir dizin olmasın (süpürme, çöken bir süreçten
    # kalanı topluyor — hiç yaratılmamış olan en ucuzu).
    os.makedirs(out_dir, exist_ok=True)
    out_tmpl = os.path.join(out_dir, "dl.%(ext)s")
    # 1) VİDEO (kritik yol). Altyazı flag'i YOK — böylece altyazı hatası (429/yok/ağ)
    #    videoyu DÜŞÜRMEZ. Format yatay VE dikey (Shorts) için sağlam: 1080'i her iki
    #    yönde dener, sonra best'e düşer. ('mp4[height<=720]' dikey Shorts'ta -height 1280-
    #    ve ayrı-stream'li modern YouTube'da eşleşmeyip exit-1 veriyordu.)
    #
    # SES BİLEREK İSTENMİYOR (`+bestaudio` kaldırıldı, 30 Ağu 2026). Bu boru hattı
    # sesi HİÇ tüketmiyor: kareler görüntüden, transkript altyazıdan geliyor —
    # yani ses akışı indirildiği anda çöpe gidiyordu. Bedeli teorik değil,
    # sahada ölçüldü: bir Shorts'ta görüntü akışı indi ve YouTube SES akışına
    # `HTTP Error 403` döndü; yt-dlp exit 1 verdi ve kullanıcı videonun tamamını
    # kaybetti ("link indirilemedi"). İki URL'de yeniden üretildi ve bu satırla
    # ikisi de düzeldi. Yan kazanç: indirilen bayt yarıya iniyor ve ayrı akış
    # kalmadığı için ffmpeg birleştirme adımı tamamen ortadan kalkıyor.
    #
    # `--max-filesize`: tek sınır 600 sn duvar saatiydi ve boyutu seçen taraf
    # uzaktaki host'tu — yeterince hızlı bir yanıt o süre içinde diski
    # doldurabiliyordu (denetim, 30 Ağu 2026). Bayrak boyutu ÖNCEDEN bilinen
    # akışları hiç başlatmadan reddeder; bilinmeyenler için aşağıdaki
    # uygulama-tarafı ölçüm var, çünkü Content-Length'i olmayan bir akışta
    # yt-dlp'nin de karşılaştıracağı bir sayı yok.
    # `_DownloadSizeGuard` is the half `--max-filesize` cannot do: it bounds the
    # bytes WRITTEN while the child runs, not just the bytes kept afterwards.
    with _DownloadSizeGuard(out_dir, _MAX_DOWNLOAD_BYTES) as guard:
        try:
            _run([ytdlp_path(), "--no-playlist", "--no-warnings",
                  "-f", "bestvideo[height<=1080]/bestvideo[width<=1080]/bestvideo/best[height<=1080]/best",
                  "--max-filesize", str(_MAX_DOWNLOAD_BYTES),
                  "-o", out_tmpl, "--", url], timeout=600)
        except ExtractionCancelled:
            raise
        except Exception:
            # A child the guard killed exits non-zero (or times out mid-kill).
            # That is our own kill, not the host failing, and it must surface as
            # the size error below — `download_failure_message` would otherwise
            # blame the site for a limit we imposed.
            if not guard.exceeded:
                raise
    # Measured over the whole directory, so a `.part` left by the killed child
    # counts. Both gates stay: the guard bounds the peak, this bounds what is
    # kept when a download completes between two ticks.
    _size = _dir_size(out_dir)
    if guard.exceeded or _size > _MAX_DOWNLOAD_BYTES:
        _reclaim_dir(out_dir)
        if guard.kill_lost:
            # The bound degraded to "the subprocess timeout", and that is worth
            # a line of its own: the numbers say how far past the cap it got and
            # how many kills were refused, which is what a report needs.
            logger.error(
                "[video_extract] boyut koruması alt süreci öldüremedi "
                "(%s deneme, %s başarısız); tepe %s bayt, tavan %s",
                guard.kill_attempts, guard.kill_failures, guard.peak,
                _MAX_DOWNLOAD_BYTES)
        raise RuntimeError(
            f"video boyut sınırını aştı ({max(_size, guard.peak)} bayt > "
            f"{_MAX_DOWNLOAD_BYTES})"
            + (f"; alt süreç {guard.kill_attempts} denemede öldürülemedi"
               if guard.kill_lost else ""))
    vids = [q for q in glob.glob(os.path.join(out_dir, "dl.*")) if not q.endswith(".vtt")]
    if not vids:
        raise RuntimeError("video indirilemedi (yt-dlp çıktısı yok)")
    # 2) ALTYAZI (best-effort, AYRI çağrı). check=False + try/except: 429/eksik/ağ
    #    hatası yutulur → transkript alınamasa bile kareler yine döner.
    transcript = ""
    try:
        logger.info("[video_extract] aşama=altyazı başlıyor")
        _run([ytdlp_path(), "--no-playlist", "--no-warnings", "--skip-download",
              "--write-auto-subs", "--write-subs", "--sub-langs", "en.*,tr.*",
              "--convert-subs", "vtt", "-o", out_tmpl, "--", url], timeout=120, check=False)
        subs = glob.glob(os.path.join(out_dir, "dl*.vtt"))
        if subs:
            # SINIRLI okuma. Eskiden `f.read()` idi ve altyazının içeriğini de,
            # boyutunu da uzaktaki host seçiyordu: tek URL dosyayı, çözülmüş
            # dizgeyi, `splitlines()` ara ürünlerini ve büyümüş İSTEMİ birden
            # ödetiyordu (denetim, 30 Ağu 2026 — 2 MiB'lık bir WebVTT ölçüldü).
            # İki ayrı tavan, çünkü iki ayrı bütçe: biri belleği, diğeri modele
            # giden bağlamı koruyor.
            with open(subs[0], encoding="utf-8", errors="ignore") as f:
                raw = f.read(_SUBTITLE_CHAR_CAP)
            transcript = parse_vtt(raw)[:_TRANSCRIPT_CHAR_CAP]
    except ExtractionCancelled:
        raise
    except Exception as e:
        # Deliberately swallowed (frames are useful without a transcript) but no
        # longer SILENT: which stage fell over is now readable from the log.
        logger.warning(f"[video_extract] aşama=altyazı başarısız (kareler yine de sürecek): {e}")
    return vids[0], transcript, os.path.basename(url) or "url-video"


def extract(source: dict, workspace: Optional[str], tag: str,
            cancel: "Optional[ExtractionCancel]" = None,
            language: str = "tr") -> ExtractResult:
    """Video kaynağını kare data-URI'leri + transkripte çevirir. Kendi temp'ini temizler.

    `cancel`: the async caller's stop switch (see `ExtractionCancel`). Optional
    so the synchronous call sites and tests stay unchanged; when it is absent a
    private one is used, which keeps `_run`'s cancellation path on a single
    code path instead of two.
    """
    kind = (source or {}).get("kind")
    # Süpürme HER çıkarımın başında: çöken bir sürecin bıraktığı dizin ancak
    # bir sonraki koşuda toplanabilir (kill edilen süreç hiçbir şey çalıştıramaz).
    # Burada, çünkü bu modülün "bir sonraki koşusu" tam olarak burasıdır ve
    # backend başlangıcına bağlamak hiç yeniden başlatılmayan bir süreçte
    # süpürmeyi hiç çalıştırmaz.
    try:
        _swept = sweep_stale_temp(workspace)
        if _swept:
            logger.info(f"[video_extract] {_swept} bayat geçici dizin süpürüldü")
    except Exception as e:                       # süpürme asıl işi DÜŞÜRMEZ
        logger.warning(f"[video_extract] bayat temp süpürme başarısız: {e}")
    tmp_root = os.path.join(_attach_root(workspace), f"{tag}_{uuid.uuid4().hex[:8]}")
    cancel = cancel or ExtractionCancel()
    cancel.tmp_root = tmp_root
    _token = _CANCEL.set(cancel)
    try:
        # Binary gate goes FIRST: diagnosing a missing ffmpeg/yt-dlp backwards
        # from the subprocess failure was near impossible (the error text is
        # generic, "the system cannot find the file specified"). Here the NAME of
        # what is missing is known and can be shown to the user.
        _missing = missing_binaries(need_ytdlp=(kind == "url"))
        if _missing:
            _ad = " ve ".join(_missing)
            raise VideoPipelineError(
                "video_binary_missing",
                pick_text(
                    language,
                    f"Videoyu işlemek için gereken {_ad} programı bu bilgisayarda bulunamadı; "
                    "video atlandı, sohbet metinle sürüyor.",
                    f"The program needed to process the video ({_ad.replace(' ve ', ' and ')}) "
                    "was not found on this computer; the video was skipped and the chat "
                    "continues with text."),
                stage="binary_resolve",
                detail=f"çözülemeyen binary: {', '.join(_missing)}")

        transcript = ""
        if kind == "path":
            video_path = source.get("path")
            if not video_path or not os.path.isfile(video_path):
                raise VideoPipelineError(
                    "video_download_failed",
                    pick_text(language, "Eklenen video dosyası bulunamadı.",
                              "The attached video file was not found."),
                    stage="kaynak", detail=f"video bulunamadı: {video_path}")
            # GÜVENLİK: mutlak yola normalize et → ffmpeg '-i' argümanı '-' ile başlayıp
            # flag gibi yorumlanamaz (argv flag-smuggling savunması).
            video_path = os.path.abspath(video_path)
            name = source.get("name") or os.path.basename(video_path)
        elif kind == "url":
            try:
                video_path, transcript, name = _download_url(source.get("url") or "", tmp_root)
            except VideoPipelineError:
                raise
            except ExtractionCancelled:
                # İptal bir indirme ARIZASI değil: kullanıcının kararı. Buradan
                # geçerse "video indirilemedi" uyarısı üretilir ve iptal edilmiş
                # bir tur kullanıcıya hata gibi görünür.
                raise
            except Exception as e:
                raise VideoPipelineError(
                    "video_download_failed",
                    download_failure_message(_subprocess_stderr(e), language),
                    stage="indirme", detail=_tool_error_detail(e)) from e
        else:
            raise VideoPipelineError(
                "video_extract_failed",
                pick_text(language, "Bu video kaynağı tanınmadı.",
                          "This video source was not recognized."),
                stage="kaynak", detail=f"bilinmeyen video kaynağı: {kind!r}")

        cancel.raise_if_cancelled("kare çıkarma öncesi")
        try:
            logger.info("[video_extract] aşama=süre ölçümü")
            duration = _probe_duration(video_path)
            fps, cap = budget_fps(duration)
            logger.info(f"[video_extract] aşama=kare çıkarma (süre≈{duration:.0f}s, fps={fps})")
            os.makedirs(tmp_root, exist_ok=True)     # yerel dosya yolunda ilk yazım burası
            raw = _extract_frames(video_path, tmp_root, fps, cap)
        except ExtractionCancelled:
            raise
        except Exception as e:
            raise VideoPipelineError(
                "video_extract_failed",
                pick_text(language,
                          "Videodan görüntü çıkarılamadı; video atlandı, sohbet metinle sürüyor.",
                          "Frames could not be extracted from the video; the video was "
                          "skipped and the chat continues with text."),
                stage="kare çıkarma", detail=_tool_error_detail(e)) from e
        if not raw:
            raise VideoPipelineError(
                "video_extract_failed",
                pick_text(language,
                          "Videodan hiç görüntü çıkmadı (dosya bozuk ya da desteklenmeyen bir "
                          "biçimde olabilir); video atlandı, sohbet metinle sürüyor.",
                          "No frames came out of the video (the file may be corrupt or in an "
                          "unsupported format); the video was skipped and the chat continues "
                          "with text."),
                stage="kare çıkarma", detail="ffmpeg 0 kare üretti")
        sigs = [_frame_signature(p) for p in raw]
        keep = dedup_indices(sigs, threshold=2.0)
        kept = [raw[i] for i in keep][:cap]
        dropped = len(raw) - len(kept)
        uris = frames_to_data_uris(kept)
        if dropped:
            logger.info(f"[video_extract] {name}: {len(raw)} ham → {len(uris)} kare ({dropped} atlandı)")
        meta = {"name": name, "duration_s": duration,
                "frame_count": len(uris), "dropped": max(0, dropped)}
        return ExtractResult(uris, transcript, meta)
    finally:
        _CANCEL.reset(_token)
        shutil.rmtree(tmp_root, ignore_errors=True)
        # Unpublish before returning: a handle that keeps a directory registered
        # after its run ended would make the sweep skip that path for the rest
        # of the process's life — the leak this whole file exists to prevent.
        cancel.tmp_root = None
