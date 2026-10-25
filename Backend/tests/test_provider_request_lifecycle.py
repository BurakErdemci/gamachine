"""Sağlayıcı isteğinin ömrü: beklerken konuşmak, tur ölünce isteği bırakmamak,
ve bozuk bir cevabı terminal olaya çevirmek.

Üç arıza, üçü de 30 Ağu 2026 denetiminde canlı üretildi:

1. Kalp atışı yalnız OpenAI-uyumlu isteğin ETRAFINA konmuştu. Gemini bloklayan
   SDK çağrısını `asyncio.to_thread` ile, Anthropic `messages.create`'i doğrudan
   bekliyordu ve ikisi de tek olay yaymadan sessiz kalıyordu. Bu deponun adı
   konmuş yinelenen arızası: kapı bir dala konuyor, diğerleri açık kalıyor.
   O yüzden buradaki testler ÜÇ yolu birden sınıyor.

2. OpenAI döngüsü isteği bir göreve alıp `asyncio.wait` ile yarıştırıyor ama
   generator kapandığında (kullanıcı "Durdur"a bastığında) o görevi kimse iptal
   etmiyordu: durdurulan bir tur sağlayıcı çağrısını 300 sn'lik zaman aşımına
   kadar sahipsiz koşar hâlde bırakıyordu.

3. `response.choices[0]` kontrolsüz indeksleniyordu. `choices=[]` dönen bir
   sağlayıcı cevabında `IndexError` generator'ın DIŞINA taşıyor ve tur HİÇBİR
   terminal olay üretmeden bitiyordu — sözleşme her turun tam olarak bir `done`
   ya da bir `error` ile bitmesi (bkz. `test_iteration_ceiling`).

Canlı sağlayıcıya çıkılmıyor: her istemci sahte.
"""
import asyncio
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import agentic.agent_runner as ar


# ── Ortak koşum düzeneği ────────────────────────────────────────────────────


def _patches(client, kalp_atisi=0.01):
    return [
        mock.patch.object(ar, "_all_tool_definitions", lambda: []),
        mock.patch.object(ar, "get_openai_tool_declarations", lambda: []),
        mock.patch.object(ar, "get_gemini_tool_declarations",
                          lambda: [{"function_declarations": []}]),
        mock.patch.object(ar, "_KALP_ATISI_SN", kalp_atisi),
        mock.patch.object(ar.openai, "AsyncOpenAI", lambda **kw: client),
        mock.patch.object(ar.anthropic, "AsyncAnthropic", lambda **kw: client),
        mock.patch.object(ar.genai, "Client", lambda **kw: client),
    ]


def _runner(provider_type):
    return ar.AgentRunner(provider_type=provider_type, api_key="k",
                          model_name="test-model", workspace_path=".")


def _loop_of(runner, provider_type, mesaj="merhaba"):
    return {"openai": runner._run_openai,
            "anthropic": runner._run_anthropic,
            "google": runner._run_gemini}[provider_type](mesaj)


def _collect(provider_type, client, kalp_atisi=0.01):
    runner = _runner(provider_type)
    ps = _patches(client, kalp_atisi)

    async def _go():
        return [e async for e in _loop_of(runner, provider_type)]

    for p in ps:
        p.start()
    try:
        return asyncio.run(_go())
    finally:
        for p in reversed(ps):
            p.stop()


def _kalpler(olaylar):
    return [e for e in olaylar if e.type == "status"
            and "sn'dir yanıt vermedi" in (e.data.get("detail") or "")]


# ── Sahte, YAVAŞ sağlayıcılar ───────────────────────────────────────────────
# Gecikme kalp atışı aralığının (0.01) birkaç katı; testin kendisi ~60 ms.
_GECIKME = 0.06


class _YavasOpenAI:
    def __init__(self):
        self.base_url = None
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        await asyncio.sleep(_GECIKME)
        message = types.SimpleNamespace(content="bitti", tool_calls=None)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=message)],
            usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1))


class _YavasAnthropic:
    def __init__(self):
        self.messages = types.SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        await asyncio.sleep(_GECIKME)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="bitti")],
            usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))


