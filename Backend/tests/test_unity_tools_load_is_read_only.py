"""Araç listesini yüklemek Unity'de HİÇBİR ŞEY DEĞİŞTİRMEMELİ.

Canlı testte ölçülen arıza (31 Tem 2026): kullanıcı uygulamayı açtı, sohbete
tek kelime yazmadı, mod "Otomatik"ti — ve ekrana bir onay kartı geldi:

    execute_code  unity_instance: Körebe@faba8ca040f59ab4  action: execute
    code: UnityEngine.Debug.Log("... MCP bağlantısı kuruldu ✓ ...");

Kaynağı ürünün kendisiydi: `load_unity_tools_async` bağlantı kurulunca Unity
konsoluna dekoratif bir afiş basıyordu ve bunu `execute_code` ile yapıyordu —
yani kütükteki en tehlikeli araçla (keyfi C# derleyip çalıştırma).

Kapı yanlış davranmadı: `ambient_auto_approve` koşan bir tur olmasını şart
koşar (`_AMBIENT_TOPLAM > 0`) ve afiş herhangi bir turun DIŞINDA gönderiliyor,
dolayısıyla oto modda bile oto-onaya giremez. Doğrusu da bu — bir tura
bağlanamayan çağrı, o turun iznini miras alamaz.

Bu test dizeyi değil SINIFI koruyor: araç yükleme yolu hiçbir araç çağrısı
yapmamalı. Yarın afiş yerine "sahneyi bir tazeleyelim" gibi bir çağrı eklenirse
de kırmızı olur. Gerekçe ölçülmüş: anlamsız kartlar refleks-onaya alıştırıyor
ve asıl tehlikeli kartı da okunmadan geçirtiyor.
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from tools import unity_mcp_tools  # noqa: E402


class _SahteArac:
    """`_mcp_schema_to_tool_def`/`_make_tool_function` için asgari araç şeması."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "sahte"
        self.inputSchema = {"type": "object", "properties": {}}


def _yukle(sahte_araclar):
    """`load_unity_tools_async`'i sahte bir sunucuyla koşturur, çağrıları döndürür."""
    # The client is now one long-lived session object; both of its entry
    # points are replaced, so the load path can only reach Unity through them.
    with patch.object(unity_mcp_tools._client, "list_tools",
                      MagicMock(return_value=sahte_araclar)), \
         patch.object(unity_mcp_tools._client, "call_tool",
                      MagicMock(return_value={"ok": True})) as cagri:
        sonuc = asyncio.run(unity_mcp_tools.load_unity_tools_async())
    return sonuc, cagri


def test_arac_yuklemesi_hicbir_arac_CAGIRMAZ():
    sonuc, cagri = _yukle([_SahteArac("manage_scene"), _SahteArac("execute_code")])

    assert sonuc is True, "araç listesi yüklendiğinde True dönmeli"
    assert cagri.call_count == 0, (
        "Araç yükleme yolu Unity'ye çağrı yapıyor. Bu yol herhangi bir turun "
        "DIŞINDA koşuyor, yani yaptığı her mutasyon kullanıcıya sebepsiz bir "
        "onay kartı olarak çıkar (ya da ürün kapalıysa reddedilir). "
        f"Yapılan çağrılar: {cagri.call_args_list}"
    )


def test_execute_code_OZELLIKLE_cagrilmaz():
    # Sınıf testi yeterli olmalı, ama bu satır arızanın tam adını taşıyor:
    # geri gelirse hata mesajı ne olduğunu doğrudan söylesin.
    _, cagri = _yukle([_SahteArac("execute_code")])

    cagrilan = [c.args[0] for c in cagri.call_args_list if c.args]
    assert "execute_code" not in cagrilan, (
        "Bağlantı afişi geri gelmiş olabilir: execute_code keyfi C# derleyip "
        "çalıştırıyor ve dekoratif bir konsol satırı bunu hak etmiyor."
    )


def test_TERS_YON_yukleme_basarisizsa_yine_cagri_yok():
    # Hata yolunda da sessiz kalmalı: "bağlanamadım" diye Unity'ye yazmak,
    # arızayı bildirmek için arızalı yolu kullanmak olurdu.
    with patch.object(unity_mcp_tools._client, "list_tools",
                      MagicMock(side_effect=RuntimeError("Unity yok"))), \
         patch.object(unity_mcp_tools._client, "call_tool",
                      MagicMock()) as cagri:
        sonuc = asyncio.run(unity_mcp_tools.load_unity_tools_async())

    assert sonuc is False
    assert cagri.call_count == 0
