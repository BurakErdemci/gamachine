"""Translate UI notices at the API boundary, preserving legacy callers."""
import json
import re

from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException
from starlette.requests import Request


EN: dict[str, str] = {
    "İşlem durduruldu.": "Operation stopped.",
    "🛑 İşlem kullanıcı tarafından durduruldu.": "🛑 Operation stopped by the user.",
    "Sırada — başka bir agy sohbetinin turu bitince başlayacak":
        "Queued — will start when another agy chat finishes its turn",
    "Çalışıyor…": "Working…",
    "🔔 Arka plan görevleri bitti — devam ettiriliyor…": "🔔 Background tasks finished — resuming…",
    "Codex app-server süreci beklenmedik şekilde kapandı.": "The Codex app-server process exited unexpectedly.",
    "AI yanıt üretemedi.": "AI could not produce a response.",
    "⚠️ Claude session yanıt vermedi — yeniden başlatılıyor…": "⚠️ Claude session did not respond — restarting…",
    "Kurulum penceresi açıldı.": "Installation window opened.",
    "Giriş penceresi açıldı.": "Sign-in window opened.",
    "Unity MCP paketi başarıyla kuruldu.": "Unity MCP package installed successfully.",
    "Yan soru akışı sırasında bir hata oluştu. Ayrıntı sunucu loglarında.":
        "An error occurred while streaming the side question. See the server logs for details.",
    "Yanıt akışı sırasında bir hata oluştu. Ayrıntı sunucu loglarında.":
        "An error occurred while streaming the response. See the server logs for details.",
    "Sohbet zaten kısaydı; özet üretilmedi ama bağlam sıfırlandı.":
        "The chat was already short; no summary was produced, but the context was reset.",
    "Projede analiz edilecek dosya bulunamadı.": "No files to analyze were found in the project.",
    "Bu yanıt sohbet geçmişine kaydedilemedi; pencereyi kapatırsan kaybolur.":
        "This response could not be saved to chat history; it will be lost if you close the window.",
    "aşama: kayıt": "stage: saving",
    "Video işlenemedi; video atlandı, sohbet metinle sürüyor.":
        "The video could not be processed; it was skipped and the chat continues with text.",
    ("📁 Çalışma klasörü bulunamadı (silinmiş veya taşınmış olabilir). "
     "Lütfen sol üstten yeni bir proje klasörü seçin."):
        "📁 Working folder not found (it may have been deleted or moved). "
        "Please choose a new project folder at the top left.",
    ("⚠️ İstek CLI'a iletilemedi (süreç girdi kanalını erken kapattı). "
     "Mesajınız işlenmedi — lütfen tekrar deneyin."):
        "⚠️ The request could not be delivered to the CLI (the process closed its input early). "
        "Your message was not processed — please try again.",
    ("🚦 Claude isteği kullanım penceresi nedeniyle bekliyor — "
     "bu tüm hesap erişiminin kapandığı anlamına gelmeyebilir "
     "(Durdur ile başka modele geçebilirsin)"):
        "🚦 Claude is waiting because of a usage window — this may not mean all account access is blocked "
        "(use Stop to switch models)",
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
    (re.compile(r"Codex session hatası: Codex thread/start başarısız: (?P<error>.*)", re.DOTALL),
     "Codex session error: Codex thread/start failed: {error}"),
    (re.compile(r"Codex thread/start başarısız: (?P<error>.*)", re.DOTALL), "Codex thread/start failed: {error}"),
    (re.compile(r"`(?P<model>.+)` turu nasıl bittiğini bildirmeden kapandı\. Bu backend tarafında bir arıza; turu tekrarla\.", re.DOTALL),
     "`{model}` closed without reporting how the turn ended. This is a backend failure; retry the turn."),
    (re.compile(r"`(?P<model>.+)` turu beklenmedik bir hatayla kesildi \((?P<error>.*)\)\. Tekrar dene ya da başka bir modele geç\.", re.DOTALL),
     "`{model}` turn was interrupted by an unexpected error ({error}). Try again or switch models."),
    (re.compile(r"`(?P<model>.+)` boş bir yanıt döndürdü \(hiç seçenek yok\)\. Bu sağlayıcı tarafında bir arıza; tekrar dene ya da başka bir modele geç\.", re.DOTALL),
     "`{model}` returned an empty response (no choices). This is a provider failure; try again or switch models."),
    (re.compile(r"`(?P<model>.+)` modeli araç çağırmayı desteklemiyor, bu yüzden Unity/dosya araçlarını kullanamıyor\. Araç gerektiren işler için araç çağırabilen bir model seç\.", re.DOTALL),
     "`{model}` does not support tool calls, so it cannot use Unity/file tools. Select a model with tool support for tasks that require tools."),
    (re.compile(r"'(?P<model>.+)' bu sürümde bulut API anahtarıyla araç kullanamıyor: GPT-6 araç çağrısını yalnız Responses API'si üzerinden yapıyor, uygulama ise Chat Completions kullanıyor\. Codex \(abonelik\) yolundan 'Codex \(GPT-6 Astra\)' seçeneğini kullanın ya da başka bir bulut modeli seçin\.", re.DOTALL),
     "'{model}' cannot use tools through a cloud API key in this version: GPT-6 tool calls require the Responses API, "
     "while the app uses Chat Completions. Choose 'Codex (GPT-6 Astra)' through Codex (subscription) or select another cloud model."),
    (re.compile(r"⚠️ (?P<cli>.+) CLI bu bilgisayarda kurulu değil \(PATH'te bulunamadı\)\. Lütfen kurun veya farklı bir model seçin\.", re.DOTALL),
     "⚠️ {cli} CLI is not installed on this computer (not found in PATH). Please install it or select another model."),
    (re.compile(r"❌ CLI hatası: (?P<error>.*)", re.DOTALL), "❌ CLI error: {error}"),
    (re.compile(r"❌ CLI hata \(rc=(?P<rc>.*?)\): (?P<error>.*)", re.DOTALL), "❌ CLI error (rc={rc}): {error}"),
    (re.compile(r"⚠️ Çıktı yok\. Hata: (?P<error>.*)", re.DOTALL), "⚠️ No output. Error: {error}"),
    (re.compile(r"⚠️ CLI yanıt üretmedi: (?P<error>.*)", re.DOTALL), "⚠️ CLI did not produce a response: {error}"),
    (re.compile(r"❌ CLI Bridge Hatası: (?P<error>.*)", re.DOTALL), "❌ CLI Bridge error: {error}"),
    (re.compile(r"Codex turu başlatılamadı: (?P<error>.*)", re.DOTALL), "Could not start the Codex turn: {error}"),
    (re.compile(r"Codex MCP yapılandırması güncellenemedi: (?P<error>.*)", re.DOTALL), "Could not update the Codex MCP configuration: {error}"),
    (re.compile(r"AI hatası: (?P<error>.*)", re.DOTALL), "AI error: {error}"),
    (re.compile(r"Claude hatası: (?P<error>.*)", re.DOTALL), "Claude error: {error}"),
    (re.compile(r"OpenAI/API hatası: (?P<error>.*)", re.DOTALL), "OpenAI/API error: {error}"),
    (re.compile(r"Claude session hatası: (?P<error>.*)", re.DOTALL), "Claude session error: {error}"),
    (re.compile(r"Codex session hatası: (?P<error>.*)", re.DOTALL), "Codex session error: {error}"),
    (re.compile(r"aşama: bilinmiyor · (?P<error>.*)", re.DOTALL), "stage: unknown · {error}"),
    (re.compile(r"⚠️ Claude kullanım limitine yaklaşılıyor(?P<pct>.*)", re.DOTALL), "⚠️ Approaching the Claude usage limit{pct}"),
    (re.compile(r"🤖 Görev başladı: (?P<desc>.*)", re.DOTALL), "🤖 Task started: {desc}"),
    (re.compile(r"(?P<icon>.+) Görev bitti: (?P<desc>.*)", re.DOTALL), "{icon} Task finished: {desc}"),
    (re.compile(r"🤖 Subagent çalışıyor: (?P<desc>.*)", re.DOTALL), "🤖 Subagent running: {desc}"),
    (re.compile(r"⏳ (?P<count>.+) arka plan görevi sürüyor \((?P<names>.*)\) — bitince devam edilecek", re.DOTALL),
     "⏳ {count} background tasks running ({names}) — will resume when finished"),
    (re.compile(r"⏳ (?P<count>.+) arka plan görevi sürüyor", re.DOTALL), "⏳ {count} background tasks running"),
    (re.compile(r"(?P<provider>.+) API key kaydedildi\.", re.DOTALL), "{provider} API key saved."),
    (re.compile(r"⏳ (?P<model>.+) (?P<seconds>.+) sn'dir yanıt vermedi — sağlayıcı hâlâ işliyor \(Durdur ile iptal edebilirsin\)", re.DOTALL),
     "⏳ {model} has not responded for {seconds}s — the provider is still processing (use Stop to cancel)"),
    (re.compile(r"🚦 (?P<provider>Google|Sağlayıcı) (?P<code>.+) döndü \((?P<reason>.*)\) — (?P<seconds>.+) sn bekleniyor, deneme (?P<attempt>.+)/3", re.DOTALL),
     "🚦 Provider returned {code} — waiting {seconds}s, attempt {attempt}/3"),
    (re.compile(r"🚦 Claude bu isteği kullanım penceresi sinyaliyle bekletiyor \((?P<window>.*)\)(?P<when>.*?)\. Bu, hesabındaki tüm Claude erişiminin kapandığı anlamına gelmeyebilir; CLI otomatik yeniden deniyor\. Durdur ile başka modele geçebilirsin\.", re.DOTALL),
     "🚦 Claude is delaying this request due to a usage window ({window}){when}. "
     "This may not mean all Claude access on your account is blocked; the CLI is retrying automatically. Use Stop to switch models."),
    (re.compile(r"⚠️ Claude API: (?P<error>.*) — CLI otomatik yeniden deniyor…", re.DOTALL),
     "⚠️ Claude API: {error} — CLI is retrying automatically…"),
    (re.compile(r"⏳ Claude API (?P<minutes>.+) dk (?P<seconds>.+) sn'dir sessiz — uzun düşünme ya da limit/yoğunluk \(otomatik yeniden deneniyor\)", re.DOTALL),
     "⏳ Claude API has been silent for {minutes}m {seconds}s — extended thinking or rate limits/load (retrying automatically)"),
    (re.compile(r"⏳ CLI uzun süre hiçbir çıktı veya ilerleme üretmediği için (?P<minutes>.+) dakika sonra durduruldu\. Sonraki mesaj temiz bir oturumda sohbet geçmişiyle devam edecek\.", re.DOTALL),
     "⏳ CLI stopped after {minutes} minutes without output or progress. The next message will continue in a fresh session with chat history."),
    (re.compile(r"⏳ CLI güvenlik amacıyla (?P<minutes>.+) dakikalık toplam çalışma tavanında durduruldu\. Sonraki mesaj temiz bir oturumda devam edecek\.", re.DOTALL),
     "⏳ CLI stopped at the {minutes}-minute total runtime safety limit. The next message will continue in a fresh session."),
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


UI_EVENT_TYPES = frozenset({"error", "status", "notice", "warning", "info", "activity"})


def localize_event(event: dict, lang) -> dict:
    localized = event.copy()
    if isinstance(event.get("type"), str) and event["type"] in UI_EVENT_TYPES:
        for field in ("message", "content", "detail", "text"):
            if field in event:
                localized[field] = translate_detail(event[field], lang)
    return localized


async def localize_sse(agen, lang):
    try:
        async for chunk in agen:
            if isinstance(chunk, str) and chunk.startswith("data: "):
                try:
                    event = json.loads(chunk[6:])
                except (ValueError, RecursionError):
                    yield chunk
                    continue
                if isinstance(event, dict):
                    localized = localize_event(event, lang)
                    if localized != event:
                        yield "data: " + json.dumps(localized) + "\n\n"
                        continue
            yield chunk
    finally:
        if hasattr(agen, "aclose"):
            await agen.aclose()


async def localized_http_exception_handler(request: Request, exc: HTTPException):
    # Delegate response construction to FastAPI, including bodyless statuses.
    localized = HTTPException(exc.status_code,
                              translate_detail(exc.detail, request.headers.get("x-ui-lang")),
                              headers=exc.headers)
    return await http_exception_handler(request, localized)
