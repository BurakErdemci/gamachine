"""Bağlam göstergesinin tek kaynağı — ve o kaynağın DÜRÜSTLÜĞÜ.

30 Ağu 2026'ya kadar bu akışın hiçbir testi yoktu: ne formülün, ne olay
şemasının, ne de göstergenin çizilmesinin. Aynı formülün iki kopyası vardı
(burada ve `useChat.ts`'te) ve ikisi de sessizce ayrışabilirdi.

Buradaki testler üç şeyi sabitliyor:
  1. Yükün `estimated` damgası — sayı bir ölçüm değil, tahmin. Damga düşerse
     arayüz onu kesin bir sayı gibi gösterir.
  2. Turun GERÇEK token'ları elde varsa yüke giriyor, yoksa uydurulmuyor.
     8 çalıştırma yolunun 4'ünde (Codex, agy, oneshot CLI, `_run_simple`)
     hiç token verisi yok — o yollarda alan BULUNMAMALI, sıfır olmamalı.
  3. Sayılan şeyin ne olduğu: yalnız DB'ye yazılan mesaj metni. Bu bir kusur
     ve bilerek kayıtlı; test onu sabitliyor ki sessizce değişmesin.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from routes.conversation_routes import _context_usage_payload, _fmt_tokens, _MAX_CONTEXT_CHARS


def _db(*contents):
    db = MagicMock()
    db.get_conversation_messages.return_value = [{"content": c} for c in contents]
    return db


def test_percent_comes_from_stored_message_chars():
    db = _db("a" * 20_000, "b" * 20_000)
    p = _context_usage_payload(db, 1)
    assert p["total_chars"] == 40_000
    assert p["max_chars"] == _MAX_CONTEXT_CHARS
    assert p["percent"] == 20
    assert p["message_count"] == 2


def test_the_number_is_always_stamped_as_an_estimate():
    # Damga düşerse arayüz tahmini ölçüm gibi gösterir — bu akışın en pahalı
    # yanlışı o olurdu, çünkü sayı zaten sistematik olarak EKSİK sayıyor.
    assert _context_usage_payload(_db("x"), 1)["estimated"] is True


def test_compact_flag_trips_at_85_percent():
    assert _context_usage_payload(_db("x" * 169_000), 1)["should_compact"] is False
    assert _context_usage_payload(_db("x" * 171_000), 1)["should_compact"] is True


def test_percent_is_capped_so_a_long_chat_cannot_report_over_100():
    assert _context_usage_payload(_db("x" * (_MAX_CONTEXT_CHARS * 3)), 1)["percent"] == 100


def test_a_path_without_token_data_carries_no_last_turn_field():
    # Codex / agy / cursor / copilot / opencode / kimi hiç `turn_usage`
    # yaymıyor. Alanı sıfırla doldurmak "0 token harcandı" demek olurdu.
    p = _context_usage_payload(_db("x"), 1, None)
    assert "last_turn" not in p


def test_real_tokens_ride_along_when_the_path_produced_them():
    usage = {"input_tokens": 41_200, "output_tokens": 830, "cost_usd": 0.21, "duration_ms": 9_100}
    p = _context_usage_payload(_db("x"), 1, usage)
    assert p["last_turn"] == {"input_tokens": 41_200, "output_tokens": 830, "cost_usd": 0.21}


def test_a_path_with_tokens_but_no_cost_keeps_cost_none_rather_than_zero():
    # Yalnız Claude Code yolunda `cost_usd` doluyor; diğer üç yayan yolda None.
    # None "bilmiyoruz", 0 "bedava" demek — ikisi aynı şey değil.
    p = _context_usage_payload(_db("x"), 1, {"input_tokens": 10, "output_tokens": 2, "cost_usd": None})
    assert p["last_turn"]["cost_usd"] is None


def test_none_content_rows_do_not_crash_the_count():
    db = MagicMock()
    db.get_conversation_messages.return_value = [{"content": None}, {}, {"content": "abc"}]
    assert _context_usage_payload(db, 1)["total_chars"] == 3


@pytest.mark.parametrize("tokens, formatted", [
    (200_000, "200k"), (1_000_000, "1M"), (112_400, "112.4k"), (999, "999"),
])
def test_token_formatting(tokens, formatted):
    assert _fmt_tokens(tokens) == formatted


@pytest.mark.parametrize("percentage, rounded, compact", [
    (56.2, 56, False), (84.4, 84, False), (84.6, 85, True),
    (85.0, 85, True), (120.0, 100, True),
])
def test_live_reading_overrides_only_the_gauge(percentage, rounded, compact):
    db = _db("x" * 10_000, "y" * 10_000)
    usage = {"input_tokens": 10, "output_tokens": 2, "cost_usd": None}
    estimate = _context_usage_payload(db, 1, usage)
    payload = _context_usage_payload(db, 1, usage, {
        "used": 112_400, "window": 200_000, "percent": percentage, "model": "claude-test",
    })
    assert payload["estimated"] is False
    assert payload["percent"] == rounded
    assert payload["should_compact"] is compact
    assert payload["real"] == {"used": "112.4k", "total": "200k", "model": "claude-test"}
    for key in ("type", "total_chars", "max_chars", "message_count", "last_turn"):
        assert payload[key] == estimate[key]


@pytest.mark.parametrize("reading", [None, {"window": 0}, {"window": -1}])
def test_missing_or_unusable_reading_keeps_the_estimate(reading):
    db = _db("x" * 50_000)
    assert _context_usage_payload(db, 1, reading=reading) == _context_usage_payload(db, 1)


def test_real_reading_without_model_uses_none():
    payload = _context_usage_payload(_db("x"), 1, reading={
        "used": 999, "window": 1_000_000, "percent": 0.1, "model": "",
    })
    assert payload["real"] == {"used": "999", "total": "1M", "model": None}
