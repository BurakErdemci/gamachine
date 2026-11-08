"""Bir tur TEK sonlanma olayıyla biter — ve kesilen tur işini kaybetmez.

Sözleşme: her tur ya bir `done` (içinde `stop_reason`) ya da bir `error` yollar.
Ne sıfır, ne iki. 30 Ağu 2026 denetimi bu sözleşmenin üç ayrı yerde tutmadığını
kanıtla gösterdi; buradaki testler o üç yerin karşılığı.

1. `ClaudeSDKSession._finish_turn(error=...)` hem `error` hem `done` kuyruğa
   koyuyordu. `_normalize_session_event` `done`'a varsayılan
   `stop_reason="complete"` damgasını vurduğu için aktarım hatası arayüze
   "hem çöktü hem başarıyla bitti" diye ulaşıyordu.
2. `/chat-stream` cevabı DB'ye yazarken patlarsa, `done` çoktan gitmişken
   dıştaki `except` ikinci bir `error` yolluyordu.
3. Kesilen turda (tavan, ilerleme sigortası) ekranda akan iş DB'ye HİÇ
   yazılmıyordu: kullanıcı çalışmayı görüyor, konuşmayı tekrar açınca yalnız
   uyarı satırını buluyordu.

Üçüncüsü sözleşme ihlali değil veri kaybı, ama aynı yerde yaşıyor: sonlanma
olayını üreten kod ile turu kaydeden kod aynı fonksiyonda.
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import agentic.agent_runner as ar
from routes.conversation_routes import create_conversation_router, _TurnRecord


def _sse_types(body: str) -> "list[str]":
    """SSE gövdesindeki olay tiplerini sırayla döndürür."""
    import json
    out = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            out.append(json.loads(line[6:]).get("type"))
        except Exception:
            pass
    return out


def _client(runner_cls, db=None):
    db = db or MagicMock()
    db.get_ai_config.return_value = ("claude", "claude-opus-5", None, None)
    db.get_api_key.return_value = ""
    db.get_last_workspace.return_value = ""
    db.get_memory.return_value = ""
    db.get_conversation_messages.return_value = []
    app = FastAPI()
    app.include_router(create_conversation_router(db, {}))
    return db, app, patch("routes.conversation_routes.AgentRunner", runner_cls)


def _fail_only_assistant_writes():
    def _yaz(conv_id, role, content, *a, **kw):
        if role == "assistant":
            raise RuntimeError("disk dolu")
        return None
    return _yaz


def _post(app):
    with TestClient(app) as client:
        return client.post("/chat-stream",
                           json={"conversation_id": 1, "user_id": 1, "message": "selam"},
                           headers={"X-Session-Token": ""})


# ── 1. Oturum hata yolu ──────────────────────────────────────────────────
def test_the_session_error_path_queues_one_terminal_not_two():
    from providers.claude_sdk_session import ClaudeSDKSession

    sess = ClaudeSDKSession(1)
    q: asyncio.Queue = asyncio.Queue()
    sess._turn_active = True
    sess._drain_state = None
    sess._out_q = q
    sess._usage_event = None
    sess._final_text = "yarım cevap"
    sess._last_result_text = ""
    sess.session_id = "s1"
    sess.conversation_id = 1
    sess._cancel_grace = lambda: None
    sess._flush_deltas = _noop

    asyncio.run(sess._finish_turn(error="aktarım koptu"))

    tipler = _drain(q)
    assert tipler.count("done") == 0, f"hata yolunda done da gitti: {tipler}"
    assert tipler.count("error") == 1, f"tam bir error bekleniyordu: {tipler}"


def test_the_session_error_path_still_delivers_the_partial_text():
    """Sonlanmayı tekilleştirmek İÇERİĞİ düşürmemeli — yoksa raporlama
    hatasını veri kaybına çevirmiş oluruz."""
    from providers.claude_sdk_session import ClaudeSDKSession

    sess = ClaudeSDKSession(1)
    q: asyncio.Queue = asyncio.Queue()
    sess._turn_active = True
    sess._drain_state = None
    sess._out_q = q
    sess._usage_event = None
    sess._final_text = "yarım cevap"
    sess._last_result_text = ""
    sess.session_id = "s1"
    sess.conversation_id = 1
    sess._cancel_grace = lambda: None
    sess._flush_deltas = _noop

    asyncio.run(sess._finish_turn(error="aktarım koptu"))
    assert "response" in _drain(q)


def test_the_session_success_path_still_sends_done():
    """Kapının ters yönü: başarılı tur hâlâ tam olarak bir `done` yolluyor."""
    from providers.claude_sdk_session import ClaudeSDKSession

    sess = ClaudeSDKSession(1)
    q: asyncio.Queue = asyncio.Queue()
    sess._turn_active = True
    sess._drain_state = None
    sess._out_q = q
    sess._usage_event = None
    sess._final_text = "tam cevap"
    sess._last_result_text = ""
    sess.session_id = "s1"
    sess.conversation_id = 1
    sess._cancel_grace = lambda: None
    sess._flush_deltas = _noop

    asyncio.run(sess._finish_turn())
    tipler = _drain(q)
    assert tipler.count("done") == 1 and "error" not in tipler, tipler


async def _noop():
    return None


def _drain(q: asyncio.Queue) -> "list[str]":
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev is None:
            break
        out.append(ev.get("type"))
    return out


# ── 2. Kayıt hatası ikinci terminal üretmiyor ────────────────────────────
class _NormalRunner:
    def __init__(self, **kw):
        pass

    async def run(self, message):
        yield ar.AgentEvent("response", {"content": "cevap"})
        yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})


def test_a_failing_turn_write_does_not_append_a_second_terminal():
    db, app, p = _client(_NormalRunner)
    # YALNIZ asistan yazımı düşsün: `add_message` kullanıcı mesajı için de
    # çağrılıyor ve o çağrı akıştan ÖNCE, yani hepsini düşürmek turu hiç
    # başlatmazdı — sınamak istediğimiz dal da hiç koşmazdı.
    db.add_message.side_effect = _fail_only_assistant_writes()
    with p:
        body = _post(app).text
    tipler = _sse_types(body)
    assert tipler.count("done") == 1, tipler
    assert "error" not in tipler, f"done'dan sonra ikinci terminal gitti: {tipler}"


def test_a_failing_turn_write_is_still_told_to_the_user():
    """Sessizlik de kabul değil: cevap ekranda ama kaydedilmedi, kullanıcı
    bunu bilmeli. `warning` terminal olmadığı için sözleşmeyi bozmuyor."""
    db, app, p = _client(_NormalRunner)
    # YALNIZ asistan yazımı düşsün: `add_message` kullanıcı mesajı için de
    # çağrılıyor ve o çağrı akıştan ÖNCE, yani hepsini düşürmek turu hiç
    # başlatmazdı — sınamak istediğimiz dal da hiç koşmazdı.
    db.add_message.side_effect = _fail_only_assistant_writes()
    with p:
        body = _post(app).text
    assert "turn_not_saved" in body
    assert "warning" in _sse_types(body)


def test_a_healthy_turn_emits_no_warning():
    # Kapının ters yönü: uyarı yalnız gerçekten kaydedilemeyince çıkmalı.
    db, app, p = _client(_NormalRunner)
    with p:
        body = _post(app).text
    assert "turn_not_saved" not in body
    assert db.add_message.called


# ── 3. Kesilen turun akan işi kayboluyor mu ──────────────────────────────
class _StoppedRunner:
    """Tavana çarpan tur: `text` akıyor, `response` HİÇ gelmiyor."""

    def __init__(self, **kw):
        pass

    async def run(self, message):
        yield ar.AgentEvent("text", {"content": "birinci adım tamam"})
        yield ar.AgentEvent("text", {"content": "ikinci adım tamam"})
        yield ar.AgentEvent("done", {"iterations": 300, "stop_reason": "max_iterations",
                                     "stop_message": "Koşum durduruldu."})


def test_a_hard_stopped_turn_keeps_the_work_it_streamed():
    db, app, p = _client(_StoppedRunner)
    with p:
        _post(app)
    assert db.add_message.called, "kesilen tur hiç kaydedilmedi"
    kayitli = db.add_message.call_args[0][2]
    assert "birinci adım tamam" in kayitli
    assert "ikinci adım tamam" in kayitli
    assert "Koşum durduruldu." in kayitli, "durma sebebi de kalmalı"


def test_a_normal_turn_does_not_store_its_answer_twice():
    """`text` biriktirmenin bedeli tam olarak burada ölçülür: normal turda
    cevap hem akar hem `response` ile gelir; ikisini de saymak cevabı iki kez
    kaydederdi."""

    class _EchoRunner:
        def __init__(self, **kw):
            pass

        async def run(self, message):
            yield ar.AgentEvent("text", {"content": "merhaba"})
            yield ar.AgentEvent("response", {"content": "merhaba"})
            yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})

    db, app, p = _client(_EchoRunner)
    with p:
        _post(app)
    kayitli = db.add_message.call_args[0][2]
    assert kayitli.count("merhaba") == 1, f"cevap iki kez kaydedildi: {kayitli!r}"


# ── Biriktiricinin kendi sözleşmesi ──────────────────────────────────────
def test_the_record_prefers_response_over_streamed_text():
    r = _TurnRecord()
    r.add(ar.AgentEvent("text", {"content": "akan"}))
    r.add(ar.AgentEvent("response", {"content": "nihai"}))
    assert r.value() == "nihai"


def test_the_record_falls_back_to_streamed_text_without_a_response():
    r = _TurnRecord()
    r.add(ar.AgentEvent("text", {"content": "akan"}))
    r.add(ar.AgentEvent("done", {"stop_message": "durdu"}))
    assert "akan" in r.value() and "durdu" in r.value()


def test_the_record_stays_empty_when_nothing_was_produced():
    r = _TurnRecord()
    r.add(ar.AgentEvent("done", {"stop_reason": "complete"}))
    assert r.value() == ""


def test_the_record_keeps_streamed_work_when_the_response_envelope_is_empty():
    """BOŞ bir `response` akan işi düşürmemeli.

    Sağlayıcı, iptal ile bitirme yarıştığında içi boş bir `response` zarfı
    yollayabiliyor. "Herhangi bir `response` geldiyse akan metni at" kuralı bu
    turda kaydı yalnız durma satırına indiriyordu — biriktiricinin var olma
    sebebi olan veri kaybının aynısı (denetim, 30 Ağu 2026).
    """
    r = _TurnRecord()
    r.add(ar.AgentEvent("text", {"content": "korunması gereken yarım iş"}))
    r.add(ar.AgentEvent("response", {"content": ""}))
    r.add(ar.AgentEvent("done", {"stop_reason": "max_iterations",
                                 "stop_message": "Koşum durduruldu."}))
    kayitli = r.value()
    assert "korunması gereken yarım iş" in kayitli
    assert "Koşum durduruldu." in kayitli


def test_a_later_real_response_still_wins_over_the_streamed_text():
    """Kapının ters yönü, araya boş zarf girmiş hâliyle: dolu `response`
    geldiği anda kural yine 'yedek değil, yerine geçme'."""
    r = _TurnRecord()
    r.add(ar.AgentEvent("text", {"content": "merhaba"}))
    r.add(ar.AgentEvent("response", {"content": ""}))
    r.add(ar.AgentEvent("response", {"content": "merhaba"}))
    assert r.value().count("merhaba") == 1, r.value()


def test_an_empty_response_after_a_real_one_does_not_bring_the_stream_back():
    """Diğer sıra: dolu zarftan SONRA gelen boş zarf, cevabı iki kez
    kaydettirecek şekilde akan metni geri çağırmamalı."""
    r = _TurnRecord()
    r.add(ar.AgentEvent("text", {"content": "merhaba"}))
    r.add(ar.AgentEvent("response", {"content": "merhaba"}))
    r.add(ar.AgentEvent("response", {"content": ""}))
    assert r.value().count("merhaba") == 1, r.value()


def test_a_turn_whose_response_arrives_empty_is_stored_with_its_work():
    """Aynı kural uçtan uca: /chat-stream'in DB'ye yazdığı metin."""

    class _EmptyEnvelopeRunner:
        def __init__(self, **kw):
            pass

        async def run(self, message):
            yield ar.AgentEvent("text", {"content": "birinci adım tamam"})
            yield ar.AgentEvent("response", {"content": ""})
            yield ar.AgentEvent("done", {"iterations": 2, "stop_reason": "max_iterations",
                                         "stop_message": "Koşum durduruldu."})

    db, app, p = _client(_EmptyEnvelopeRunner)
    with p:
        _post(app)
    assert db.add_message.called, "kesilen tur hiç kaydedilmedi"
    kayitli = db.add_message.call_args[0][2]
    assert "birinci adım tamam" in kayitli
    assert "Koşum durduruldu." in kayitli


# ── 4. Codex: iptal edilen tur da bir sonlanma olayı borçlu ──────────────
def _drain_all(q: asyncio.Queue) -> "list[str]":
    """Kuyruğun TAMAMI — sentinel'de durmadan.

    `_drain` sentinel'de duruyor, ve ikinci bir terminal tam olarak orada
    saklanıyor: iki üretici de "terminal + sentinel" koyduğu için ikinci çift
    ilk sentinel'in ARKASINDA kalıyor ve mutasyon testi yeşil bırakıyor
    (ölçüldü, 30 Ağu 2026 — sözleşme sınandığını sanan ama sınamayan test).
    Sıra her zaman böyle de değil: sunucunun `done`u, iptalin sentinel'inden
    ÖNCE düşerse arayüze gerçekten iki terminal ulaşıyor. O yüzden ölçüt
    "kuyrukta iki terminal olmasın".
    """
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev is None:
            continue
        out.append(ev.get("type"))
    return out


def _codex_session(conv_id: int):
    """Süreç açmadan, tur ortasındaymış gibi duran bir CodexSession."""
    from providers.codex_session import CodexSession

    sess = CodexSession(conv_id, model="gpt-test", cwd=os.path.dirname(__file__))
    sess._started = True
    sess.thread_id = "thread-1"
    return sess


def test_a_codex_turn_cancelled_after_a_rejected_interrupt_still_ends():
    """Sıfır terminal tarafı: interrupt reddedilse bile akış bitmeli.

    `cancel_turn` yalnız `_cancel_event`i set ediyordu; o olayı kimse
    tüketmediği ve kuyruğa hiçbir şey konmadığı için çağıran `out_q.get()`te
    süresiz bekliyordu — Durdur'a basmak turu kilitliyordu.
    """

    async def exercise():
        sess = _codex_session(991)

        async def fake_request(method, _params, timeout=60):
            if method == "turn/start":
                return {"result": {"turn": {"id": "turn-1"}}}
            if method == "turn/interrupt":
                raise RuntimeError("interrupt reddedildi")
            raise AssertionError(f"beklenmeyen istek: {method}")

        sess._request = fake_request
        events = []

        async def consume():
            async for ev in sess.stream("selam"):
                events.append(ev)

        task = asyncio.create_task(consume())
        for _ in range(200):
            if sess._out_q is not None and sess._current_turn_id == "turn-1":
                break
            await asyncio.sleep(0.005)
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise AssertionError("sahte Codex turu olay beklemesine hiç gelmedi")
        await sess.cancel_turn()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise AssertionError("iptalden sonra akış bitmedi (sonlanma olayı gelmedi)")
        return events

    events = asyncio.run(exercise())
    tipler = [e.get("type") for e in events]
    assert tipler.count("done") + tipler.count("error") == 1, tipler
    assert events[-1].get("stop_reason") == "cancelled", events[-1]


def test_a_cancelled_codex_turn_does_not_take_a_second_terminal_from_the_server():
    """İki terminal tarafı: interrupt KABUL edilirse app-server kendi
    `turn/completed`ini de yolluyor; iptalin ürettiğiyle birlikte arayüze
    'hem bitti hem durduruldu' giderdi."""

    async def exercise():
        sess = _codex_session(992)
        sess._current_turn_id = "turn-1"
        q: asyncio.Queue = asyncio.Queue()
        sess._out_q = q
        sess._cancel_event = asyncio.Event()
        sess._terminal_sent = False
        sess._final_text = "yarım iş"

        async def fake_request(method, _params, timeout=60):
            assert method == "turn/interrupt", method
            return {"result": {}}

        sess._request = fake_request
        await sess.cancel_turn()
        await sess._handle_notification({"method": "turn/completed", "params": {}})
        return _drain_all(q)

    tipler = asyncio.run(exercise())
    assert tipler.count("done") + tipler.count("error") == 1, tipler
    # Sonlanmış turun cevabı da ikinci kez kuyruğa girmemeli: turun metni zaten
    # `text` olaylarıyla aktı, `response`u tekrar koymak kaydı çift yazdırırdı.
    assert "response" not in tipler, tipler


def test_a_normal_codex_turn_still_ends_with_one_done():
    """Kapının ters yönü: iptal edilmemiş tur hâlâ cevabını ve tam bir `done`
    olayını yolluyor."""

    async def exercise():
        sess = _codex_session(993)
        q: asyncio.Queue = asyncio.Queue()
        sess._out_q = q
        sess._terminal_sent = False
        sess._final_text = "tam cevap"
        await sess._handle_notification({"method": "turn/completed", "params": {}})
        return _drain_all(q)

    tipler = asyncio.run(exercise())
    assert tipler.count("done") == 1 and "error" not in tipler, tipler
    assert "response" in tipler, tipler


def test_a_codex_error_notification_ends_the_turn_exactly_once():
    async def exercise():
        sess = _codex_session(994)
        q: asyncio.Queue = asyncio.Queue()
        sess._out_q = q
        sess._terminal_sent = False
        await sess._handle_notification({"method": "error",
                                         "params": {"message": "app-server çöktü"}})
        # İkinci bir hata bildirimi ikinci terminal ÜRETMEMELİ.
        await sess._handle_notification({"method": "error",
                                         "params": {"message": "aynı çöküş"}})
        return _drain_all(q)

    tipler = asyncio.run(exercise())
    assert tipler.count("error") == 1 and "done" not in tipler, tipler


# ── 5. Codex: BİTMİŞ turun geç gelen bildirimi sonrakini bitirmemeli ─────
# `_terminal_sent` kapısı tur İÇİNDEKİ çift sonlanmayı kapattı ama turlar
# ARASINDAKİNİ açtı: kapıyı `stream()` her turda sıfırlıyor, dolayısıyla A
# turunun geciken `turn/completed`i B turunun kuyruğunda taze bir kapı buluyor
# ve B'yi A'nın cevabıyla bitiriyordu (denetim, 30 Ağu 2026).

def _codex_after_cancel_then_new_turn(conv_id: int):
    """A turunu iptal etmiş, B turunu kurmuş bir session + B'nin kuyruğu."""
    sess = _codex_session(conv_id)
    sess._current_turn_id = "turn-old"
    sess._out_q = asyncio.Queue()
    sess._cancel_event = asyncio.Event()
    sess._terminal_sent = False
    return sess