class _YavasGemini:
    """Gemini'nin SDK çağrısı BLOKLAYAN: `time.sleep`, `asyncio.sleep` değil.

    Arızanın tam şekli buydu — iş `asyncio.to_thread` ile havuz thread'ine
    gidiyor, olay döngüsü boşta ama hiçbir şey yayınlamıyordu.
    """

    def __init__(self):
        self.models = types.SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        time.sleep(_GECIKME)
        part = types.SimpleNamespace(function_call=None, text="bitti", thought=False)
        return types.SimpleNamespace(
            candidates=[types.SimpleNamespace(
                content=types.SimpleNamespace(parts=[part]))],
            usage_metadata=types.SimpleNamespace(
                prompt_token_count=1, candidates_token_count=1))


_YAVAS = (("openai", _YavasOpenAI), ("anthropic", _YavasAnthropic),
          ("google", _YavasGemini))


class TestHeartbeatCoversEveryProvider(unittest.TestCase):

    def test_a_slow_call_reports_on_all_three_paths(self):
        for ad, factory in _YAVAS:
            with self.subTest(provider=ad):
                olaylar = _collect(ad, factory())
                self.assertTrue(_kalpler(olaylar),
                                "bekleme sessiz kalmamalı")

    def test_the_heartbeat_names_the_model_and_offers_a_way_out(self):
        for ad, factory in _YAVAS:
            with self.subTest(provider=ad):
                detay = _kalpler(_collect(ad, factory()))[0].data["detail"]
                self.assertIn("test-model", detay)
                self.assertIn("Durdur", detay)

    def test_a_fast_call_produces_no_heartbeat_noise(self):
        """Kalp atışı bir gürültü kaynağına dönüşürse anlamını kaybeder."""

        class _HizliOpenAI(_YavasOpenAI):
            async def _create(self, **kwargs):
                message = types.SimpleNamespace(content="bitti", tool_calls=None)
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=message)],
                    usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1))

        self.assertEqual(_kalpler(_collect("openai", _HizliOpenAI())), [])

    def test_the_run_still_finishes_after_the_wait(self):
        for ad, factory in _YAVAS:
            with self.subTest(provider=ad):
                olaylar = _collect(ad, factory())
                self.assertEqual([e.type for e in olaylar
                                  if e.type in ("done", "error")], ["done"])

    def test_the_wait_lives_in_one_helper_not_three(self):
        """Sınıfı kapatıyor, bir dalı değil.

        Bekleme mantığı üç döngüye kopyalanırsa biri düzeltilip diğeri
        unutuluyor — bu deponun ölçülmüş en sık arıza şekli, ve kalp atışının
        ilk hâli tam olarak böyle tek dala konmuştu.
        """
        src = open(ar.__file__, encoding="utf-8").read()
        self.assertEqual(src.count("asyncio.wait({"), 1)
        # The three provider loops now share one request call site.
        self.assertEqual(src.count("self._await_provider("), 1)


# ── Durdurulan tur sağlayıcı isteğini de götürür ────────────────────────────


class _AskidaOpenAI:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.tasks = []
        self.base_url = None
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.started.set()
        self.tasks.append(asyncio.current_task())
        await self.release.wait()
        raise AssertionError("askıda kalan istek serbest bırakılmamalıydı")


class _AskidaAnthropic(_AskidaOpenAI):
    def __init__(self):
        super().__init__()
        self.messages = types.SimpleNamespace(create=self._create)


