"""Session hata metinleri SSE ile tarayıcıya gidiyor → sır taşımamalı.

Oturum yapılandırması unityMCP `X-API-Key`'ini taşıyor, dolayısıyla bir istisnanın
`str(e)`'si sırrı içerebiliyor. Claude yolu bunu `redact_secrets` ile temizliyordu;
**Codex yolu TEMİZLEMİYORDU** (8 Ağu 2026'da ölçüldü) — yani sınıf kapatılmış
sanılıyordu ama yalnız raporun adını verdiği yol kapanmıştı. Bu dosya iki yolu
birden bağlıyor.

⚠️ İki farklı güç seviyesi var, karıştırılmasın:
  • `test_codex_...`  DAVRANIŞSAL — gerçek üretim akışını sürüp yayılan olayı okur.
  • `test_iki_yol_...` YAPISAL — kaynakta redaksiyon çağrısının varlığına bakar.
    Zayıf ama ucuz: birinin silinmesini yakalar, doğru çalıştığını KANITLAMAZ.
    Claude yolu için davranışsal test yazılmadı (o yolun kurulumu MCP kaydı ve
    workspace ameliyatı istiyor); bu, bilinen ve kabul edilmiş bir boşluk.
"""
import inspect
import pytest

from agentic.agent_runner import AgentRunner

SIR = "sk-ant-api03-CANARY-DO-NOT-LEAK-0123456789"


def _runner():
    return AgentRunner(
        provider_type="subscription",
        api_key="",
        model_name="gpt-5.6-codex",
        workspace_path="",
        conversation_id=1,
    )


class _PatlayanSession:
    """Bir olay yayar, sonra sırrı taşıyan bir istisnayla patlar.

    Önce bir olay yaymak ŞART: Claude yolu hiç olay akmadan patlayan turu
    "sıkışmış session" sayıp resetleyip TEKRAR deniyor, yani hata dalına
    hiç girmiyor.
    """

    thread_id = "t-1"
    session_id = "s-1"
    _ctx_injected = True
    auto_approve = True

    async def stream(self, message, image_paths=None, **kw):
        yield {"type": "text", "content": "kismi cikti"}
        raise RuntimeError(f"bridge kapandi, header X-API-Key: {SIR}")


@pytest.mark.asyncio
async def test_codex_session_hatasi_SIRRI_SIZDIRMIYOR(monkeypatch):
    from providers import codex_session

    monkeypatch.setattr(codex_session, "get_session", lambda *a, **k: _PatlayanSession())
    monkeypatch.setattr(codex_session, "close_session", lambda *a, **k: None, raising=False)
    # Without this the run spawned real `codex mcp remove/add` against the
    # owner's ~/.codex/config.toml (measured 26 Sep 2026: unityMCP removed,
    # unityai re-pointed at Backend/).
    from providers.codex_provider import CodexProvider
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", lambda self, *a, **k: "")

    hatalar = []
    async for ev in _runner()._run_codex_session("merhaba"):
        if ev.type == "error":
            hatalar.append(ev.data.get("message", ""))

    assert hatalar, "hata olayı hiç yayılmadı — test yanlış yolu sürüyor olabilir"
    birlesik = "\n".join(hatalar)
    assert SIR not in birlesik, f"SIR SSE ile tarayıcıya sızdı: {birlesik[:200]}"
    # Karşıt yön: mesaj tamamen boşalmasın, kullanıcı hâlâ ne olduğunu görsün.
    assert "Codex session hatası" in birlesik


def test_iki_yol_da_redaksiyondan_geciyor():
    """Yapısal nöbetçi: iki istisna dalının İKİSİ de redaksiyon çağırmalı.

    Bu depoda tekrarlayan arıza şekli tam bu: bir düzeltme, raporun adını verdiği
    yolu kapatıp kardeş yolu açık bırakıyor. Burada ikisi bir arada tutuluyor —
    biri silinirse test kırmızıya döner.
    """
    for fn in (AgentRunner._run_claude_session, AgentRunner._run_codex_session):
        src = inspect.getsource(fn)
        assert "_redact(str(e))" in src, (
            f"{fn.__name__} istisna metnini redaksiyondan geçirmiyor — "
            "bu metin SSE ile tarayıcıya gidiyor"
        )