async def _cancel_then_start_next(sess):
    async def kabul_eden_interrupt(_method, _params, timeout=60):
        return {"result": {}}

    sess._request = kabul_eden_interrupt
    await sess.cancel_turn()

    # `stream()`in yeni tur için yaptığı sıfırlamanın aynısı.
    yeni_kuyruk: asyncio.Queue = asyncio.Queue()
    sess._out_q = yeni_kuyruk
    sess._current_turn_id = "turn-new"
    sess._terminal_sent = False
    sess._final_text = "yeni turun yarım metni"
    return yeni_kuyruk


async def _cancel_then_start_next_unknown_id(sess):
    """`stream()`in gercek sirasi: yeni kuyruk KURULUR, kimlik SILINIR, ve yeni
    kimlik ancak `turn/start` cevabiyla ogrenilir. Aradaki pencere bu."""
    async def kabul_eden_interrupt(_method, _params, timeout=60):
        return {"result": {}}

    sess._request = kabul_eden_interrupt
    await sess.cancel_turn()

    yeni_kuyruk: asyncio.Queue = asyncio.Queue()
    sess._out_q = yeni_kuyruk
    sess._current_turn_id = None      # turn/start henuz cevaplamadi
    sess._terminal_sent = False
    sess._final_text = ""
    return yeni_kuyruk


