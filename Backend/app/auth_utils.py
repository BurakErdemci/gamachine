import hmac
import os
from pathlib import Path, PurePath
from typing import Any

from fastapi import HTTPException

_LOCAL_USER = (1, "local", "local@localhost", None, None)


def _check_token(token) -> None:
    """LOCAL_APP_TOKEN her çağrıda env'den okunur (import-time değil — test güvenliği).

    Fail-CLOSED: token yoksa istek reddedilir. Eskiden tam tersiydi — token
    kurulmamışsa kontrol tamamen atlanıyordu, yani backend tek başına
    çalıştırıldığında (dev, ya da frozen binary'nin doğrudan koşturulması)
    127.0.0.1:8000'deki her uç kimliksizdi: /write-file, /chat, api-keys… Aynı
    "yapılandırılmamış = korumasız" kalıbını 2026-07-27 denetiminde Unity MCP
    kontrol düzleminde de bulduk; sessiz açık kapı, açık kapıdır.

    Paketlenmiş uygulama token'ı zaten veriyor (background.ts). Token'sız
    çalıştırmak isteyen UNITYAI_ALLOW_NO_TOKEN=1 ile bunu AÇIKÇA seçer.
    """
    app_token = os.environ.get("LOCAL_APP_TOKEN", "")
    if not app_token:
        if os.environ.get("UNITYAI_ALLOW_NO_TOKEN", "").lower() in {"1", "true", "yes", "on"}:
            return
        raise HTTPException(
            status_code=503,
            detail="LOCAL_APP_TOKEN tanımlı değil. Dev için UNITYAI_ALLOW_NO_TOKEN=1 kullanın.",
        )
    # compare_digest: token uzunluğu/eşleşme süresi üzerinden sızıntıyı kapatır.
    # BAYT üzerinden, str üzerinden DEĞİL: compare_digest'in str aşırı yüklemesi
    # ASCII dışı girdide TypeError fırlatıyor ("comparing strings with non-ASCII
    # characters is not supported"). Başlık değeri saldırgan denetiminde ve tel
    # üzerinde bayt olduğu için, 0x80-0xFF gönderen bir yerel soket eskiden her
    # korumalı uçta kimliksiz 500 üretebiliyordu (ölçüldü 2026-07-27). Ayrıca
    # LOCAL_APP_TOKEN'a elle Türkçe karakterli bir değer yazılırsa DOĞRU token
    # bile 500 veriyordu — paketlenmiş app randomUUID() ürettiği için oraya
    # düşmüyor, ama .env'i elle dolduran biri backend'i kilitliyordu.
    # Aynı tuzak gömülü sunucuda zaten biliniyordu: unity-mcp/Server/src/core/
    # local_auth.py bunu bayta çevirerek çözmüş; backend o dersi almamıştı.
    if not hmac.compare_digest(str(token or "").encode("utf-8"), app_token.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Geçersiz uygulama token'ı")


def require_user(db: Any, token: str, expected_user_id: int = None) -> tuple[int, str]:
    """Token'ı doğrula; istenen kullanıcı YEREL kullanıcı olmalı.

    Local mode'da tek kullanıcı var (`_LOCAL_USER`, id=1), yani "hangi kullanıcı"
    sorusunun tek doğru cevabı var. Eskiden `expected_user_id` okunmadan atılıyordu:
    çağıran 999999 gönderdiğinde istek kabul edilip sessizce 1'e çevriliyordu, yani
    adı bir kimlik doğrulaması vaat eden fonksiyon hiçbir kimlik doğrulamıyordu
    (dış denetim, 30 Ağu 2026). Bu ikinci bir insan kullanıcıya karşı gizlilik
    sınırı DEĞİL — ama bir rotanın bu vaade dayanabilmesi için vaadin tutması şart.
    """
    _check_token(token)
    if expected_user_id is not None and int(expected_user_id) != _LOCAL_USER[0]:
        raise HTTPException(status_code=403, detail="Bu kullanıcıya erişim yok")
    return (_LOCAL_USER[0], _LOCAL_USER[1])


def get_current_user(db: Any, token: str) -> tuple[int, str]:
    _check_token(token)
    return (_LOCAL_USER[0], _LOCAL_USER[1])


def _require_owned(db: Any, token: str, obj_id: int, lookup: str, ne: str) -> tuple[int, int]:
    """Token + NESNE kontrolü: kayıt var mı ve yerel kullanıcıya mı ait.

    Var olma sebebi: bu iki bağımlılık adlarında "owner" taşıyıp yalnızca uygulama
    token'ına bakıyordu — veritabanına hiç sorulmuyor, `conv_id`/`item_id` hiç
    okunmuyordu (dış denetim, 30 Ağu 2026). Var olmayan bir id ile çağrılan uçlar
    reddedilmek yerine boş rapor döndürüyordu. Sorgu ucuz ve indeksli (birincil
    anahtar üzerinde tek satır), yani vaadi tutmanın bedeli yok.

    Kayıt yoksa 404: "yok" ile "senin değil" ayrımı burada anlamlı değil (tek
    kullanıcı), ve 404 çağırana doğru olanı söylüyor.
    """
    user_id, _ = require_user(db, token)
    getter = getattr(db, lookup, None)
    if getter is None:
        # DB katmanı bu aramayı sunmuyorsa AÇIKÇA patla: sessizce kabul etmek
        # tam olarak düzeltilen arızanın kendisiydi.
        raise HTTPException(status_code=500, detail=f"db.{lookup} yok")
    owner = getter(obj_id)
    if owner is None:
        raise HTTPException(status_code=404, detail=f"{ne} bulunamadı")
    if int(owner) != user_id:
        raise HTTPException(status_code=403, detail=f"Bu {ne} size ait değil")
    return (user_id, obj_id)


def require_conversation_owner(db: Any, token: str, conv_id: int) -> tuple[int, int]:
    return _require_owned(db, token, conv_id, "get_conversation_owner", "sohbet")


def is_path_within_workspace(file_path: str, workspace_path: str) -> bool:
    try:
        resolved_file = Path(file_path).resolve(strict=False)
        resolved_workspace = Path(workspace_path).resolve(strict=False)
        return resolved_workspace == resolved_file or resolved_workspace in resolved_file.parents
    except Exception:
        return False


def is_allowed_unity_script_path(file_path: str, workspace_path: str) -> bool:
    if not is_path_within_workspace(file_path, workspace_path):
        return False

    resolved_file = Path(file_path).resolve(strict=False)
    resolved_workspace = Path(workspace_path).resolve(strict=False)
    try:
        rel_parts = resolved_file.relative_to(resolved_workspace).parts
    except ValueError:
        return False

    if len(rel_parts) < 3:
        return False
    if rel_parts[0] != "Assets" or rel_parts[1] != "Scripts":
        return False

    return PurePath(file_path).suffix.lower() == ".cs"
