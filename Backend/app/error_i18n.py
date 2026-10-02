"""Translate HTTP exception details at the API boundary, preserving legacy callers."""
import re

from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException
from starlette.requests import Request


EN: dict[str, str] = {
    "LOCAL_APP_TOKEN tanımlı değil. Dev için UNITYAI_ALLOW_NO_TOKEN=1 kullanın.":
        "LOCAL_APP_TOKEN is not set. For development, use UNITYAI_ALLOW_NO_TOKEN=1.",
    "Geçersiz uygulama token'ı": "Invalid application token",
    "Bu kullanıcıya erişim yok": "Access to this user is denied",
    "Çok fazla analiz isteği. Lütfen bir dakika bekleyin.": "Too many analysis requests. Please wait a minute.",
    "Bu endpoint devre dışı bırakıldı. Güvenli workspace export kullanın.":
        "This endpoint is disabled. Use the secure workspace export.",
    "Otomatik kurulum şu anda Windows ve macOS'ta destekleniyor.":
        "Automatic installation is currently supported on Windows and macOS.",
    "provider_type gerekli.": "provider_type is required.",
    "API key boş olamaz.": "API key cannot be empty.",
    "Desteklenen: cursor, opencode, copilot, codex": "Supported: cursor, opencode, copilot, codex",
    "Bu CLI'ın kurulumu için Node.js gerekiyor. Önce nodejs.org'dan Node.js kurun (npm ile birlikte gelir).":
        "This CLI requires Node.js. Install Node.js from nodejs.org first (it includes npm).",
    "Çok fazla istek gönderdiniz. Lütfen bir dakika bekleyin.": "Too many requests. Please wait a minute.",
    "Sohbet bulunamadı.": "Chat not found.",
    "Yan sohbet bulunamadı.": "Side chat not found.",
    "Bu sohbette yanıt hâlâ sürüyor; dal açmak için turun bitmesini bekle.":
        "A response is still running in this chat. Wait for the turn to finish before branching.",
    "Sohbetin hafıza dosyası okunamadı; dal açılmadı.":
        "The chat memory file could not be read. No branch was created.",
    "Dalın hafıza kopyası yazılamadı; dal geri alındı.":
        "The branch memory copy could not be saved. The branch was rolled back.",
    "Ana sohbet gizlenemez; yalnız dallar kapatılıp yeniden açılabilir.":
        "The main chat cannot be hidden. Only branches can be closed and reopened.",
    "Workspace yolu bulunamadı.": "Workspace path not found.",
    "AI sağlayıcısına ulaşılamadı.": "Could not reach the AI provider.",
    "İçerik boş olamaz.": "Content cannot be empty.",
    "Hafıza güvenlik denetimi yapılamadı.": "The memory security check could not be completed.",
    "gate_id gerekli": "gate_id is required",
    "enabled true ya da false olmalı.": "enabled must be true or false.",
    "Mod bu kanaldan değiştirilemez.": "The mode cannot be changed through this channel.",
    "Çalışma modu yalnız uygulama arayüzünden değiştirilebilir.":
        "The operating mode can only be changed from the app interface.",
    "mode 'auto', 'balanced' ya da 'step' olmalı.": "mode must be 'auto', 'balanced' or 'step'.",
    "Not bulunamadı.": "Note not found.",
    "Unity Editor açık değil. Lütfen önce Unity'yi açın.": "Unity Editor is not open. Please open Unity first.",
    "Unity MCP sunucusu başlatılamadı.": "The Unity MCP server could not be started.",
    "Paket kurulumu başarısız.": "Package installation failed.",
    "Uzaktan kontrol yalnız uygulama arayüzünden açılabilir.":
        "Remote control can only be enabled from the app interface.",
    "Dosya yalnızca workspace içindeki Assets/Scripts altına yazılabilir.":
        "Files can only be written under Assets/Scripts in the workspace.",
    # These literals reach HTTPException through constants or a local fallback.
    "Terminal.app açılamadı.": "Terminal.app could not be opened.",
    "Bu bir yan sohbet; bu işlem yan sohbete uygulanamaz.":
        "This is a side chat. This operation cannot be applied to it.",
    ("Yan soru Antigravity (agy) ile kullanılamıyor: agy aynı anda tek bir tur "
     "çalıştırıyor, yani yan soru ana sohbetin turunun bitmesini beklerdi. "
     "Yan soru için başka bir model seç."):
        "Side questions are unavailable with Antigravity (agy), which runs one turn at a time. "
        "Choose another model for the side question.",
}

PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"db\.(?P<lookup>.+) yok", re.DOTALL), "db.{lookup} is unavailable"),
    (re.compile(r"(?P<ne>.+) bulunamadı", re.DOTALL), "{ne} not found"),
    (re.compile(r"Bu (?P<ne>.+) size ait değil", re.DOTALL), "This {ne} does not belong to you"),
    (re.compile(r"'(?P<cli>.+)' için otomatik kurulum desteklenmiyor\.", re.DOTALL),
     "Automatic installation is not supported for '{cli}'."),
    (re.compile(r"'(?P<cli>.+)' için giriş akışı desteklenmiyor\.", re.DOTALL),
     "Sign-in is not supported for '{cli}'."),
    (re.compile(r"Analiz sırasında bir hata oluştu: (?P<error>.*)", re.DOTALL),
     "An error occurred during analysis: {error}"),
    (re.compile(r"Güvenlik Riski: Yüklemeye çalıştığınız dosya şüpheli talimatlar içeriyor ve engellendi\. \((?P<audit_result>.*)\)", re.DOTALL),
     "Security risk: The file contains suspicious instructions and was blocked. ({audit_result})"),
]


def translate_detail(detail, lang):
    if lang != "en" or not isinstance(detail, str):
        return detail
    if detail in EN:
        return EN[detail]
    for pattern, template in PATTERNS:
        match = pattern.fullmatch(detail)
        if match:
            return template.format(**match.groupdict())
    return detail


async def localized_http_exception_handler(request: Request, exc: HTTPException):
    # Delegate response construction to FastAPI, including bodyless statuses.
    localized = HTTPException(exc.status_code,
                              translate_detail(exc.detail, request.headers.get("x-ui-lang")),
                              headers=exc.headers)
    return await http_exception_handler(request, localized)