def test_a_late_completion_cannot_end_a_turn_whose_id_is_not_known_yet():
    """Ucuncu dogrulama turunun bulgusu (30 Agu 2026).

    "Iki BILINEN ve farkli kimlik reddeder" kurali dogruydu ama bir pencere
    birakiyordu: `stream()` yeni kuyrugu kurup kimligi siliyor, ve yeni kimlik
    ancak sunucu cevabiyla geliyor. O aralikta gelen eski bitis canli sayiliyor,
    yeni turun sonlanma kapisini harciyor ve tur eski turun cevabiyla bitiyordu.
    Asagi tarafta fark edilmesi de imkansizdi: bayat `done` bir `stop_reason`
    tasimadigi icin `_normalize_session_event` ona `complete` damgasini vuruyor.

    Pencere kurali SIKILASTIRARAK degil, ters taraftan kapatildi: BITMIS bir tur
    sonsuza dek bayattir, canli turun kimligi o an bilinsin ya da bilinmesin.
    """

    async def exercise():
        sess = _codex_after_cancel_then_new_turn(997)
        q = await _cancel_then_start_next_unknown_id(sess)
        await sess._handle_notification({
            "method": "turn/completed",
            "params": {"turn": {"id": "turn-old"}},
        })
        return _drain_all(q), sess._terminal_sent

    tipler, kapandi = asyncio.run(exercise())
    assert tipler == [], f"kimlik bilinmezken bayat bitis sizdi: {tipler}"
    assert not kapandi, "yeni turun sonlanma kapisi eski turla harcandi"


