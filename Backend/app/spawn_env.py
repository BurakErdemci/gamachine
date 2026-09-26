"""Alt süreçlere geçen ortamın izin listesi — spawn eden HER yerin tek kaynağı.

Neden `providers/cli_base.py` içinde değil (taşındı 2026-07-29): bu filtreyi
kullanan altı çağrı yerinin YALNIZ ikisi bir CLI sağlayıcısı —
`unity_mcp_manager` (uvx torunu), `routes/config_routes` (model listesi),
`agentic/agent_runner` (Claude SDK) ve `providers/video_extract` (ffmpeg/yt-dlp)
sağlayıcı değil. Bir dict filtresi almak için 1195 satırlık sağlayıcı taban
sınıfını import etmek zorunda kalmak, o dosyalarda altı ayrı FONKSİYON-İÇİ
geç-import yazılmasına yol açmıştı (import döngüsü ve açılış maliyeti).

`cli_base` bu adları geriye dönük uyumluluk için yeniden dışa veriyor;
`from providers.cli_base import build_spawn_env` yazan çağrı yerleri ve testler
aynen çalışmaya devam ediyor.
"""
import os
from typing import Dict, Optional

# ── Alt sürece geçen ortamın İZİN LİSTESİ ────────────────────────────────────
#
# Neden allow-list, neden `{**os.environ}` değil (ölçüm 2026-07-28): altı canary
# ebeveynin ortamına konup her spawn noktasında çocuğun gördüğü env yakalandı;
# beş spawn noktasının BEŞİNDE de ALTISI birden geçiyordu — LOCAL_APP_TOKEN,
# API_KEY_ENCRYPTION_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY,
# AWS_SECRET_ACCESS_KEY. Yani kullanıcı Cursor'u seçtiğinde `cursor-agent`
# süreci Anthropic anahtarını, veritabanı şifreleme anahtarını ve backend'in tek
# yetki kanıtı olan bearer token'ı okuyabiliyordu.
#
# Aynı desen C# tarafında zaten uygulanmıştı (`omnisharp_manager._ENV_ALLOWLIST`);
# burası onun sağlayıcı tarafındaki eşi. Ondan farkı İKİ KATMANLI olması:
#   (a) _BASE_ENV_ALLOWLIST — her sağlayıcının çalışması için gereken işletimsel
#       taban (yol, ev dizini, yerel ayar, proxy, TLS kökü, node araç zinciri),
#   (b) _PROVIDER_ENV_ALLOWLIST — yalnız O sağlayıcının KENDİ kimlik/uç-nokta
#       değişkenleri. Bu ayrım düzeltmenin bütün değeri: tek düz liste ya
#       sızıntıyı sürdürür ya da env ile giriş yapan kullanıcının CLI'ını kırar.
#
# ⚠️ Listeyi DARALTIRKEN dikkat: buradan düşen bir ad sağlayıcının hiç
# çalışmamasına yol açar ve arıza SESSİZDİR — CLI bulunamaz, ya da kurumsal ağda
# her HTTPS çağrısı sertifika hatasıyla düşer, ya da CLI oturum dosyasını bulamayıp
# "giriş yapın" der. Bu düzeltmenin başarısızlık yönü sızıntı değil, ÜRÜNÜN
# SESSİZCE KIRILMASI.
#
# Liste bilerek TEK parça: Windows'a özgü adlar da burada duruyor ve yalnız
# gerçekten VAR olanlar kopyalanıyor (macOS'ta hiçbiri eklenmiyor). os.name'e
# göre dallanmak Windows dalını macOS'tan sınanamaz kılardı — bu depoda
# dallanmayı azaltmak doğrulanabilirliğin kendisi.
_BASE_ENV_ALLOWLIST = (
    # Süreç başlatma ve dosya sistemi. PATH olmadan CLI binary'si hiç bulunamaz
    # (main.py PATH'i ayrıca augment ediyor: Finder'dan başlatılan uygulamanın
    # PATH'i npm/nvm/homebrew dizinlerini taşımıyor). HOME her CLI'ın oturum ve
    # config dizini için zorunlu — ~/.claude, ~/.codex, ~/.gemini, ~/.kimi-code
    # hepsi oradan çözülüyor; HOME düşerse kullanıcı her turda "giriş yapın" alır.
    "PATH", "HOME", "TMPDIR", "TMP", "TEMP",
    # Yerel ayar. Sohbet Türkçe: UTF-8 olmayan bir çocukta CLI'ın kendi çıktısı
    # ve dosya adları bozuluyor, JSONL parse'ı da bundan etkileniyor.
    "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ",
    # Kullanıcı kimliği (sır değil). CLI'lar `run_command` için kabuk açıyor;
    # SHELL düşerse /bin/sh'a düşülüyor ve kullanıcının kabuk ayarları kayboluyor.
    "USER", "LOGNAME", "SHELL",
    # ssh-agent soketi. Modeller onay kapısından geçerek `git push` çalıştırıyor;
    # bu düşerse SSH remote'lu her push parola sorup takılıyor. Soketin kendisi
    # bir sır değil ve çocuk zaten aynı kullanıcı olarak ~/.ssh'ı okuyabiliyor —
    # yani elemek güvenlik satın almadan kullanılabilirlik satardı.
    "SSH_AUTH_SOCK",
    # Terminal. NO_COLOR/TERM/COLUMNS/LINES çağrı yerinde ZORLA ezilir (overrides),
    # ama COLORTERM gibi kalanlar buradan geçiyor.
    "TERM", "COLORTERM",
    # XDG dizinleri: Linux'ta ve bazı CLI'larda config/cache kökü HOME değil bunlar.
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR",
    # Kurumsal proxy. Küçük harfli biçimler ayrı adlardır ve bazı istemciler
    # (curl, node'un undici'si) yalnız birini okuyor — ikisi de geçmeli.
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    # TLS güven kökü. TLS'i kesen bir kurumsal ağda bunlardan biri düşerse
    # sağlayıcıya giden HER istek sertifika hatasıyla ölür — ve kullanıcının
    # gördüğü mesaj sebebi tamamen gizler.
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    # Node araç zinciri: claude/codex/agy/copilot/opencode npm CLI'ları ve
    # `node` çoğu makinede nvm/fnm/volta üzerinden geliyor. Bu değişkenler
    # düşerse PATH'teki shim doğru node kurulumunu bulamıyor.
    # ⚠️ NPM_TOKEN / NODE_AUTH_TOKEN BİLEREK YOK: registry kimlik bilgisi, sır.
    "NODE_OPTIONS", "NODE_PATH", "NVM_DIR", "NVM_BIN", "NVM_INC",
    "NPM_CONFIG_PREFIX", "NPM_CONFIG_USERCONFIG", "NPM_CONFIG_CACHE",
    "FNM_DIR", "FNM_MULTISHELL_PATH", "VOLTA_HOME", "COREPACK_HOME",
    # Bizim işletimsel değişkenlerimiz (sır DEĞİL — localhost adresi ve klasör
    # yolu). MCP sunucusu ve `unityai` CLI'ı backend'i bunlardan buluyor.
    "UNITYAI_URL", "ANTIGRAVITY_URL", "WORKSPACE",
    # Windows. macOS'tan sınanamayan tek yüzey burası olduğu için adlar tek
    # listede duruyor ve varlıklarına göre kopyalanıyor (bkz. testler).
    "SystemRoot", "SystemDrive", "WINDIR", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "APPDATA", "LOCALAPPDATA", "ProgramData", "ProgramFiles", "ProgramFiles(x86)",
    "ProgramW6432", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432", "ALLUSERSPROFILE",
    "PUBLIC", "USERDOMAIN", "COMPUTERNAME", "OS", "SESSIONNAME",
)

