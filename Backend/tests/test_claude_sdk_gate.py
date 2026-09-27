"""Claude SDK onay kapısı + workspace kapsama regresyon testleri.

Üç ölçülmüş arıza (2026-07-28) burada teste bağlanıyor:

1. `_path_in_workspace` `os.path.abspath` kullanıyordu → symlink çözülmüyordu.
   `ws/link → /disari` kurulumunda workspace dışı bir hedef "içeride" sayılıyor,
   yani "workspace dışına yazımı SORMADAN reddet" dalı %100 atlatılıyordu.
2. Yol tabanlı okuma araçları (`Read`/`Glob`/`Grep`/`LS`/`NotebookRead`)
   workspace kontrolünden ÖNCE auto-allow oluyordu → adım modunda
   `Read("/etc/passwd")` kartsız izin alıyordu.
3. `get_session` yalnız `conversation_id`'ye bakıyordu → workspace A ile açılan
   session, B istendiğinde aynen dönüyordu (model ve effort de sessizce düşüyordu).

Testlerin symlink kurulumu `tempfile.mkdtemp()` ile yapılıyor, `tmp_path` ile
DEĞİL: macOS'ta `/tmp → /private/tmp` bağı yüzünden "yem" dizininin workspace ile
aynı bağ altında olması testi yanlış yeşile düşürebiliyor. Bu yüzden yem dizini
workspace'in DIŞINDA, AYRI bir `mkdtemp()` ile açılıyor.
"""
import asyncio
import inspect
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from agentic.command_gates import APPROVAL_GATES, APPROVAL_RESULTS
from providers import claude_sdk_session as css
from providers.claude_sdk_session import (
    ClaudeSDKSession,
    _glob_literal_root,
    _path_in_workspace,
    _read_tool_target,
    get_session,
)


# ── Ortak kurulum ────────────────────────────────────────────────────────────