def test_an_unknown_id_notification_from_the_live_turn_is_still_delivered():
    """Kapinin TERS yonu, ve bu testin varlik sebebi: pencereyi 'bilinmeyen
    kimligi de reddet' diye kapatmak gercek isi coper. Canli turun kendi ilk
    bildirimleri tam da o aralikta geliyor."""

    async def exercise():
        sess = _codex_after_cancel_then_new_turn(998)
        q = await _cancel_then_start_next_unknown_id(sess)
        await sess._handle_notification({
            "method": "item/agentMessage/delta",
            "params": {"turnId": "turn-new", "delta": "canli metin"},
        })
        return _drain_all(q)

    assert "text" in asyncio.run(exercise())


def test_only_a_bounded_number_of_finished_turn_ids_is_remembered():
    """Sinir olmadan hatirlamak, uzun bir oturumda sinirsiz buyuyen bir kume
    demek olurdu."""
    from providers.codex_session import _RETIRED_TURN_IDS_MAX

    sess = _codex_after_cancel_then_new_turn(999)
    for i in range(_RETIRED_TURN_IDS_MAX * 3):
        sess._retired_turn_ids.append(f"turn-{i}")
    assert len(sess._retired_turn_ids) == _RETIRED_TURN_IDS_MAX


def test_a_late_completion_from_a_cancelled_turn_does_not_end_the_next_turn():
    async def exercise():
        sess = _codex_after_cancel_then_new_turn(995)
        q = await _cancel_then_start_next(sess)
        await sess._handle_notification({
            "method": "turn/completed",
            "params": {"turn": {"id": "turn-old"}},
        })
        return _drain_all(q), sess._terminal_sent

    tipler, kapandi = asyncio.run(exercise())
    assert tipler == [], f"biten turun bildirimi yeni tura sızdı: {tipler}"
    assert not kapandi, "yeni turun sonlanma kapısı başkasının turuyla harcandı"


