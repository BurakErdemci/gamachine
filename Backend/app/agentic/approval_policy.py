"""OpenCode MCP çağrıları için yalnız aktif tur boyunca geçerli onay politikası.

OpenCode'un proje içindeki ``opencode.json`` dosyası kalıcıdır. Bu nedenle Auto
mod bilgisini doğrudan o dosyaya yazmak, uygulama turu bittikten sonra da yetki
bırakır. Buradaki tek kullanımlık anahtar sadece çalışan AgentRunner turu boyunca
geçerlidir; tur bittiğinde anahtar iptal edilir.
"""

from dataclasses import dataclass
import os
import secrets
import threading


@dataclass(frozen=True)
class _ApprovalTurn:
    workspace_path: str
    auto_approve: bool


_ACTIVE_TURNS: dict[str, _ApprovalTurn] = {}
_LOCK = threading.Lock()


def _canonical_workspace(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path or ".")))


def begin_opencode_turn(workspace_path: str, generation_mode: str) -> str:
    """Yeni bir OpenCode turu kaydeder ve tahmin edilemez geçici anahtar döndürür."""
    token = secrets.token_urlsafe(32)
    turn = _ApprovalTurn(
        workspace_path=_canonical_workspace(workspace_path),
        auto_approve=(generation_mode == "auto"),
    )
    with _LOCK:
        _ACTIVE_TURNS[token] = turn
    return token


def end_opencode_turn(token: str | None) -> None:
    """Tur anahtarını iptal eder. Bilinmeyen/boş anahtarlar güvenle yok sayılır."""
    if not token:
        return
    with _LOCK:
        _ACTIVE_TURNS.pop(token, None)


def should_auto_approve(token: str | None, workspace_path: str) -> bool:
    """Anahtar aktif Auto turuna ve tam olarak aynı workspace'e aitse True döner."""
    if not token:
        return False
    with _LOCK:
        turn = _ACTIVE_TURNS.get(token)
    return bool(
        turn
        and turn.auto_approve
        and turn.workspace_path == _canonical_workspace(workspace_path)
    )


# ── Ortam turu — anahtar TAŞIYAMAYAN çağıranlar için ────────────────────────
#
# unityMCP sunucusu ayrı bir süreç ve tek kullanımlık tur anahtarını bilemez:
# anahtar ürünün kendi MCP sürecinde üretiliyor, sunucuya hiç ulaşmıyor. Kapı
# oraya konunca (K1 ADIM 3) anahtarsız gelen her istek "onay iste" olurdu ve
# **Auto mod kart çıkarmaya başlardı** — kullanıcının açıkça istemediği şey:
# *"oto modda hiçbir sıkıntı yok zaten her şeyin otomatik çalışması gerek"*.
#
# Bu yüzden ikinci bir sinyal var: şu an KOŞAN bir turun modu. Anahtarlı yol
# (yukarısı) daha dar ve olduğu gibi duruyor; ortam turu yalnız anahtar
# YOKKEN sorulur.
#
# ⚠️ Dürüst sınır: bu, "bu çağrı gerçekten o turdan geldi" kanıtı DEĞİL. Auto
# bir tur koşarken ürüne bağlı başka bir MCP istemcisi (ör. Cursor) de o
# pencereden geçebilir. Kabul edilebilir olmasının sebebi ölçülmüş: bugün o
# istemcilerde kapı HİÇ YOK ve mutasyonlar sorgusuz geçiyor — yani bu, dar
# olmayan bir kapı değil, hiç olmayan bir kapının yerine gelen dar bir kapı.
# Turun BİTİŞİ sayaçla garanti: `with` bloğu istisna ve generator kapanışında
# da düşüyor, yani "auto turu bitti ama sinyal açık kaldı" hali oluşmuyor.

_AMBIENT_AUTO = 0
_AMBIENT_TOPLAM = 0
# conversation_id -> the mail depth of each turn of that chat running now, one
# entry per turn. Lets /mcp-approval-request accept a card's claimed owner only
# while that chat has a turn in flight, and gives a mail send the depth of the
# turn it comes from. The depth lives with the running turn, not with the chat:
# a per-chat value was reset by a user request arriving between a mail turn's
# route and its stream start (Codex mailverify, 27 Sep 2026).
_TURNS_BY_CONVERSATION: dict[int, list[int]] = {}