@pytest.fixture
def symlink_escape():
    """workspace + workspace DIŞINDA ayrı bir yem dizini + ikisini bağlayan symlink.

    Yem dizini ayrı bir `mkdtemp()` — workspace'in altında ya da aynı geçici
    kökte olsaydı, `/tmp → /private/tmp` çözümü sonrası ikisi de aynı gerçek
    kökün altına düşer ve test "kaçış engellendi" derken aslında hiçbir şey
    ölçmemiş olurdu.
    """
    ws = tempfile.mkdtemp(prefix="ws_")
    outside = tempfile.mkdtemp(prefix="outside_")
    link = os.path.join(ws, "link")
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError, AttributeError):
        # Bağ kurulamıyor (Windows: `SeCreateSymbolicLinkPrivilege` yok →
        # WinError 1314). Fixture'ı DÜŞÜRMEK yerine `link`'i None bırakıyoruz.
        #
        # Sebep ölçüldü (30 Tem 2026): fixture kurulumda patladığında bu
        # dosyanın 40 testi ERROR veriyordu, yani K1 onay kapısının TAMAMI bu
        # makinede ölçülemez haldeydi. Oysa 40 testin yalnız 6'sı bağı
        # kullanıyor; kalanı fixture'ı `ws`/`outside` için alıyor ve ölçtüğü şey
        # izin mantığı, bağ değil. Bağı gerçekten kullanan 6 test
        # `@pytest.mark.baglar_gerekli` ile işaretli — None'a hiç ulaşmıyorlar.
        link = None
    try:
        yield ws, outside, link
    finally:
        shutil.rmtree(ws, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def _mk_session(**kwargs):
    """Bağlanmayan (connect edilmeyen) session — yalnız izin mantığı sınanıyor."""
    s = ClaudeSDKSession(conversation_id=kwargs.pop("cid", 1), **kwargs)
    s._out_q = asyncio.Queue()
    return s


async def _drive_gate(session, tool_name, input_data, *, approve=True, timeout=2.0):
    """`_can_use_tool`'u task olarak koştur; kart çıkarsa yakala ve cevapla.

    Dönen: (izin_sonucu, kart_event'i | None)
    """
    task = asyncio.create_task(session._can_use_tool(tool_name, input_data, None))
    try:
        ev = await asyncio.wait_for(session._out_q.get(), timeout=0.3)
    except asyncio.TimeoutError:
        ev = None  # kart çıkmadı
    if ev is not None and ev.get("type") == "command_approval_needed":
        gid = ev["gate_id"]
        APPROVAL_RESULTS[gid] = approve
        APPROVAL_GATES[gid].set()
    res = await asyncio.wait_for(task, timeout=timeout)
    return res, ev


def _is_allow(res) -> bool:
    from claude_agent_sdk import PermissionResultAllow
    return isinstance(res, PermissionResultAllow)


# ── DÜZELTME 1: canonical kapsama ────────────────────────────────────────────


@pytest.mark.baglar_gerekli


def test_symlink_escape_is_outside_workspace(symlink_escape):
    """Asıl bulgu: bağın altındaki hedef gerçekte workspace dışında."""
    ws, outside, link = symlink_escape
    assert _path_in_workspace(os.path.join(link, "escape.cs"), ws) is False


@pytest.mark.baglar_gerekli


def test_symlinked_workspace_root_still_accepts_its_own_files(symlink_escape):
    """realpath İKİ TARAFA uygulanmalı: kurulumun kendisi bir bağın altında olabilir
    (macOS'ta /tmp → /private/tmp). Yalnız hedefe uygulanırsa meşru kurulum kırılır."""
    ws, outside, _link = symlink_escape
    ws_link = os.path.join(outside, "ws_alias")
    os.symlink(ws, ws_link)
    # Hem yol hem workspace bağ üzerinden verilmiş
    assert _path_in_workspace(os.path.join(ws_link, "A.cs"), ws_link) is True
    # Yol gerçek, workspace bağ üzerinden
    assert _path_in_workspace(os.path.join(ws, "A.cs"), ws_link) is True
    # Yol bağ üzerinden, workspace gerçek
    assert _path_in_workspace(os.path.join(ws_link, "A.cs"), ws) is True


@pytest.mark.baglar_gerekli


def test_nonexistent_target_is_still_classified(symlink_escape):
    """Yeni dosya yazımında hedef HENÜZ YOK — yokluk 'reddet' anlamına gelmemeli,
    ama var olmayan bir bağ-altı yol da 'içeride' sayılmamalı."""
    ws, outside, link = symlink_escape
    assert _path_in_workspace(os.path.join(ws, "Yok", "Derin", "Yeni.cs"), ws) is True
    assert _path_in_workspace(os.path.join(link, "Yok", "Yeni.cs"), ws) is False


def test_sibling_prefix_still_rejected():
    """Eski `commonpath` davranışının koruduğu şey kaybolmasın (Game vs Game_backup)."""
    root = tempfile.mkdtemp(prefix="prefix_")
    try:
        ws = os.path.join(root, "Game")
        sib = os.path.join(root, "Game_backup")
        os.makedirs(ws)
        os.makedirs(sib)
        assert _path_in_workspace(os.path.join(sib, "X.cs"), ws) is False
        assert _path_in_workspace(os.path.join(ws, "X.cs"), ws) is True
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_empty_workspace_means_no_restriction():
    assert _path_in_workspace("/etc/passwd", "") is True


# ── DÜZELTME 2: okuma araçları workspace kontrolünden önce auto-allow ────────


@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": "/etc/passwd"}),
    ("LS", {"path": "/"}),
    ("Glob", {"pattern": "/Users/**/*.key"}),
    ("Grep", {"pattern": "PRIVATE KEY", "path": "/Users"}),
    ("NotebookRead", {"notebook_path": "/etc/secret.ipynb"}),
])
async def test_step_mode_outside_read_shows_card(tool, inp, symlink_escape):
    ws, _outside, _link = symlink_escape
    s = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    res, ev = await _drive_gate(s, tool, inp, approve=True)
    assert ev is not None and ev["type"] == "command_approval_needed", \
        f"{tool} workspace dışını hedeflerken kartsız izin aldı"
    assert _is_allow(res)