# BİLEREK DIŞARIDA BIRAKILANLAR — her biri bir karar, unutulmuş bir ad değil:
#   LOCAL_APP_TOKEN        backend'in tek yetki kanıtı. MCP sunucusu ona ortamdan
#                          DEĞİL 0600 bir dosyadan ulaşıyor (bkz. local_token_file),
#                          yani elenmesi hiçbir yolu kırmıyor.
#   API_KEY_ENCRYPTION_KEY veritabanındaki kullanıcı API anahtarlarının şifreleme
#                          anahtarı; hiçbir CLI'ın işi yok.
#   AWS_*                  Claude Code'un Bedrock kipi bunlarla çalışıyor ama
#                          AWS_SECRET_ACCESS_KEY ölçülen canary'lerden biri ve
#                          bir sır. Bedrock/Vertex kipi bilinçli olarak
#                          desteklenmiyor — kullanıcı `claude login` kullanır.
#   PYTHONPATH/PYTHONHOME/VIRTUAL_ENV
#                          backend'in kendi venv'i; çocuğun python'una dayatılırsa
#                          çocuk yorumlayıcısı kırılır (launcher'lar PYTHONPATH'i
#                          zaten kendileri kuruyor).
#   Sağlayıcıya ait olmayan tüm *_API_KEY / *_TOKEN / *_SECRET adları.

