"""unityMCP araç çağrılarının okuma/yazma sınıflandırması — tek kaynaktan.

Onay kapısının unityMCP için sorduğu soru tek: *bu çağrı bir şey değiştiriyor
mu?* Cevap `unity-mcp/Server/src/services/registry/tool_actions.json` kütüğünde
duruyor (45 araç, action bazında, kanıt referanslarıyla) ve okuyucusu aynı
dizindeki `tool_actions.py`.

**Neden kütüğü kopyalamıyoruz.** Bu depodaki arızaların ortak biçimi
"birbiriyle uyuşması gereken iki yer uyuşmuyor". Muafiyet listesini buraya elle
kopyalamak tam olarak o sınıfı yeniden üretirdi — nitekim kopyalanmış eski liste
`manage_scene` için var olmayan bir `get_info` action'ına muafiyet yazıyordu ve
o satır hiçbir zaman eşleşmedi. Kütük, kaynağa karşı bir ayrışma testiyle
bağlanmış durumda (`unity-mcp/Server/tests/test_tool_actions_ledger.py`).

**Neden `sys.path`'e eklemiyoruz.** Sunucunun `src/` ağacında `services`,
`core`, `utils`, `transport` gibi jenerik üst düzey paketler var; onları bu
sürecin arama yoluna sokmak Backend'in kendi modüllerini gölgeleyebilir.
`tool_actions.py` yalnız stdlib kullandığı için dosya yolundan izole biçimde
yüklenebiliyor. Bu varsayımı `tests/test_unity_tool_policy.py` bir testle
sabitliyor: modül stdlib dışına çıkarsa kırmızı verir.

**Kütük bulunamazsa fail-closed:** hiçbir muafiyet uygulanmaz, yani her çağrı
onay kartına düşer. Sessizce serbest bırakmaktansa gürültülü olmak doğru taraf.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from types import ModuleType
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

# Backend, unityMCP araçlarını bu önekle görüyor.
UNITY_MCP_PREFIX = "mcp__unityMCP__"

_LEDGER_RELPATH = os.path.join(
    "unity-mcp", "Server", "src", "services", "registry", "tool_actions.py"
)

_module: Optional[ModuleType] = None
_load_attempted = False


def _project_root() -> str:
    """`unity_mcp_manager` ile AYNI kural — iki yerde farklı çözmek sapma üretir."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.dirname(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _candidate_paths() -> list[str]:
    """Kütüğün aranacağı TEK yer — paketin kendi içi.

    Burada eskiden ikinci bir aday vardı: `~/.unity_architect_ai/...`, çünkü
    frozen build'de `_ensure_writable_resources()` Server'ı oraya kopyalıyor.
    Dış denetim (29 Tem 2026) bunun bir kod çalıştırma yüzeyi olduğunu gösterdi
    ve ölçüldü: o dizin kullanıcı tarafından YAZILABİLİR, dosya yoksa bile üst
    dizin yazılabilir, ve `_load()` bulduğu Python'u `exec_module` ile
    **kapının kendi sürecinde** çalıştırıp dönüşüne koşulsuz güveniyor. Yani
    oraya tek bir dosya bırakan, bütün unityMCP onay kartlarını kalıcı olarak
    susturabiliyordu — dosya sonradan silinse bile, modül zaten belleğe
    alınmış oluyor.

    Aday KALDIRILDI çünkü hiçbir şey satın almıyordu: `electron-builder.yml`
    `unity-mcp/Server`'ı `extraResources` ile paketin içine kopyalıyor, yani
    birinci aday kurulu her üründe var; ikincisi onun aynı içerikli kopyası.
    Bulunamazsa davranış zaten fail-closed (her çağrı karta düşer) — sessizce
    yazılabilir bir yerden kod yüklemektense gürültülü olmak doğru taraf.

    ⚠️ KALAN ve ÖLÇÜLEMEYEN: Windows'ta kurulum per-user
    (`electron-builder.yml` → `nsis.perMachine: false`), yani birinci adayın
    KENDİSİ de kullanıcı yazılabilir. Bu yol bütünlük doğrulaması istiyor
    (özet karşılaştırma ya da okuyucuyu binary'ye gömmek) ve o bir paketleme
    kararı — burada çözülmedi, deftere yazıldı.
    """
    return [os.path.join(_project_root(), _LEDGER_RELPATH)]


def _load() -> Optional[ModuleType]:
    """Okuyucuyu dosya yolundan, benzersiz bir modül adıyla yükle."""
    global _module, _load_attempted
    if _module is not None or _load_attempted:
        return _module
    _load_attempted = True

    for path in _candidate_paths():
        if not os.path.isfile(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                "unity_mcp_tool_actions_ledger", path
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _module = module
            return _module
        except Exception as e:
            logger.warning(f"[unityMCP politika] Kütük yüklenemedi ({path}): {e}")
    logger.warning(
        "[unityMCP politika] Araç sınıflandırma kütüğü bulunamadı; "
        "hiçbir salt-okuma muafiyeti uygulanmayacak (fail-closed)."
    )
    return None


def reset_cache() -> None:
    """Testler için: bir sonraki çağrıda kütüğü yeniden yükle."""
    global _module, _load_attempted
    _module = None
    _load_attempted = False


def ledger_available() -> bool:
    return _load() is not None


def strip_prefix(tool_name: str) -> Optional[str]:
    """`mcp__unityMCP__manage_scene` → `manage_scene`. unityMCP değilse None."""
    if tool_name.startswith(UNITY_MCP_PREFIX):
        return tool_name[len(UNITY_MCP_PREFIX):]
    return None


def is_unity_mcp_read_only(tool_name: str, params: Mapping[str, Any] | None = None) -> bool:
    """
    unityMCP çağrısı kanıtlanmış biçimde salt-okuma mı?

    unityMCP aracı olmayan her şeye, ve kütük yokken her şeye False döner —
    "emin değilim" cevabı daima "onay iste" tarafına düşer.
    """
    bare = strip_prefix(tool_name)
    if bare is None:
        return False
    module = _load()
    if module is None:
        return False
    try:
        return bool(module.is_read_only(bare, params or {}))
    except Exception as e:
        logger.warning(f"[unityMCP politika] '{bare}' sınıflandırılamadı: {e}")
        return False


def unity_action_risk(bare_name: str, params: Mapping[str, Any] | None = None) -> str:
    """"read", "routine" (a write the balanced mode runs without a card),
    "critical", or "unknown" (a tool the ledger does not know). A missing
    ledger, or one without `is_critical`, answers "critical": fail closed."""
    module = _load()
    if module is None or not hasattr(module, "is_critical"):
        return "critical"
    try:
        if module.tool_entry(bare_name) is None:
            return "unknown"
        if module.is_read_only(bare_name, params or {}):
            return "read"
        return "critical" if module.is_critical(bare_name, params or {}) else "routine"
    except Exception as e:
        logger.warning(f"[unityMCP politika] '{bare_name}' risk sınıflandırılamadı: {e}")
        return "critical"