async def test_step_mode_outside_read_denied_when_user_rejects(symlink_escape):
    """Kart yalnız görünmesin — kullanıcının HAYIR'ı da etki etsin."""
    ws, _outside, _link = symlink_escape
    s = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    res, ev = await _drive_gate(s, "Read", {"file_path": "/etc/passwd"}, approve=False)
    assert ev is not None
    assert not _is_allow(res)


@pytest.mark.parametrize("tool,inp_key", [
    ("Read", "file_path"),
    ("LS", "path"),
    ("Grep", "path"),
])
async def test_step_mode_inside_read_stays_cardless(tool, inp_key, symlink_escape):
    """Bilerek: workspace içi her okumada kart çıkarmak refleks-onaya alıştırır."""
    ws, _outside, _link = symlink_escape
    s = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    inp = {inp_key: os.path.join(ws, "Assets")}
    if tool == "Grep":
        inp["pattern"] = "foo"
    res, ev = await _drive_gate(s, tool, inp)
    assert ev is None, f"{tool} workspace içi olmasına rağmen kart çıkardı"
    assert _is_allow(res)


@pytest.mark.parametrize("tool,inp", [
    ("Glob", {"pattern": "**/*.cs"}),          # göreli → workspace içi sayılır
    ("Grep", {"pattern": "MonoBehaviour"}),    # path yok → workspace kökü
    ("LS", {}),                                 # argümansız
    ("TodoWrite", {"todos": []}),               # yol tabanlı değil
    ("ToolSearch", {"query": "x"}),             # yol tabanlı değil
])
async def test_pathless_reads_stay_cardless(tool, inp, symlink_escape):
    ws, _outside, _link = symlink_escape
    s = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    res, ev = await _drive_gate(s, tool, inp)
    assert ev is None, f"{tool} yolsuz çağrıda kart çıkardı"
    assert _is_allow(res)


@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": "/etc/passwd"}),
    ("LS", {"path": "/"}),
    ("Glob", {"pattern": "/Users/**/*.key"}),
])
async def test_auto_mode_outside_read_is_free(tool, inp, symlink_escape):
    """Kabul edilmiş taviz (kullanıcı kararı): oto modda workspace dışı serbest."""
    ws, _outside, _link = symlink_escape
    from agentic import approval_mode
    approval_mode.set_mode("auto", source="test")
    s = _mk_session(cwd=ws, auto_approve=True, approval_timeout=2.0)
    res, ev = await _drive_gate(s, tool, inp)
    assert ev is None
    assert _is_allow(res)


@pytest.mark.baglar_gerekli


async def test_symlink_read_escape_shows_card(symlink_escape):
    """Düzeltme 1 ile 2'nin kesişimi: kapsama kontrolü TEK fonksiyondan gelmeli."""
    ws, _outside, link = symlink_escape
    s = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    res, ev = await _drive_gate(s, "Read", {"file_path": os.path.join(link, "gizli.txt")})
    assert ev is not None, "symlink üzerinden workspace dışı okuma kartsız geçti"


@pytest.mark.baglar_gerekli


async def test_write_outside_still_hard_denied(symlink_escape):
    """Yazım dalı davranış değiştirmemeli: sormadan reddediliyor."""
    ws, _outside, link = symlink_escape
    s = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    res, ev = await _drive_gate(s, "Write", {"file_path": os.path.join(link, "x.cs"),
                                             "content": "x"})
    assert not _is_allow(res)
    assert ev is not None and ev["type"] == "tool_result" and ev["success"] is False