class TestCancellationTakesTheRequestWithIt(unittest.TestCase):
    """Durdurulan bir tur arkasında koşan bir sağlayıcı çağrısı bırakmaz.

    Bırakınca ne oluyordu: sonucunu tüketecek kimse kalmadığı hâlde istek
    300 sn'lik zaman aşımına kadar sağlayıcı/ağ kaynağı harcamaya devam
    ediyor, art arda durdurulan turlar bunları biriktiriyordu.
    """

    def _iptal_et(self, provider_type, client):
        async def _go():
            runner = _runner(provider_type)
            gen = _loop_of(runner, provider_type)
            tuketici = asyncio.create_task(gen.__anext__())
            await asyncio.wait_for(client.started.wait(), timeout=2.0)
            tuketici.cancel()
            await asyncio.gather(tuketici, return_exceptions=True)
            await asyncio.sleep(0)
            askida = [t for t in client.tasks if not t.done()]
            await gen.aclose()
            return client.tasks, askida

        ps = _patches(client)
        for p in ps:
            p.start()
        try:
            return asyncio.run(_go())
        finally:
            for p in reversed(ps):
                p.stop()

    def test_stopping_the_turn_cancels_the_openai_request(self):
        tumu, askida = self._iptal_et("openai", _AskidaOpenAI())
        self.assertEqual(len(tumu), 1)
        self.assertEqual(askida, [])

    def test_stopping_the_turn_cancels_the_anthropic_request(self):
        tumu, askida = self._iptal_et("anthropic", _AskidaAnthropic())
        self.assertEqual(len(tumu), 1)
        self.assertEqual(askida, [])

    def test_a_finished_request_is_not_cancelled_out_from_under_the_caller(self):
        """İptal `done()` olmayan isteğe ÖZEL.

        Biten bir isteği `finally` içinde beklemek onun sonucunu (ya da
        istisnasını) yutardı; bu test bitenin sonucunun hâlâ okunduğunu
        gösteriyor, yani iptal ucu yutan bir uca dönüşmemiş.
        """
        olaylar = _collect("openai", _YavasOpenAI())
        yanit = [e for e in olaylar if e.type == "response"]
        self.assertEqual([e.data["content"] for e in yanit], ["bitti"])

    def test_a_provider_error_is_still_reported_not_swallowed(self):
        class _Patlayan(_YavasOpenAI):
            async def _create(self, **kwargs):
                raise RuntimeError("saglayici patladi")

        olaylar = _collect("openai", _Patlayan())
        terminal = [e for e in olaylar if e.type in ("done", "error")]
        self.assertEqual([e.type for e in terminal], ["error"])
        self.assertIn("saglayici patladi", terminal[0].data["message"])


# ── Bozuk sağlayıcı cevabı = tam olarak bir terminal olay ───────────────────


