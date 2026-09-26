"""Adında "owner" geçen bağımlılıklar GERÇEKTEN nesneye bakıyor mu.

HANGİ ARIZADAN DOĞDU
    Dış denetim, 30 Ağu 2026: `require_user` `expected_user_id`i okumadan
    atıyordu, `require_conversation_owner` ise ne veritabanına ne de `conv_id`ye
    bakıyordu. İkisi de yalnız uygulama token'ını doğrulayıp sabit yerel
    kullanıcı demetleri döndürüyordu. Yani adları nesne yetkilendirmesi vaat
    eden iki fonksiyon hiçbir nesneyi doğrulamıyordu; var olmayan bir sohbet
    id'siyle çağrılan uçlar reddedilmek yerine BOŞ RAPOR üretiyordu.

    Bu tek kullanıcılı bir masaüstü uygulaması, dolayısıyla ikinci bir insana
    karşı gizlilik açığı DEĞİL. Düzeltmenin sebebi başka: bir rotanın bu
    bağımlılığa dayanabilmesi için vaadin tutması gerekiyor, ve tutmayan bir
    vaat yarın yazılacak rotanın sessiz açığıdır.

NEDEN TOKEN BU DOSYADA AÇIK
    `conftest.py` tüm suite için `UNITYAI_ALLOW_NO_TOKEN=1` veriyor, yani token
    kapısı kapalı koşuyor. Buradaki iddia "token GEÇTİKTEN SONRA da nesne
    kontrol ediliyor mu" olduğu için kapı gerçek token'la kuruluyor —
    `test_authz_matrix.py`nin yaptığının aynısı, aynı gerekçeyle.

NE SINANMIYOR
    Sahiplik AYRIMI (kullanıcı A'nın kaydını B görebilir mi). Yerel modda tek
    kullanıcı var; sınanan şey VARLIK ve yerel kullanıcıya AİTLİK.
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import auth_utils  # noqa: E402
from routes.conversation_routes import create_conversation_router  # noqa: E402

TOKEN = "object-authz-token-0123456789"


@pytest.fixture
def gercek_token(monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
    yield


def _db(owner=1, analysis_owner=1):
    db = MagicMock()
    db.get_conversation_owner.return_value = owner
    db.get_analysis_owner.return_value = analysis_owner
    return db


# ── Bağımlılıkların kendisi ────────────────────────────────────────────────

def test_a_conversation_that_does_not_exist_is_rejected_not_accepted(gercek_token):
    db = _db(owner=None)
    with pytest.raises(HTTPException) as e:
        auth_utils.require_conversation_owner(db, TOKEN, conv_id=999999)
    assert e.value.status_code == 404
    # Asıl iddia: veritabanına GERÇEKTEN soruldu. Eski kod hiç sormuyordu.
    db.get_conversation_owner.assert_called_once_with(999999)


def test_a_conversation_owned_by_someone_else_is_rejected(gercek_token):
    with pytest.raises(HTTPException) as e:
        auth_utils.require_conversation_owner(_db(owner=7), TOKEN, conv_id=5)
    assert e.value.status_code == 403


def test_an_existing_conversation_still_passes(gercek_token):
    assert auth_utils.require_conversation_owner(_db(owner=1), TOKEN, conv_id=5) == (1, 5)


def test_the_analysis_dependency_carries_the_same_promise(gercek_token):
    # Aynı sınıftan bir fonksiyon aynı dosyada aynı hatayı taşıyordu; ikisini
    # ayrı davranışta bırakmak, bir sonraki okuyucunun hangisine güveneceğini
    # bilememesi demek olurdu.
    with pytest.raises(HTTPException) as e:
        auth_utils.require_analysis_owner(_db(analysis_owner=None), TOKEN, item_id=999999)
    assert e.value.status_code == 404
    assert auth_utils.require_analysis_owner(_db(), TOKEN, item_id=3) == (1, 3)


def test_a_user_id_that_is_not_the_local_user_is_rejected(gercek_token):
    with pytest.raises(HTTPException) as e:
        auth_utils.require_user(_db(), TOKEN, expected_user_id=999999)
    assert e.value.status_code == 403
    assert auth_utils.require_user(_db(), TOKEN, expected_user_id=1) == (1, "local")
    # expected_user_id verilmemesi "kim olduğu umurumda değil" demek ve geçerli
    # bir kullanım (uçların çoğu yalnız kimlik doğruluyor).
    assert auth_utils.require_user(_db(), TOKEN) == (1, "local")


def test_a_wrong_token_is_still_rejected_before_any_database_work(gercek_token):
    # Nesne kontrolü eklenirken token kontrolünün önüne geçmediğinin kanıtı:
    # yanlış token'la DB'ye HİÇ gidilmemeli.
    db = _db()
    with pytest.raises(HTTPException) as e:
        auth_utils.require_conversation_owner(db, "YANLIS", conv_id=1)
    assert e.value.status_code == 401
    db.get_conversation_owner.assert_not_called()


# ── Bugün eklenen iki okuma ucu bunu gerçekten devralıyor mu ───────────────

def _uc(path: str, db, **kwargs):
    router = create_conversation_router(db, MagicMock())
    route = next(r for r in router.routes if getattr(r, "path", "") == path)
    return asyncio.run(route.endpoint(x_session_token=TOKEN, **kwargs))


def test_context_usage_for_an_unknown_conversation_is_404_not_an_empty_report(gercek_token):
    """Denetimin somut zararı buydu: uydurulmuş bir id boş rapor döndürüyordu.

    Boş rapor "bu sohbette bağlam yok" demek; doğrusu "böyle bir sohbet yok".
    """
    with pytest.raises(HTTPException) as e:
        _uc("/conversations/{conv_id}/context-usage", _db(owner=None), conv_id=999999)
    assert e.value.status_code == 404


def test_the_session_report_endpoint_checks_the_conversation_too(gercek_token):
    db = _db(owner=None)
    db.get_ai_config.return_value = ("subscription", "claude-opus-5", "", False)
    with pytest.raises(HTTPException) as e:
        _uc("/session-report/{conv_id}/{kind}", db, conv_id=999999, kind="usage")
    assert e.value.status_code == 404


# ── Branching routes (tabs) ────────────────────────────────────────────────

def test_branching_a_foreign_or_unknown_conversation_is_refused(gercek_token):
    for owner, status in ((7, 403), (None, 404)):
        db = _db(owner=owner)
        with pytest.raises(HTTPException) as e:
            _uc("/conversations/{conv_id}/branch", db, conv_id=5)
        assert e.value.status_code == status
        db.create_branch.assert_not_called()


def test_hiding_a_foreign_conversation_is_refused(gercek_token):
    from schemas import HiddenRequest
    db = _db(owner=7)
    with pytest.raises(HTTPException) as e:
        _uc("/conversations/{conv_id}/hidden", db, conv_id=5, req=HiddenRequest(hidden=True))
    assert e.value.status_code == 403
    db.set_conversation_hidden.assert_not_called()