# ── DÜZELTME 4: Windows mutlak Glob desenleri kapıyı atlıyordu ───────────────
#
# Dış denetim (2026-07-28) ve mimarın kendi ölçümü:
#
#   Glob C:\Users\**\*.key      -> hedef=None -> KARTSIZ
#   Glob C:/Users/**/*.key      -> hedef=None -> KARTSIZ
#   Glob \\sunucu\pay\**\*.key  -> hedef=None -> KARTSIZ
#   Glob /Users/**/*.key        -> hedef=/Users -> KART   (kontrol, doğruydu)
#
# Sebep: `pat.startswith(("/", "~"))` yalnız POSIX mutlak yolunu tanıyordu; sürücü
# harfi ve UNC tanınmadığı için hedef None'a düşüyor, None da "workspace içi say"
# varsayılanını alıyordu. Yani ürünün ANA PLATFORMUNDA (Windows) bu kapı tümüyle
# atlatılabiliyordu.
#
# Testler `_read_tool_target` seviyesinde, uçtan uca kart seviyesinde DEĞİL — ve
# bu bilinçli: nihai kapsama kararını `_path_in_workspace` veriyor ve o
# platform-duyarlı. macOS'ta `C:\Users` gerçekten göreli bir yoldur, orada kart
# çıkmaması DOĞRU davranıştır; Windows'ta aynı kök mutlak çözülür ve kart çıkar.
# Ölçülebilen ve platformdan bağımsız olan şey şu: desen artık None'a DÜŞMÜYOR,
# yani karar `_path_in_workspace`'e ULAŞIYOR.


@pytest.mark.parametrize("pattern,beklenen_kok", [
    ("C:\\Users\\**\\*.key", "C:\\Users"),
    ("C:/Users/**/*.key", "C:/Users"),
    ("\\\\sunucu\\pay\\**\\*.key", "\\\\sunucu\\pay"),
    ("/Users/**/*.key", "/Users"),          # kontrol: bu zaten çalışıyordu
    ("d:/Gizli/**/*.pem", "d:/Gizli"),      # sürücü harfi küçük de olabilir
])
def test_absolute_glob_patterns_reach_the_workspace_check(pattern, beklenen_kok):
    """Beklenen kök elle yazılı, desenden TÜRETİLMİYOR: beklentisini ölçtüğü
    şeyden üreten bir test hiçbir zaman kırmızı olamaz (bu depoda ölçüldü)."""
    hedef = _read_tool_target("Glob", {"pattern": pattern})
    assert hedef is not None, f"{pattern!r} None'a düştü — 'workspace içi' sayılıyor"
    assert hedef == beklenen_kok


@pytest.mark.parametrize("pattern", [
    "**/*.cs",
    "Assets/**/*.cs",
    "Assets\\Scripts\\**\\*.cs",   # göreli ama Windows ayracıyla
    "*.key",
    "src/**",
])
def test_relative_glob_patterns_stay_cardless(pattern):
    """Karşı yön — bunun ateşlenMEmesi gerekiyor. Göreli desenler gerçekten
    workspace'i tarar; onları 'dışarıda' saymak her aramada kart çıkarır ve
    kullanıcıyı refleks-onaya alıştırır (kapının değerini sıfırlar)."""
    assert _read_tool_target("Glob", {"pattern": pattern}) is None


@pytest.mark.parametrize("pattern,kok", [
    ("/Users/**/*.key", "/Users"),
    ("C:\\Users\\**\\*.key", "C:\\Users"),
    ("/**/*.key", "/"),                       # jokerin önünde yalnız kök var
    ("/etc/passwd", "/etc/passwd"),           # joker yok → desenin tamamı yol
    ("\\\\sunucu\\pay\\gizli.key", "\\\\sunucu\\pay\\gizli.key"),
])
def test_literal_root_understands_both_separators(pattern, kok):
    """`os.sep` ile bölmek macOS'ta `C:\\Users\\**\\*.key`'i HİÇ bölmüyordu →
    kök yanlış çıkardı. Ayraç korunmalı: birleştirmede tek ayraç seçmek UNC
    önekini (`\\\\sunucu\\pay`) bozar."""
    assert _glob_literal_root(pattern) == kok


def test_grep_pattern_is_still_treated_as_a_regex_not_a_path():
    """Karşı yön, ikinci kez: `Grep`'in `pattern`'ı REGEX. `/` ile başlayan bir
    regex'i yol sanmak her aramada kart çıkarırdı."""
    assert _read_tool_target("Grep", {"pattern": "/Users/.*secret"}) is None
    assert _read_tool_target("Grep", {"pattern": "C:\\\\Users\\\\.*"}) is None