class TestMalformedResponseStillTerminates(unittest.TestCase):

    def test_empty_choices_becomes_exactly_one_error(self):
        class _BosSecenek:
            def __init__(self):
                self.base_url = None
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create))

            async def _create(self, **kwargs):
                return types.SimpleNamespace(choices=[], usage=None)

        olaylar = _collect("openai", _BosSecenek())   # istisna sızarsa test kırmızı
        terminal = [e for e in olaylar if e.type in ("done", "error")]
        self.assertEqual([e.type for e in terminal], ["error"])
        self.assertEqual(terminal[0].data["code"], "provider_malformed_response")
        # Mesaj hangi modelin bozuk cevap verdiğini söylemeli: "bir hata oldu"
        # kullanıcının üzerine hareket edebileceği bir cümle değil.
        self.assertIn("test-model", terminal[0].data["message"])

    def test_gemini_content_without_parts_still_terminates(self):
        """Aynı sınıfın Gemini'deki hâli: `candidates` boşluğu kapalıydı ama
        `content.parts` değildi ve `for part in None` bir `TypeError`'ı dışarı
        taşırdı. Parçasız bir aday araçsız bir tur demek → tek `done`."""

        class _ParcasizGemini:
            def __init__(self):
                self.models = types.SimpleNamespace(generate_content=self._generate)

            def _generate(self, **kwargs):
                return types.SimpleNamespace(
                    candidates=[types.SimpleNamespace(
                        content=types.SimpleNamespace(parts=None))],
                    usage_metadata=None)

        olaylar = _collect("google", _ParcasizGemini())
        self.assertEqual([e.type for e in olaylar
                          if e.type in ("done", "error")], ["done"])

    def test_anthropic_response_without_content_still_terminates(self):
        class _IceriksizAnthropic:
            def __init__(self):
                self.messages = types.SimpleNamespace(create=self._create)

            async def _create(self, **kwargs):
                return types.SimpleNamespace(content=None, usage=None)

        olaylar = _collect("anthropic", _IceriksizAnthropic())
        self.assertEqual([e.type for e in olaylar
                          if e.type in ("done", "error")], ["done"])


    def test_a_gemini_part_without_function_call_still_terminates(self):
        """Bir önceki düzeltme `content.parts=None` şeklini kapatmıştı; hemen
        ardındaki `p.function_call` okuması AÇIK kalmıştı. Yalnız `text` taşıyan
        bir parça `AttributeError`'ı istek `try`'ının DIŞINDA fırlatıyor ve tur
        hiç terminal olay üretmeden bitiyordu (denetim, 30 Ağu 2026)."""

        class _EksikAlanliGemini:
            def __init__(self):
                self.models = types.SimpleNamespace(generate_content=self._generate)

            def _generate(self, **kwargs):
                # `function_call` ve `thought` YOK — sadece metin var.
                part = types.SimpleNamespace(text="sağlayıcı metni")
                return types.SimpleNamespace(
                    candidates=[types.SimpleNamespace(
                        content=types.SimpleNamespace(parts=[part]))],
                    usage_metadata=None)

        olaylar = _collect("google", _EksikAlanliGemini())
        self.assertEqual([e.type for e in olaylar
                          if e.type in ("done", "error")], ["done"])
        yanit = [e for e in olaylar if e.type == "response"]
        self.assertEqual([e.data["content"] for e in yanit], ["sağlayıcı metni"])

    def test_a_nameless_function_call_is_not_treated_as_a_tool_call(self):
        """Çağrılamayan bir `function_call` araç sayılmıyor: tur araçsız
        yolundan doğal olarak tek `done` ile kapanıyor, `fc.name` üzerinde
        patlamıyor."""

        class _IsimsizCagriGemini:
            def __init__(self):
                self.models = types.SimpleNamespace(generate_content=self._generate)

            def _generate(self, **kwargs):
                part = types.SimpleNamespace(
                    text="", function_call=types.SimpleNamespace(args={}))
                return types.SimpleNamespace(
                    candidates=[types.SimpleNamespace(
                        content=types.SimpleNamespace(parts=[part]))],
                    usage_metadata=None)

        olaylar = _collect("google", _IsimsizCagriGemini())
        self.assertEqual([e.type for e in olaylar
                          if e.type in ("done", "error")], ["done"])


# ── Sonlanma garantisi ŞEKİLDEN BAĞIMSIZ ────────────────────────────────────