# Sağlayıcıya ÖZEL katman: yalnız o ailenin kendi kimlik ve uç-nokta değişkenleri.
# Anahtarlar `env_family()` üzerinden çözülüyor ve o fonksiyonun önekleri
# `manager.get_provider`'ın sevk tablosuyla AYNI olmak zorunda.
_PROVIDER_ENV_ALLOWLIST = {
    # Claude Code. Abonelikle (claude login) çalışırken hiçbiri gerekmiyor; ama
    # ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN ile giriş yapmış kurulumlar var ve
    # bunları elemek onların CLI'ını sessizce auth hatasına düşürürdü.
    # BASE_URL/CUSTOM_HEADERS kurumsal gateway arkasındaki kurulumlar için.
    "claude": (
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
        "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    ),
    # Codex. CODEX_HOME bu depoda zaten okunuyor (codex_session._configured_codex_mcp_names)
    # → config.toml'un yerini o belirliyor; düşerse MCP kayıtları görünmez olur.
    "codex": (
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID",
        "CODEX_HOME",
    ),
    # Antigravity/Gemini CLI. GOOGLE_APPLICATION_CREDENTIALS bir DOSYA YOLU;
    # sırrın kendisi değil ve dosyayı aynı kullanıcı zaten okuyabiliyor.
    "agy": (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION", "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_APPLICATION_CREDENTIALS", "GEMINI_CONFIG_DIR",
    ),
    # Kimi Code. KIMI_CODE_HOME bu depoda okunuyor (kimi_provider.py).
    "kimi": ("KIMI_CODE_HOME", "KIMI_API_KEY", "MOONSHOT_API_KEY"),
    "cursor": ("CURSOR_API_KEY", "CURSOR_CONFIG_DIR"),
    # Copilot CLI kimliğini gh oturumundan ya da bu token'lardan alıyor.
    "copilot": ("GITHUB_TOKEN", "GH_TOKEN", "GH_CONFIG_DIR", "GH_HOST"),
    # ⚠️ OpenCode ÇOK SAĞLAYICILI: kullanıcı onu istediği modelin sağlayıcısıyla
    # kullanabiliyor, yani "kendi" kimlik değişkeni tek bir vendor'a ait değil.
    # Vendor anahtarları bilerek verilmiyor (verilseydi bu düzeltmenin amacı
    # OpenCode için tamamen kalkardı); kimlik `opencode auth login` ile kurulan
    # disk oturumundan gelmeli.
    "opencode": ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT", "OPENCODE_DISABLE_AUTOUPDATE"),
    # Unity MCP'yi başlatan `uvx` torunu. Her açılışta PyPI'dan indirdiği için
    # önbellek/python dizinleri işletimsel olarak gerekli.
    # ⚠️ UV_INDEX_URL / UV_DEFAULT_INDEX BİLEREK YOK: kimlik bilgisi gömülü bir
    # URL olabiliyor (https://user:token@...), yani sır sınıfına giriyor.
    "uvx": (
        "UV_CACHE_DIR", "UV_NO_CACHE", "UV_OFFLINE", "UV_PYTHON",
        "UV_PYTHON_INSTALL_DIR", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "UV_NATIVE_TLS",
    ),
}

# binary_name öneki → aile. Sıra önemli DEĞİL (önekler ayrık) ama içerik
# `manager.get_provider`'ın if/elif zinciriyle birebir aynı olmak zorunda:
# ayrışırlarsa bir sağlayıcı sessizce kendi kimlik değişkenini kaybeder.
_FAMILY_PREFIXES = (
    # Tire YOK: buraya hem model adı ("cursor-gpt-5.2") hem çıplak CLI adı
    # ("cursor", `oneshot_cli.probe_named_models`) geliyor. Tireli desen çıplak
    # adı kaçırıyordu ve bilinmeyen ad "claude"a düştüğü için Cursor'un süreci
    # ANTHROPIC_API_KEY'i almaya devam ediyordu — sızıntı kapatılmış görünürken
    # tek çağrı yerinde açık kalıyordu (ölçüldü 2026-07-28, testle sabitlendi).
    # ⚠️ 2026-07-29'da AYNI tuzağa ikinci kez düşüldü, bu sefer "opencode:"da:
    # `config_routes._run_cli_capture` `opencode models` çalıştırırken elinde
    # ÇIPLAK "opencode" var. "opencode:" öneki onu kaçırıyor, bilinmeyen ad
    # "claude"a düşüyor ve OpenCode'un çocuğu ANTHROPIC_API_KEY'i alıyordu —
    # yani aşağıdaki `_PROVIDER_ENV_ALLOWLIST["opencode"]` yorumunun "vendor
    # anahtarları bilerek verilmiyor" beyanı tam da o çağrı yerinde bozuluyordu.
    # Bu yüzden bütün önekler artık ÇIPLAK ada da uyuyor; tireli/iki noktalı
    # biçimler zaten çıplak adın uzantısı olduğu için tek entry ikisini de tutar.
    ("cursor", "cursor"),
    ("copilot", "copilot"),
    ("opencode", "opencode"),
    ("gpt-", "codex"),
    ("codex", "codex"),
    ("kimi", "kimi"),
    ("gemini", "agy"),
    ("agy", "agy"),
)


def env_family(binary_name: Optional[str]) -> Optional[str]:
    """`binary_name` → izin listesi ailesi.

    Bilinmeyen ad "claude" döner çünkü `manager.get_provider` de bilinmeyen adı
    ClaudeCodeProvider'a düşürüyor ("claude-* ve diğerleri"). İki tablonun
    ayrışması, çalışan bir sağlayıcının kimlik değişkenini sessizce kaybetmesi
    demek olurdu — bu depodaki arızaların ortak şekli tam olarak "birbiriyle
    uyuşması gereken iki yer uyuşmuyor".
    """
    if not binary_name:
        return None
    for prefix, family in _FAMILY_PREFIXES:
        if binary_name.startswith(prefix):
            return family
    return "claude"


def build_spawn_env(family: Optional[str] = None,
                    overrides: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Alt süreç ortamını izin listesinden kurar. ASLA `None` dönmez.

    `None` dönmek subprocess'e "ebeveyn ortamını AYNEN devral" demek, yani
    sızıntıyı hiç kapatmayan dal olurdu — OmniSharp tarafında tam olarak bu dal
    gözden kaçmıştı (dış denetim, 2026-07-27).

    Var olmayan bir ad UYDURULMUYOR: boş dizeyle tanımlı bir değişken tanımsız
    olmakla aynı şey değil (örn. `HTTPS_PROXY=""` gören bir HTTP istemcisi boş
    proxy'yi kullanmayı deneyip bağlantıyı düşürebiliyor).

    Bunun SİMETRİĞİ de kasıtlı: ebeveynde VAR olan boş bir değer (`COLORTERM=""`)
    çocuğa aynen geçer, elenmez. Sebep aynı ayrımın diğer yönü — elemek, çocuğa
    ebeveynden farklı bir dünya göstermek olurdu ve "tanımlı ama boş" ile
    "hiç tanımlı değil" arasında ayrım yapan bir CLI'ı sessizce kırardı. Bu
    filtre yalnız hangi ADLARIN geçeceğine karar verir, değerlerine karışmaz;
    boş değer geçişi de bu fonksiyondan önceki `{**os.environ}` davranışıyla
    aynıdır, yani bir regresyon değil korunmuş davranıştır. (Dış denetim
    2026-07-28'de üstteki paragrafı "boşlar eleniyor" iddiası sanıp bunu bulgu
    olarak yazdı; iddia hiç yapılmamıştı — bu paragraf o yanlış okumayı kapatır.)

    `overrides` en son uygulanır ve izin listesini EZER: çağrı yerinin bilerek
    dayattığı değerler (NO_COLOR, ya da Unity MCP'nin argv yerine ortamla
    geçirdiği paylaşımlı sır) buradan geçer.
    """
    names = _BASE_ENV_ALLOWLIST + _PROVIDER_ENV_ALLOWLIST.get(family or "", ())
    env = {name: os.environ[name] for name in names if name in os.environ}
    if overrides:
        env.update(overrides)
    return env


# The unityai approval bridge and the Unity MCP stdio bridge read this to name
# the chat their approval cards belong to.
CONVERSATION_ENV = "GAMACHINE_CONVERSATION_ID"


def conversation_env(conversation_id) -> Dict[str, str]:
    """`overrides` entry naming the chat a spawned CLI works for, or {}.

    Only for a process dedicated to one chat: ids <= 0 are throwaway one-shots
    and side calls have none, and a card owned by the wrong chat is worse than
    an unowned one. Same guard as the Claude session's header (agent_runner).
    """
    if type(conversation_id) is int and conversation_id > 0:
        return {CONVERSATION_ENV: str(conversation_id)}
    return {}