def test_other_read_tools_are_untouched_by_the_glob_fix():
    """Regresyon: düzeltme yalnız Glob'un pattern dalını ilgilendiriyor."""
    assert _read_tool_target("Read", {"file_path": "/etc/passwd"}) == "/etc/passwd"
    assert _read_tool_target("LS", {"path": "C:\\Windows"}) == "C:\\Windows"
    assert _read_tool_target("NotebookRead", {"notebook_path": "/x.ipynb"}) == "/x.ipynb"
    assert _read_tool_target("Read", {}) is None
    # Açık `path` verilmişse desen hiç değerlendirilmemeli — tarama orada yapılır.
    assert _read_tool_target("Glob", {"pattern": "/Users/**", "path": "/opt"}) == "/opt"


async def test_the_glob_root_actually_reaches_the_card(symlink_escape):
    """Zincirin uçtan uca kapandığını gösteren yön: kök çıkarılıyor VE kapsama
    kararına gidiyor. Burada bilerek gerçek bir mutlak kök kullanılıyor.

    Neden sürücü harfli desen uçtan uca sınanmıyor: kapsama kararını
    `_path_in_workspace` veriyor ve o platform-duyarlı. macOS'ta `C:\\Users`
    GERÇEKTEN göreli bir yoldur, orada kart çıkmaması doğru davranıştır;
    Windows'ta aynı kök mutlak çözülür ve kart çıkar. Bu testin macOS'ta
    `C:\\Users` için kart beklemesi, doğru düzeltmeyi kırmızı gösterirdi.
    Platformdan bağımsız ölçülebilen kısım yukarıdaki `_read_tool_target`
    testleri: desen artık None'a DÜŞMÜYOR, yani karar kapıya ULAŞIYOR."""
    ws, outside, _link = symlink_escape
    s = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    res, ev = await _drive_gate(s, "Glob", {"pattern": outside + "/**/*.key"}, approve=True)
    assert ev is not None and ev["type"] == "command_approval_needed", \
        "mutlak desenin kökü workspace dışında ama kart çıkmadı"


# ── DÜZELTME 3: session cache anahtarı ───────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_sessions():
    css._SESSIONS.clear()
    yield
    css._SESSIONS.clear()


async def test_workspace_change_rebuilds_session():
    a = tempfile.mkdtemp(prefix="wsA_")
    b = tempfile.mkdtemp(prefix="wsB_")
    try:
        s1 = get_session(7, cwd=a, model="claude-x", effort="high")
        s2 = get_session(7, cwd=b, model="claude-x", effort="high")
        assert s1 is not s2, "workspace değişti ama eski session döndü"
        assert s2.cwd == b
    finally:
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


@pytest.mark.baglar_gerekli


async def test_same_workspace_via_symlink_reuses_session(symlink_escape):
    """Karşılaştırma canonical olmalı: aynı dizinin bağ alias'ı 'değişti' saymamalı."""
    ws, outside, _link = symlink_escape
    alias = os.path.join(outside, "ws_alias")
    os.symlink(ws, alias)
    s1 = get_session(8, cwd=ws, model="claude-x", effort="high")
    s2 = get_session(8, cwd=alias, model="claude-x", effort="high")
    assert s1 is s2, "aynı workspace'in bağ alias'ı gereksiz yere session'ı sıfırladı"


async def test_model_change_rebuilds_session():
    a = tempfile.mkdtemp(prefix="wsM_")
    try:
        s1 = get_session(9, cwd=a, model="claude-x", effort="high")
        s2 = get_session(9, cwd=a, model="claude-BASKA", effort="high")
        assert s1 is not s2
        assert s2.model == "claude-BASKA"
    finally:
        shutil.rmtree(a, ignore_errors=True)


async def test_effort_change_rebuilds_session():
    a = tempfile.mkdtemp(prefix="wsE_")
    try:
        s1 = get_session(10, cwd=a, model="claude-x", effort="high")
        s2 = get_session(10, cwd=a, model="claude-x", effort="low")
        assert s1 is not s2
        assert s2.effort == "low"
    finally:
        shutil.rmtree(a, ignore_errors=True)