class TestTerminalGuaranteeIsStructural(unittest.TestCase):
    """Tek tek şekilleri kapatmak bu depoda işe yaramadı.

    `choices=[]`, `content.parts=None`, `function_call` alanı olmayan parça —
    üçü de aynı sınıf, üçü de bulunduğu yerde kapatıldı ve her seferinde bir
    sonraki öngörülmemiş sağlayıcı şekli yeni bir delik açtı. Bu testler
    özel duruma değil, sınıfı kapatan sarmalayıcıya bakıyor.
    """

    @staticmethod
    def _topla(uretici):
        async def _go():
            return [e async for e in ar._guarantee_terminal(uretici, "test-model")]
        return asyncio.run(_go())

    def test_an_unexpected_exception_becomes_exactly_one_error(self):
        async def _patlayan():
            yield ar.AgentEvent("text", {"content": "yarım iş"})
            raise AttributeError("öngörülmemiş sağlayıcı şekli")

        olaylar = self._topla(_patlayan())
        terminal = [e for e in olaylar if e.type in ("done", "error")]
        self.assertEqual([e.type for e in terminal], ["error"])
        self.assertEqual(terminal[0].data["code"], "provider_loop_crashed")
        self.assertIn("test-model", terminal[0].data["message"])

    def test_the_work_streamed_before_the_crash_is_not_dropped(self):
        """Sonlanmayı garanti etmek İÇERİĞİ düşürmemeli."""

        async def _patlayan():
            yield ar.AgentEvent("text", {"content": "yarım iş"})
            raise RuntimeError("kopuş")

        olaylar = self._topla(_patlayan())
        self.assertIn("yarım iş", [e.data.get("content") for e in olaylar])

    def test_a_loop_that_ends_without_saying_how_is_reported(self):
        async def _sessiz():
            yield ar.AgentEvent("text", {"content": "iş"})

        terminal = [e for e in self._topla(_sessiz())
                    if e.type in ("done", "error")]
        self.assertEqual([e.data["code"] for e in terminal],
                         ["provider_no_terminal"])

    def test_a_healthy_loop_gets_no_extra_terminal(self):
        """Kapının ters yönü: sağlam bir tur ikinci bir terminal olay almıyor."""

        async def _saglam():
            yield ar.AgentEvent("response", {"content": "cevap"})
            yield ar._done_event(1)

        terminal = [e for e in self._topla(_saglam())
                    if e.type in ("done", "error")]
        self.assertEqual([e.type for e in terminal], ["done"])

    def test_a_stopped_turn_owes_no_terminal_event(self):
        """`CancelledError`/`GeneratorExit` yakalanmıyor: turu bırakan taraf
        zaten olayı okuyacak olan taraf. Uydurma bir `error` üretmek durdurma
        akışını hataya çevirirdi."""
        kapandi = []

        async def _askida():
            try:
                yield ar.AgentEvent("text", {"content": "iş"})
                await asyncio.sleep(10)
            finally:
                kapandi.append(True)

        async def _go():
            # İç generator'a burada da bir referans TUTULUYOR ve bu kasıtlı:
            # bırakılsaydı sarmalayıcı kapanır kapanmaz referans sayacı düşer,
            # CPython'un kendi sonlandırıcısı `finally`'yi çalıştırır ve test
            # mekanizmayı değil çöp toplayıcıyı ölçerdi (ölçüldü: mutasyon
            # testi yeşil bıraktı). Referans dururken `finally`'yi
            # çalıştırabilecek tek şey sarmalayıcının açık `aclose()`'u.
            ic = _askida()
            gen = ar._guarantee_terminal(ic, "test-model")
            ilk = await gen.__anext__()
            await gen.aclose()
            # Sarmalayıcı kapanınca SARDIĞI da kapanmalı: iç döngünün
            # `finally`'si sahipsiz kalan sağlayıcı isteğini devreden yer
            # (bkz. `_await_provider`), onu çöp toplayıcıya bırakmak durdurma
            # yolunu belirsizleştirirdi.
            return ilk, list(kapandi), ic

        ilk, kapandi_ic, _ic = asyncio.run(_go())
        self.assertEqual(ilk.type, "text")
        self.assertEqual(kapandi_ic, [True])

    def test_a_second_terminal_event_never_reaches_the_consumer(self):
        """The wrapper used to COUNT duplicates and forward them anyway: it
        detected the violation after both had already escaped, so a consumer
        could end or persist the same turn twice. Enforcement, not reporting —
        the extra terminal is dropped at the moment it is seen."""

        async def _cift_terminal():
            yield ar.AgentEvent("response", {"content": "cevap"})
            yield ar._done_event(1)
            yield ar.AgentEvent("error", {"message": "geç gelen sağlayıcı hatası"})
            yield ar.AgentEvent("text", {"content": "sonrası"})

        with self.assertLogs(ar.logger, level="ERROR") as kayit:
            olaylar = self._topla(_cift_terminal())

        self.assertEqual([e.type for e in olaylar
                          if e.type in ("done", "error")], ["done"])
        # Dropping the duplicate must not turn into dropping the stream: a
        # non-terminal event after it still reaches the consumer.
        self.assertIn("sonrası", [e.data.get("content") for e in olaylar])
        # Loud on the way out: a loop with two terminals stays findable.
        self.assertTrue(any("terminal" in satir for satir in kayit.output))

    def test_a_group_carrying_a_real_failure_becomes_one_error(self):
        """`except Exception` excluded a `BaseExceptionGroup` too, because a
        group is BaseException. Structured concurrency (TaskGroup/anyio, what
        the provider SDKs sit on) puts a `CancelledError` next to the real
        failure exactly like this, and the turn ended with zero terminals."""

        async def _karisik_grup():
            if False:
                yield None
            raise BaseExceptionGroup(
                "sağlayıcı görevleri düştü",
                [asyncio.CancelledError("iptal edilen görev"),
                 RuntimeError("gerçek sağlayıcı hatası")])

        terminal = [e for e in self._topla(_karisik_grup())
                    if e.type in ("done", "error")]
        self.assertEqual([e.type for e in terminal], ["error"])
        self.assertEqual(terminal[0].data["code"], "provider_loop_crashed")

    def test_a_group_of_only_cancellations_still_passes_through(self):
        """The other side of the same gate: widening the catch must not start
        answering a stopped turn with an invented `error`. A group whose every
        leaf is a cancellation is a cancellation."""

        async def _iptal_grubu():
            if False:
                yield None
            raise BaseExceptionGroup(
                "tur durduruldu",
                [asyncio.CancelledError("a"), asyncio.CancelledError("b")])

        with self.assertRaises(BaseExceptionGroup):
            self._topla(_iptal_grubu())

    def test_every_api_loop_goes_through_the_guarantee(self):
        """Sınıfı kapatıyor, bir dalı değil — kapının bir dala konup
        diğerlerinin açık kalması bu deponun ölçülmüş en sık arızası."""
        src = open(ar.__file__, encoding="utf-8").read()
        self.assertEqual(src.count("_guarantee_terminal("), 4)   # 1 tanım + 3 kullanım