def test_a_late_error_from_a_cancelled_turn_does_not_end_the_next_turn():
    """`error` de bir terminal üreticisi: kapı yalnız `turn/completed`i
    kapatsaydı aynı yarış bu yoldan aynen sürerdi."""

    async def exercise():
        sess = _codex_after_cancel_then_new_turn(996)
        q = await _cancel_then_start_next(sess)
        await sess._handle_notification({
            "method": "error",
            "params": {"turnId": "turn-old", "message": "eski tur çöktü"},
        })
        return _drain_all(q), sess._terminal_sent

    tipler, kapandi = asyncio.run(exercise())
    assert tipler == [], f"biten turun hatası yeni tura sızdı: {tipler}"
    assert not kapandi


def test_a_late_item_notification_does_not_leak_into_the_next_turn():
    """Terminal olmayan bildirimler de aidiyet sınavına giriyor: eski turun
    araç çıktısı yeni turun dökümüne yapışırsa kayıt yanlış olur."""

    async def exercise():
        sess = _codex_after_cancel_then_new_turn(997)
        q = await _cancel_then_start_next(sess)
        await sess._handle_notification({
            "method": "item/completed",
            # Yazım bilerek `turn_id`: ölçüt adres değil, sadeleştirilmiş
            # anahtar ADI — casing/ayırıcı farkı sessizce kaçırılmamalı.
            "params": {"turn_id": "turn-old",
                       "item": {"type": "commandExecution", "exitCode": 0}},
        })
        await sess._handle_notification({
            "method": "item/agentMessage/delta",
            "params": {"turnId": "turn-old", "delta": "eski turun metni"},
        })
        return _drain_all(q), sess._final_text

    tipler, metin = asyncio.run(exercise())
    assert tipler == [], f"eski turun araç/metin olayları yeni tura sızdı: {tipler}"
    assert "eski turun metni" not in metin, metin