async def test_identical_request_reuses_session():
    """Ters yön: kimlik aynıyken session KORUNMALI (yoksa her tur bağlam sıfırlanır)."""
    a = tempfile.mkdtemp(prefix="wsS_")
    try:
        s1 = get_session(11, cwd=a, model="claude-x", effort="high")
        s2 = get_session(11, cwd=a, model="claude-x", effort="high")
        assert s1 is s2
    finally:
        shutil.rmtree(a, ignore_errors=True)


# ── DÜZELTME 4: onay kapısı timeout'u tek kaynaktan ─────────────────────────


def test_the_gate_timeout_leaves_a_human_enough_time():
    """Sabitin DEĞERİNİ sabitler — eşitlik testi bunu yapamaz.

    İlk yazımda burada yalnız `default == ar.APPROVAL_TIMEOUT_S` vardı ve
    mutasyonla ölçüldü (2026-07-28): sabit 60.0'a düşürülünce test YEŞİL kaldı,
    çünkü karşılaştırmanın iki tarafı da aynı sabitten geliyordu — beklentiyi
    ölçtüğü şeyden üreten test hiçbir zaman kırmızı olamaz. Bulgunun kendisi
    sayının küçüklüğüydü: 60 sn'de backend gate'i silip `approved=False`
    yapıyordu, yani 90. saniyede onaylayan kullanıcının kararı sessizce
    reddediliyordu. O yüzden burada bir TABAN duruyor, eşitlik değil."""
    from agentic.command_gates import APPROVAL_TIMEOUT_S as gate_timeout
    assert gate_timeout >= 300.0, (
        "onay penceresi kısaldı; kullanıcı kararını yetiştiremeden gate düşer"
    )


def test_every_consumer_reads_the_one_shared_timeout():
    """Dört tüketici de aynı nesneyi okumalı. Dördüncüsü (`codex_session`)
    ilk turda gözden kaçmıştı: üç yer hizalanmış, biri ayrı literal kalmıştı —
    'uyuşması gereken N yer' arızası N'i azaltmakla değil 1'e indirmekle kapanır."""
    from agentic.command_gates import APPROVAL_TIMEOUT_S as shared
    from agentic import agent_runner as ar
    from providers import codex_session as cs

    assert ar.APPROVAL_TIMEOUT_S is shared
    for cls in (ClaudeSDKSession, cs.CodexSession):
        default = inspect.signature(cls.__init__).parameters["approval_timeout"].default
        assert default is shared, f"{cls.__name__} ayrı bir literal taşıyor"


def test_agent_runner_has_no_hardcoded_gate_timeout():
    """Arızanın kendisi 'uyuşması gereken üç yer'di — literal geri gelirse ısırsın.
    Bu kaynak-metin kontrolü YALNIZCA ikincil: bu depoda bir probe kaynak metnine
    bakıp düzeltme yapıldığı halde 'hâlâ canlı' demişti. Asıl koruma üstteki
    taban testi; burası yalnız literalin geri sızmasını yakalar."""
    from agentic import agent_runner as ar
    src = inspect.getsource(ar)
    assert "timeout=60.0" not in src
    # ONE, not three. Three was the number of copied approval gates; the gates
    # were collapsed into a single `_await_approval` helper and the constant is
    # read there. In the words of the test above: this fault closes by taking N
    # to 1, not by lowering it — a count above 1 means a copy came back, and
    # this assertion should bite then.
    assert src.count("timeout=APPROVAL_TIMEOUT_S") == 1


# ── unityMCP muafiyetleri kütüğe bağlı mı ────────────────────────────────────
#
# Neden BURADA, `test_unity_tool_policy.py`'ye ek olarak: o dosya politikanın
# kendisini sınıyor, bu blok kapının onu GERÇEKTEN ÇAĞIRDIĞINI. İkisi ayrı
# arızalar — muafiyet listesi doğru olup kapıya hiç bağlanmamış olabilir, ki bu
# depoda kapatılan bug'ın sınıfı tam olarak buydu: yazılı ama hiç eşleşmeyen
# bir kural. Kapı çağrısı silinirse aşağıdakiler kırmızı olur.