# ── İptal edilemeyen Gemini isteği sahipsiz kalmıyor ────────────────────────


class _AskidaGemini:
    """Bloklayan, thread'e bağlı ve İPTAL EDİLEMEZ — gerçek Gemini şekli."""

    def __init__(self, hata: "Exception | None" = None):
        self.started = threading.Event()
        self.release = threading.Event()
        self.hata = hata
        self.models = types.SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.started.set()
        self.release.wait(5)
        if self.hata:
            raise self.hata
        part = types.SimpleNamespace(function_call=None, text="bitti", thought=False)
        return types.SimpleNamespace(
            candidates=[types.SimpleNamespace(
                content=types.SimpleNamespace(parts=[part]))],
            usage_metadata=None)


class TestAbandonedGeminiRequestIsAccountedFor(unittest.TestCase):
    """`asyncio.to_thread` içindeki iş DURDURULAMAZ ve bu değişmiyor.

    `Task.cancel()` bir havuz thread'ine ulaşmıyor; iptal etmek bloklayan SDK
    çağrısını kesmez, yalnız sonucunu kimsenin okumadığı bir `CancelledError`'a
    çevirirdi. O yüzden istek turdan uzun yaşıyor — düzeltilebilir olan
    SAHİPLİK: sonucu her hâlükârda okunuyor ve hatası kayda geçiyor.
    """

    def _kesilen_tur(self, client):
        """Turu sağlayıcı çağrısı başladıktan sonra durdurur; sonra thread'i
        serbest bırakıp isteğin akıbetini döndürür."""
        original_create_task = asyncio.create_task
        gorevler = []

        def _yakala(coro, *a, **kw):
            gorev = original_create_task(coro, *a, **kw)
            gorevler.append(gorev)
            return gorev

        async def _go():
            runner = _runner("google")
            gen = runner._run_gemini("merhaba")
            with mock.patch.object(ar.asyncio, "create_task", _yakala):
                tuketici = original_create_task(gen.__anext__())
                await asyncio.wait_for(
                    asyncio.to_thread(client.started.wait, 2), timeout=2)
                tuketici.cancel()
                await asyncio.gather(tuketici, return_exceptions=True)
                await gen.aclose()
            self.assertTrue(gorevler, "sağlayıcı görevi hiç oluşturulmadı")
            istek = gorevler[0]
            # Thread durdurulamıyor: tur bittiği hâlde istek hâlâ koşuyor.
            # Bu bir kusur değil, ödenen bedelin kendisi — testin kaydettiği şey.
            askida_kaldi = not istek.done()
            client.release.set()
            await asyncio.wait_for(asyncio.shield(
                asyncio.gather(istek, return_exceptions=True)), timeout=5)
            return askida_kaldi, istek

        ps = _patches(client)
        for p in ps:
            p.start()
        try:
            return asyncio.run(_go())
        finally:
            client.release.set()
            for p in reversed(ps):
                p.stop()

    def test_the_thread_bound_request_does_outlive_the_stopped_turn(self):
        client = _AskidaGemini()
        with mock.patch.object(ar.logger, "warning"):
            askida_kaldi, istek = self._kesilen_tur(client)
        self.assertTrue(askida_kaldi,
                        "iptal edilemeyen istek turdan uzun yaşamalı — "
                        "aksi bir sonuç thread'in durdurulabildiği anlamına gelir")
        self.assertTrue(istek.done())

    def test_a_failure_after_the_turn_is_logged_not_lost(self):
        client = _AskidaGemini(hata=RuntimeError("sağlayıcı geç patladı"))
        with mock.patch.object(ar.logger, "warning") as uyari:
            self._kesilen_tur(client)
        metinler = [str(c.args[0]) for c in uyari.call_args_list if c.args]
        self.assertTrue(any("sahipsiz" in m and "sağlayıcı geç patladı" in m
                            for m in metinler),
                        f"sahipsiz isteğin hatası kayda geçmedi: {metinler}")

    def test_a_clean_late_finish_stays_quiet(self):
        """Kapının ters yönü: başarıyla biten sahipsiz istek gürültü üretmiyor."""
        client = _AskidaGemini()
        with mock.patch.object(ar.logger, "warning") as uyari:
            self._kesilen_tur(client)
        metinler = [str(c.args[0]) for c in uyari.call_args_list if c.args]
        self.assertEqual([m for m in metinler if "sahipsiz" in m], [])

    def test_the_uncancellable_branch_hands_the_outcome_to_a_reader(self):
        """Mekanizmanın kendisi: iptal edilmeyen istek bir done-callback'e
        bağlanıyor. Bu olmadan görevin istisnası hiç okunmuyor ve asyncio onu
        çok sonra, hangi tura ait olduğu bilinmeden rapor ediyor."""

        def _okuyucu_bagli_mi(istek) -> bool:
            # `Task._callbacks` özel bir alan; mekanizmayı doğrudan görmenin
            # başka yolu yok — takılı OLMAMASI da sınanan bir durum olduğu için
            # olayı dolaylı gözlemek (log) tek başına yetmiyor.
            return any((c[0] if isinstance(c, tuple) else c)
                       is ar._consume_abandoned_request
                       # Biten bir görevde alan `None`'a düşüyor.
                       for c in (istek._callbacks or []))

        async def _go():
            runner = _runner("google")
            istek = asyncio.create_task(asyncio.sleep(0.05))
            async for _ in runner._await_provider(istek, iptal_edilebilir=False):
                pass
            return istek, _okuyucu_bagli_mi(istek)

        async def _kes():
            runner = _runner("google")
            istek = asyncio.create_task(asyncio.sleep(5))
            gen = runner._await_provider(istek, iptal_edilebilir=False)
            tuketici = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0)
            tuketici.cancel()
            await asyncio.gather(tuketici, return_exceptions=True)
            await gen.aclose()
            bagli = _okuyucu_bagli_mi(istek)
            istek.cancel()
            await asyncio.gather(istek, return_exceptions=True)
            return bagli

        with mock.patch.object(ar, "_KALP_ATISI_SN", 0.01):
            self.assertTrue(asyncio.run(_kes()),
                            "iptal edilemeyen istek sahipsiz bırakıldı")
            # Ters yön: biten bir isteğe callback takılmıyor, sonucunu zaten
            # çağıranın kendisi (`istek.result()`) okuyor.
            istek, bagli = asyncio.run(_go())
            self.assertTrue(istek.done())
            self.assertFalse(bagli, "biten isteğe gereksiz okuyucu takıldı")


if __name__ == "__main__":
    unittest.main()