def test_the_running_turns_own_completion_still_ends_it():
    """Kapının ters yönü: id'ler UYUŞUNCA bildirim aynen işleniyor."""

    async def exercise():
        sess = _codex_after_cancel_then_new_turn(998)
        q = await _cancel_then_start_next(sess)
        await sess._handle_notification({
            "method": "turn/completed",
            "params": {"turn": {"id": "turn-new"}},
        })
        return _drain_all(q)

    tipler = asyncio.run(exercise())
    assert tipler.count("done") == 1 and "error" not in tipler, tipler
    assert "response" in tipler, tipler


def test_a_notification_arriving_before_the_turn_id_is_known_is_still_delivered():
    """Bilinmeyen id bir aidiyet kanıtı DEĞİL: `stream()` id'yi ancak
    `turn/start` cevabıyla öğreniyor, o ana kadar gelen gerçek olayları elemek
    turun işini düşürürdü."""

    async def exercise():
        sess = _codex_session(999)
        q: asyncio.Queue = asyncio.Queue()
        sess._out_q = q
        sess._terminal_sent = False
        sess._current_turn_id = None
        await sess._handle_notification({
            "method": "item/agentMessage/delta",
            "params": {"turnId": "turn-1", "delta": "canlı metin"},
        })
        await sess._handle_notification({
            "method": "turn/completed",
            "params": {"turn": {"id": "turn-1"}},
        })
        return _drain_all(q), sess._final_text

    tipler, metin = asyncio.run(exercise())
    assert tipler.count("done") == 1, tipler
    assert metin == "canlı metin", metin