@pytest.mark.parametrize("tool,inp", [
    ("mcp__unityMCP__read_console", {"action": "get"}),
    ("mcp__unityMCP__manage_build", {"action": "status"}),
    ("mcp__unityMCP__manage_physics", {"action": "raycast"}),
    ("mcp__unityMCP__unity_reflect", {"action": "search"}),
    ("mcp__unityMCP__find_in_file", {"path": "Assets/A.cs", "pattern": "x"}),
    ("mcp__unityMCP__get_sha", {"path": "Assets/A.cs"}),
])
async def test_unity_mcp_reads_pass_without_a_card(tool, inp, symlink_escape):
    """Keşif çağrıları kart çıkarmamalı; her turda kart refleks-onaya alıştırır."""
    ws, _outside, _link = symlink_escape
    s = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    res, ev = await _drive_gate(s, tool, inp)
    assert ev is None, f"{tool} {inp} salt-okuma olduğu hâlde kart çıkardı"
    assert _is_allow(res)


@pytest.mark.parametrize("tool,inp", [
    # Eski elle yazılmış listenin ÖLÜ girdisi: `manage_scene`'in get_info'su yok.
    ("mcp__unityMCP__manage_scene", {"action": "get_info"}),
    # 29 Tem denetimi: bu action semantik olarak okuma ama `manage_scene.py:90`
    # koşulsuz preflight refresh yapıyor → asset import + derleme + domain reload.
    ("mcp__unityMCP__manage_scene", {"action": "get_hierarchy"}),
    ("mcp__unityMCP__find_gameobjects", {"name": "Player"}),
    # Eski liste `read_console`'un tamamını muaf tutuyordu; clear konsolu siler.
    ("mcp__unityMCP__read_console", {"action": "clear"}),
    ("mcp__unityMCP__manage_scene", {"action": "save"}),
    ("mcp__unityMCP__execute_code", {"action": "execute", "code": "// x"}),
    # safety_checks modelin kendi geçtiği bir bayrak — kapı girdisi değil.
    ("mcp__unityMCP__execute_code", {"action": "execute", "code": "// x", "safety_checks": False}),
    ("mcp__unityMCP__manage_gameobject", {"action": "delete", "target": "Player"}),
])
async def test_unity_mcp_mutations_show_a_card(tool, inp, symlink_escape):
    ws, _outside, _link = symlink_escape
    s = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    res, ev = await _drive_gate(s, tool, inp, approve=True)
    assert ev is not None and ev["type"] == "command_approval_needed", \
        f"{tool} {inp} kartsız izin aldı"
    assert _is_allow(res)


async def test_unity_mcp_batch_is_gated_on_its_contents(symlink_escape):
    """`batch_execute` keyfi alt-çağrı taşıyor; dış ada bakan bir kapı hepsini kaçırır."""
    ws, _outside, _link = symlink_escape

    reads = {"commands": [
        {"tool": "unity_reflect", "params": {"action": "search"}},
        {"tool": "read_console", "params": {"action": "get"}},
    ]}
    s = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    _res, ev = await _drive_gate(s, "mcp__unityMCP__batch_execute", reads)
    assert ev is None, "yalnız okuma içeren batch kart çıkardı"

    mixed = {"commands": [
        {"tool": "unity_reflect", "params": {"action": "search"}},
        {"tool": "manage_gameobject", "params": {"action": "delete", "target": "Player"}},
    ]}
    s2 = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    _res2, ev2 = await _drive_gate(s2, "mcp__unityMCP__batch_execute", mixed, approve=True)
    assert ev2 is not None, "içinde mutasyon olan batch kartsız geçti"


async def test_unity_mcp_mutation_denied_when_user_rejects(symlink_escape):
    """Kart görünmesi yetmez — HAYIR'ın da etkisi olmalı."""
    ws, _outside, _link = symlink_escape
    s = _mk_session(cwd=ws, auto_approve=False, approval_timeout=2.0)
    res, ev = await _drive_gate(
        s, "mcp__unityMCP__execute_code", {"action": "execute", "code": "// x"}, approve=False
    )
    assert ev is not None
    assert not _is_allow(res)