class ambient_turn:
    """Koşan turun modunu kaydeder; çıkışta MUTLAKA bırakır.

    Sayaç (bool değil) çünkü aynı anda birden fazla tur koşabiliyor: iç içe ya
    da paralel bir tur bittiğinde diğerininki hâlâ açık kalmalı. Bool olsaydı
    ilk biten, koşmaya devam eden turun sinyalini kapatırdı.

    İKİ sayaç tutuluyor, biri değil — sebebi aşağıda `ambient_auto_approve`'da.
    """

    def __init__(self, workspace_path: str, generation_mode: str,
                 conversation_id: int | None = None, mail_depth: int = 0) -> None:
        self._auto = generation_mode == "auto"
        self._conversation = (
            conversation_id if type(conversation_id) is int and conversation_id > 0 else None
        )
        self._mail_depth = max(0, int(mail_depth or 0))

    def __enter__(self) -> "ambient_turn":
        global _AMBIENT_AUTO, _AMBIENT_TOPLAM
        with _LOCK:
            _AMBIENT_TOPLAM += 1
            if self._auto:
                _AMBIENT_AUTO += 1
            if self._conversation is not None:
                _TURNS_BY_CONVERSATION.setdefault(self._conversation, []).append(
                    self._mail_depth)
        return self

    def __exit__(self, *_exc) -> None:
        global _AMBIENT_AUTO, _AMBIENT_TOPLAM
        with _LOCK:
            _AMBIENT_TOPLAM = max(0, _AMBIENT_TOPLAM - 1)
            if self._auto:
                _AMBIENT_AUTO = max(0, _AMBIENT_AUTO - 1)
            if self._conversation is not None:
                depths = _TURNS_BY_CONVERSATION.get(self._conversation, [])
                if self._mail_depth in depths:
                    depths.remove(self._mail_depth)
                if not depths:
                    _TURNS_BY_CONVERSATION.pop(self._conversation, None)
        return None


def conversation_turn_in_flight(conversation_id: int) -> bool:
    """Is a turn of this conversation running in AgentRunner.run right now?"""
    with _LOCK:
        return bool(_TURNS_BY_CONVERSATION.get(conversation_id))


def conversations_with_turn_in_flight() -> set[int]:
    """Snapshot of every conversation with a turn in AgentRunner.run now."""
    with _LOCK:
        return {cid for cid, depths in _TURNS_BY_CONVERSATION.items() if depths}


def running_mail_depth(conversation_id: int) -> int:
    """Highest mail depth among this conversation's running turns, 0 if none.

    The highest, because a send cannot tell which of two parallel turns of a
    chat it comes from; a user turn beside a depth-2 mail turn therefore
    cannot send either until the mail turn ends.
    """
    with _LOCK:
        return max(_TURNS_BY_CONVERSATION.get(conversation_id) or [0])


def ambient_auto_approve() -> bool:
    """Koşan turların HEPSİ Auto modda mı?

    "En az biri" DEĞİL "hepsi" — ve fark bir açığı kapatıyor. İlk yazımı "en az
    bir auto turu var mı" idi; o hâliyle aynı anda bir auto ve bir step turu
    koşarken, STEP turunun mutasyonları da sessizce oto-onaylanırdı. Yani adım
    modunun kapısı, kullanıcının başka bir sekmede başlattığı ilgisiz bir auto
    turu yüzünden kaybolurdu — kapının varlık sebebini yok eden bir hal.

    Kapı ayrı süreçte olduğu için çağrının HANGİ turdan geldiğini bilemiyoruz;
    bilinemeyen bir şey hakkında en güvenli varsayım, koşanlardan herhangi biri
    onay istiyorsa onay istemektir. Bedeli: auto turu koşarken açılan bir step
    turu, auto turun kartlarını da geri getirir. Bu yön DOĞRU yön — fazladan
    kart bir rahatsızlık, eksik kart bir açık.
    """
    with _LOCK:
        return _AMBIENT_TOPLAM > 0 and _AMBIENT_AUTO == _AMBIENT_TOPLAM
