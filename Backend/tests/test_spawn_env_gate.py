"""KAPI TESTİ: `Backend/app/` altındaki HER alt-süreç başlatma noktası ya
izin listesinden bir ortam alır ya da GEREKÇESİYLE muaf listesindedir.

Neden bir sınıf testi, neden tek tek yol testleri değil (bu depoda üç kez
ölçüldü):
  · 2026-07-27 — OmniSharp'ta `env=None` dalı gözden kaçtı.
  · 2026-07-28 — beş spawn noktası kapatıldı, `oneshot_cli.probe_named_models`
    altıncı olarak açık kaldı. Kapatılırken "altıncı yolu da kapattık" yorumu
    yazıldı.
  · 2026-07-29 — dış denetim YEDİNCİ, SEKİZİNCİ ve DOKUZUNCU noktayı buldu
    (`config_routes._run_cli_capture`, `claude_provider._register_mcp`,
    `codex_provider._register_mcp`). Üçü de canlı canary ile doğrulandı:
    çocuk süreç altı canary'nin ALTISINI da görüyordu.

Yani "yolları tek tek kapatmak" bu depoda üç kez denendi ve üçünde de eksik
kaldı. Bu dosya yolu değil SINIFI kapatır: ONUNCU nokta eklendiğinde kırmızı
olur. Kırmızıyı gören ya `env=` verir ya da muafiyeti gerekçesiyle yazar.

Kaçırma yönü kasıtlı olarak "yanlış alarm" tarafına ayarlı:
  · `env=` HİÇ yoksa            → kırmızı
  · `env=None`                  → kırmızı ("ebeveyni aynen devral" demek)
  · env ifadesinde `os.environ` geçiyorsa → kırmızı (`os.environ.copy()`,
    `{**os.environ, ...}`, `dict(os.environ)` hepsi tam devralma)
  · muafiyet var ama o fonksiyondaki muaf spawn SAYISI değişmişse → kırmızı
    (muaf bir fonksiyona yeni bir sızıntı noktası eklemek sessizce geçmesin)
  · muafiyet yazılmış ama artık o nokta yoksa → kırmızı (bayat muafiyet)
"""
import ast
import os
import sys
from typing import Dict, List, Optional, Tuple

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))

# `subprocess` modülünün süreç başlatan adları. `run`/`call` genel isimler, o
# yüzden yalnız gerçekten subprocess modülüne bağlanmış bir ad üzerinden
# çağrıldıklarında sayılırlar (aşağıda import'lar ayrıştırılıyor).
_SUBPROCESS_SPAWNERS = frozenset({
    "run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput",
})
_ASYNCIO_SPAWNERS = frozenset({"create_subprocess_exec", "create_subprocess_shell"})

# Bu adların hiçbirinde `env` parametresi YOK — yani ortam süzülemez. Kodda
# görünmeleri tek başına bir bulgudur, muafiyeti bile olamaz.
_UNGATEABLE = frozenset({"system", "popen", "execv", "execvp", "execl", "execlp",
                         "spawnv", "spawnvp", "spawnl", "spawnlp"})


class _Site:
    def __init__(self, relpath: str, qualname: str, lineno: int, call: str, gated: bool, why: str):
        self.relpath, self.qualname, self.lineno = relpath, qualname, lineno
        self.call, self.gated, self.why = call, gated, why

    def __repr__(self):
        return f"{self.relpath}:{self.lineno} ({self.qualname}) {self.call} — {self.why}"


def _env_is_inherited(value: ast.expr) -> Tuple[bool, str]:
    """`env=` argümanının değeri ebeveyn ortamını devralıyor mu?

    `os.environ`'ın ifadenin HERHANGİ bir yerinde geçmesi yeterli: `os.environ`,
    `os.environ.copy()`, `dict(os.environ)` ve `{**os.environ, "X": "1"}`
    biçimlerinin dördü de tam devralmadır ve dördü de bu depoda kullanılmış.
    """
    if isinstance(value, ast.Constant) and value.value is None:
        return True, "env=None — ebeveyn ortamı aynen devralınır"

    # TEK DEĞER çekmek devralma DEĞİL: `build_spawn_env(overrides={"X":
    # os.environ.get("X")})` bilerek tek bir adı geçiriyor (unity_mcp_manager
    # paylaşımlı sırrı böyle veriyor). Ayrımı yapmayan bir kural bu noktayı
    # yanlışlıkla "açık" sayıyordu — kapı testinin ilk koşusunda ölçüldü.
    parent = {}
    for node in ast.walk(value):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    for node in ast.walk(value):
        if not (isinstance(node, ast.Attribute) and node.attr == "environ"):
            continue
        p = parent.get(node)
        if isinstance(p, ast.Subscript) and p.value is node:
            continue                                  # os.environ["X"]
        if isinstance(p, ast.Attribute) and p.attr in ("get",):
            continue                                  # os.environ.get("X")
        return True, "env ifadesi os.environ'ın TAMAMINDAN türüyor — devralma"
    return False, ""


# ONAYLI ORTAM ÜRETİCİLERİ — `env=<çağrı>` yalnız bu adlardan biri çağrıldığında
# süzülü sayılır.
#
# Neden bir liste, neden "os.environ geçmiyorsa güvenli" değil (dış denetim
# 2026-07-29, `spawn-gate-opaque-env`): eski kural LEXICAL'dı — env ifadesinde
# `os.environ` metni geçmiyorsa süzülü sayıyordu. Gövdesi `os.environ.copy()`
# dönen bir `full_env()` yardımcısı bu kuralda YEŞİL görünüyor. Canlı ölçüldü:
# probe böyle bir yardımcıyla çocuğa `LOCAL_APP_TOKEN`'ı geçirdi ve kapı bunu
# "süzülü" saydı. Kodda öyle bir yer YOKTU, yani canlı açık değil; kapının
# verdiği GARANTİ sandığımızdan dardı.
#
# Bu adların gövdeleri ayrı ayrı test ediliyor:
#   build_spawn_env → app/spawn_env.py, tests/test_provider_env_allowlist.py
#   _spawn_env      → app/omnisharp/omnisharp_manager.py (OmniSharp izin listesi)
# ⚠️ Buraya bir ad eklemek "bu fonksiyonun ortamı gerçekten süzdüğüne güveniyorum"
# demektir. Sarmalayıcı/kısayol adları EKLENMEYECEK: tek satırlık bir sarmalayıcı
# eklendiği anda kapı yine gövdesine bakılmayan bir ada güvenmeye başlar.
_ENV_PRODUCERS = frozenset({"build_spawn_env", "_spawn_env"})


def _called_name(value: ast.expr) -> Optional[str]:
    """Bir çağrının çağrılan ADI (`f()` → "f", `m.f()` → "f"). Çağrı değilse None."""
    if not isinstance(value, ast.Call):
        return None
    fn = value.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return None


def _assignments_in(func: ast.AST, name: str) -> List[ast.expr]:
    """`func` gövdesinde `name`'e yapılan atamaların sağ tarafları."""
    out: List[ast.expr] = []
    for node in ast.walk(func):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name and getattr(node, "value", None) is not None:
                out.append(node.value)
    return out


class _Scanner(ast.NodeVisitor):
    def __init__(self, relpath: str):
        self.relpath = relpath
        self.stack: List[str] = []
        # `env=<ad>` biçimini çözebilmek için içinde bulunduğumuz fonksiyon
        # düğümleri. Sadece isme bakmak `env = os.environ.copy()` satırını
        # kaçırıyordu ve `file_tools.run_command` bu yüzden YEŞİL görünüyordu —
        # yani kapının kendisinde ölçülmüş bir yalancı yeşil.
        self.func_stack: List[ast.AST] = []
        self.sites: List[_Site] = []
        self.ungateable: List[_Site] = []
        # ad → hangi modüle bağlı. `import subprocess as sp`, fonksiyon içi
        # `import subprocess as _sp` dahil (ast.walk her yeri görüyor).
        self.mod_alias: Dict[str, str] = {}
        self.direct: Dict[str, str] = {}

    # ── isim çözümü ──
    def collect_imports(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in ("subprocess", "asyncio", "os"):
                        self.mod_alias[a.asname or a.name] = a.name
            elif isinstance(node, ast.ImportFrom):
                if node.module in ("subprocess", "asyncio", "os"):
                    for a in node.names:
                        self.direct[a.asname or a.name] = f"{node.module}.{a.name}"

    # ── kapsam takibi ──
    def _push_func(self, node):
        self.stack.append(node.name)
        self.func_stack.append(node)
        self.generic_visit(node)
        self.func_stack.pop()
        self.stack.pop()

    def _push_class(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _push_func
    visit_AsyncFunctionDef = _push_func
    visit_ClassDef = _push_class

    def visit_Call(self, node: ast.Call):
        self._inspect(node)
        self.generic_visit(node)

    def _inspect(self, node: ast.Call):
        module = attr = None
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            module = self.mod_alias.get(node.func.value.id)
            attr = node.func.attr
        elif isinstance(node.func, ast.Name):
            bound = self.direct.get(node.func.id)
            if bound:
                module, attr = bound.split(".", 1)
        if module is None:
            return

        if module == "os" and attr in _UNGATEABLE:
            self.ungateable.append(_Site(
                self.relpath, ".".join(self.stack) or "<modül>", node.lineno,
                f"os.{attr}", False, "ortam süzülemez — bu çağrı biçimi kullanılamaz"))
            return
        if module == "subprocess" and attr in _UNGATEABLE:
            self.ungateable.append(_Site(
                self.relpath, ".".join(self.stack) or "<modül>", node.lineno,
                f"subprocess.{attr}", False, "ortam süzülemez"))
            return

        is_spawn = ((module == "subprocess" and attr in _SUBPROCESS_SPAWNERS)
                    or (module == "asyncio" and attr in _ASYNCIO_SPAWNERS))
        if not is_spawn:
            return

        qual = ".".join(self.stack) or "<modül>"
        call = f"{module}.{attr}"

        # `**kwargs` ile yayılan çağrı statik olarak okunamaz → gated SAYILMAZ.
        if any(kw.arg is None for kw in node.keywords):
            self.sites.append(_Site(self.relpath, qual, node.lineno, call, False,
                                    "**kwargs ile yayılıyor — ortam statik olarak okunamıyor"))
            return

        env_kw = next((kw for kw in node.keywords if kw.arg == "env"), None)
        if env_kw is None:
            self.sites.append(_Site(self.relpath, qual, node.lineno, call, False,
                                    "env= YOK — ebeveyn ortamı aynen devralınır"))
            return
        inherited, why = self._resolve_env(env_kw.value)
        self.sites.append(_Site(self.relpath, qual, node.lineno, call, not inherited, why))

    def _resolve_env(self, value: ast.expr, depth: int = 0) -> Tuple[bool, str]:
        """`env=` değerini çözer. Dönen `True` = SÜZÜLÜ SAYILAMAZ.

        Sözleşme kasıtlı olarak BEYAZ LİSTE: "süzülü" diyebilmek için ifadenin
        ya onaylı bir üretici çağrısı ya da öyle bir çağrıya bağlanan bir ad
        olması gerekir. Eskisi kara listeydi (`os.environ` metnini içermiyorsa
        güvenli) ve `full_env()` gibi opak bir yardımcı ondan geçiyordu —
        `_ENV_PRODUCERS`'ın başındaki ölçüme bakın. Kara liste, kapının
        bilmediği her yeni biçimi sessizce "güvenli" saymak demek.
        """
        inherited, why = _env_is_inherited(value)
        if inherited:
            return True, why

        if isinstance(value, ast.Call):
            ad = _called_name(value)
            if ad in _ENV_PRODUCERS:
                return False, ""
            return True, (f"env={ad or '<ifade>'}(...) — onaylı ortam üreticisi DEĞİL; "
                          "gövdesi burada okunmuyor, `os.environ.copy()` de dönebilir")

        if isinstance(value, ast.Name):
            # `env=<ad>`: adın bu fonksiyondaki atamalarına bak. Zincir olabilir
            # (`a = build_spawn_env(); e = a`), o yüzden özyineleme; `depth`
            # sınırı `e = e` gibi bir döngüde sonsuza gitmeyi engelliyor ve
            # aşıldığında GÜVENLİ yöne (süzülü sayma) düşüyor.
            if depth >= 4:
                return True, f"env={value.id} — ad zinciri çözülemedi (4 adım)"
            func = self.func_stack[-1] if self.func_stack else None
            atamalar = _assignments_in(func, value.id) if func is not None else []
            if not atamalar:
                # Ad bu fonksiyonda ATANMIYOR → parametre ya da global, yani ortamı
                # ÇAĞIRAN belirliyor. Kapı burada kurulamaz; çağrı yerinin ayrıca
                # sabitlenmesi gerekir (bkz. lsp_client muafiyeti + çağrı yeri testi).
                return True, (f"env={value.id} bu fonksiyonda atanmıyor (parametre/global) — "
                              "ortamı çağıran belirliyor, kapı burada kurulamaz")
            for atama in atamalar:
                belirsiz, neden = self._resolve_env(atama, depth + 1)
                if belirsiz:
                    return True, f"env={value.id}, ataması: {neden}"
            return False, ""

        # Ne devralma, ne onaylı üretici, ne de çözülebilir bir ad: sözlük
        # literali, koşullu ifade, abone… Hiçbiri şu an kodda yok; çıkarsa
        # yanlış alarm verip incelenmesini istiyoruz (kaçırma yönü kasıtlı).
        return True, "env= ifadesi statik olarak süzülü gösterilemiyor"


def _scan_app() -> Tuple[List[_Site], List[_Site]]:
    sites: List[_Site] = []
    ungateable: List[_Site] = []
    for root, dirs, files in os.walk(_APP_ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "vendor", "node_modules")]
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, os.path.dirname(_APP_ROOT)).replace(os.sep, "/")
            with open(full, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=full)
            sc = _Scanner(rel)
            sc.collect_imports(tree)
            sc.visit(tree)
            sites.extend(sc.sites)
            ungateable.extend(sc.ungateable)
    return sites, ungateable


# ── MUAFİYET KÜTÜĞÜ ──────────────────────────────────────────────────────────
#
# Anahtar: (dosya, spawn'ı içeren fonksiyonun qualname'i) → (adet, gerekçe).
# `adet` = O FONKSİYONDAKİ ortamı süzmeyen spawn sayısı. Bilerek sayıya bağlı:
# muaf bir fonksiyona onuncu bir spawn eklenirse test kırmızı olsun. Muafiyet
# artık karşılığı yoksa da kırmızı olur (bayat kayıt, güncel kayıt gibi okunur).
#
# ⚠️ Buraya bir satır eklemek "bu sızıntı kabul edildi" demektir. Gerekçesi
# ölçüme ya da somut bir arıza yönüne dayanmayan satır eklenmeyecek.
_EXEMPT: Dict[Tuple[str, str], Tuple[int, str]] = {
    # ── Sistem ikilileri: sabit argv, üçüncü taraf kod DEĞİL, vendor sırrı
    #    okuyacak bir yüzeyleri yok. Ortamı kısmak güvenlik satın almadan
    #    kullanılabilirlik satardı (git'in proxy/askpass ayarları, ekran
    #    yakalama araçlarının DISPLAY/WAYLAND_DISPLAY'i).
    ("app/providers/workspace_config.py", "_git_covered"): (
        1, "git check-ignore — sistem ikilisi, sabit argv, sır tüketmiyor"),
    ("app/providers/workspace_config.py", "_tracked"): (
        1, "git ls-files — sistem ikilisi, sabit argv"),
    ("app/providers/workspace_config.py", "harden_config_file"): (
        2, "icacls — Windows yerleşiği, sabit argv, sır tüketmiyor; yalnız "
           "yazdığımız config dosyasının ACL'ini kısıtlıyor (bulgu S4b). "
           "İKİ çağrı: biri kısıtlama, biri SONUCU DOĞRULAMA — rc=0 tek başına "
           "yeterli değil, çünkü explicit ACE'ler /inheritance:r'den sağ "
           "çıkıyor (2. doğrulama turu bulgusu)"),
    ("app/unity_ai_mcp/mcp_port_guard.py", "_run"): (
        1, "lsof/pgrep/netstat — port sahibi sorgusu; DISPLAY'siz sistem aracı"),
    ("app/unity_ai_mcp/unity_mcp_manager.py", "UnityMCPManager.is_unity_running"): (
        2, "tasklist / pgrep — Unity Editor süreci var mı; sabit argv"),
    ("app/tools/screenshot_tool.py", "_capture_mac"): (
        2, "screencapture — macOS sistem aracı; ekran yakalama oturum "
           "ortamına bağlı ve kısmak yakalamayı sessizce boş dosyaya düşürür"),
    ("app/tools/screenshot_tool.py", "_capture_linux"): (
        1, "grim/gnome-screenshot/spectacle/scrot/import — hepsi DISPLAY, "
           "WAYLAND_DISPLAY, XAUTHORITY okur; bunlar izin listesinde YOK ve "
           "eklemek yakalamayı Linux'ta canlı sınayamadığımız bir dala sokardı"),
    # ⚠️ `app/providers/video_extract.py :: _run` BURADAN KALDIRILDI (2026-07-29).
    #    Gerekçesi "sabit argv, vendor anahtarı TÜKETMİYOR" idi ve dış denetim
    #    bunun YANLIŞ TÜRDEN bir gerekçe olduğunu gösterdi: bir ikilinin anahtarı
    #    tüketmemesi onu ALMASINI engellemiyor, ve yt-dlp ağa çıkan üçüncü taraf
    #    bir araç. Nokta artık `env=build_spawn_env()` ile süzülü (_ANCHORS'ta).

    # ── Kullanıcının ONAYLADIĞI kabuk komutu: ortamın devralınması BURADA
    #    ürünün vaadinin parçası. Model `npm run`, `git push`, kullanıcının
    #    kendi araç zincirini çalıştırıyor; izin listesine indirmek projeye
    #    özel her değişkeni sessizce düşürürdü. Komut onay kartında kullanıcıya
    #    GÖSTERİLİYOR ve çocuk zaten aynı kullanıcı olarak koşuyor.
    #    ⚠️ Bu, kapatılmış bir sızıntı değil KABUL EDİLMİŞ bir taviz: onaylanan
    #    bir komut `echo $ANTHROPIC_API_KEY` de olabilir.
    ("app/tools/file_tools.py", "run_command"): (
        1, "kullanıcı onaylı kabuk komutu — ortam devralması kasıtlı (taviz)"),
    ("app/unity_ai_mcp/tools/bash_tool.py", "register_bash_tool._run_command"): (
        1, "kullanıcı onaylı kabuk komutu — ortam devralması kasıtlı (taviz)"),
    ("app/unityai_cli.py", "cmd_bash"): (
        1, "kullanıcı onaylı kabuk komutu — ortam devralması kasıtlı (taviz)"),

    # ── Ortamı ÇAĞIRAN veriyor (kapı burada kurulamaz, çağrı yerinde kurulu).
    ("app/omnisharp/lsp_client.py", "LspClient.start"): (
        1, "genel LSP taşıması; `env` parametresi. Tek çağrı yeri "
           "omnisharp_manager:402 ve orada `_spawn_env()` (OmniSharp'ın kendi "
           "izin listesi) veriliyor — aşağıdaki "
           "test_the_only_lsp_call_site_passes_an_allowlist_env onu sabitliyor"),

    # ── Kullanıcının GÖRDÜĞÜ interaktif terminal (CLI kurulum/giriş akışı).
    ("app/routes/config_routes.py", "_open_visible_terminal"): (
        2, "PowerShell penceresi + /usr/bin/open — kullanıcının önünde açılan "
           "interaktif kurulum terminali. macOS dalında zaten LaunchServices "
           "araya giriyor (Terminal.app bizim ortamımızı hiç devralmıyor); "
           "Windows dalında pencere kullanıcıya görünür ve npm kurulumunun "
           "kullanıcının kendi araç zinciri ortamına ihtiyacı var"),
}


def _key(site: _Site) -> Tuple[str, str]:
    return (site.relpath, site.qualname)


class TestSpawnEnvKapisi:
    def test_no_ungateable_spawn_primitive_is_used(self):
        """`os.system` / `os.popen` / `os.exec*` ortam süzmeye izin VERMİYOR.

        Muafiyeti bile olamaz: bu çağrı biçimleri seçildiğinde izin listesi
        uygulanamaz hale gelir, yani kapı kodun kendisi tarafından kaldırılır.
        """
        _, ungateable = _scan_app()
        assert not ungateable, (
            "ortamı süzülemeyen süreç başlatma biçimi kullanılmış:\n  "
            + "\n  ".join(str(s) for s in ungateable))

    def test_every_spawn_site_is_either_gated_or_explicitly_exempt(self):
        """SINIF KAPISI. Yeni bir sızıntı noktası eklendiğinde bu test kırmızı olur."""
        sites, _ = _scan_app()
        assert sites, "hiç spawn noktası bulunamadı — tarayıcı kendi kendini ölçüyor"

        ungated = [s for s in sites if not s.gated]
        beklenmedik = [s for s in ungated if _key(s) not in _EXEMPT]
        assert not beklenmedik, (
            f"{len(beklenmedik)} spawn noktası ortamı süzmüyor ve muaf listesinde de "
            "değil. Ya `env=build_spawn_env(env_family(...))` ver, ya da "
            "_EXEMPT'e GEREKÇESİYLE ekle:\n  "
            + "\n  ".join(str(s) for s in beklenmedik))

    def test_an_exempt_function_cannot_silently_grow_a_new_leak(self):
        """Muaf bir fonksiyondaki süzülmemiş spawn SAYISI sabitlenir.

        Sayı olmasaydı, muaf bir fonksiyonun içine eklenen ikinci bir spawn
        (örn. `run_command`'ın yanına konan bir CLI çağrısı) sessizce muaf
        olurdu — bu dosyanın kapatmaya çalıştığı arızanın ta kendisi.
        """
        sites, _ = _scan_app()
        gercek: Dict[Tuple[str, str], int] = {}
        for s in sites:
            if not s.gated:
                gercek[_key(s)] = gercek.get(_key(s), 0) + 1
        sapan = {
            k: (beklenen, gercek.get(k, 0))
            for k, (beklenen, _) in _EXEMPT.items()
            if gercek.get(k, 0) != beklenen
        }
        assert not sapan, (
            "muaf noktaların sayısı kütükle uyuşmuyor "
            "(beklenen, bulunan):\n  "
            + "\n  ".join(f"{k[0]} :: {k[1]} → {v}" for k, v in sorted(sapan.items())))

    def test_the_exemption_ledger_has_no_stale_entries(self):
        """Karşılığı kalmamış muafiyet, bayat hafıza gibi: güncel diye okunur.

        Bir nokta düzeltilip `env=` verildiğinde kaydın burada kalması,
        "bu sızıntı kabul edildi" diyen ölü bir satır bırakır.
        """
        sites, _ = _scan_app()
        var_olan = {_key(s) for s in sites if not s.gated}
        bayat = sorted(k for k in _EXEMPT if k not in var_olan)
        assert not bayat, (
            "bu muafiyetlerin artık karşılığı yok (düzeltildiler mi? kaydı sil):\n  "
            + "\n  ".join(f"{f} :: {q}" for f, q in bayat))

    def test_every_exemption_carries_a_reason(self):
        for key, (_, reason) in _EXEMPT.items():
            assert reason.strip(), f"{key} muafiyeti gerekçesiz"

    def test_the_scanner_actually_recognises_the_shapes_it_must_catch(self):
        """TARAYICININ KENDİ KAPISI — yalancı yeşile karşı.

        Bir kapı testinin en kötü arıza biçimi, hiçbir şey bulamayıp yeşil
        kalmasıdır. Burada tarayıcı bilinen dört sızıntı biçimiyle sürülüyor.
        """
        # Aşağıdaki metin ÇALIŞTIRILMIYOR: `ast.parse` için sabit test verisi.
        # İçindeki `os.system(...)` kasıtlı — tarayıcının "ortamı süzülemez"
        # dalını sürmek için orada duruyor (bkz. _UNGATEABLE).
        kaynak = (
            "import subprocess\n"
            "import subprocess as sp\n"
            "import asyncio\n"
            "import os\n"
            "def f():\n"
            "    subprocess.run(['a'])\n"                     # env yok
            "    sp.Popen(['a'], env=None)\n"                 # None
            "    subprocess.run(['a'], env=os.environ.copy())\n"   # devralma
            "    subprocess.run(['a'], env={**os.environ})\n"      # devralma
            # ⚠️ Ad `build_spawn_env` — uydurma bir `build()` DEĞİL. 2026-07-29'dan
            # beri süzülü sayılmanın koşulu ONAYLI bir üretici çağırmak
            # (`_ENV_PRODUCERS`); rastgele bir ad artık burada da yeşil olmaz.
            "    subprocess.run(['a'], env=build_spawn_env())\n"   # SÜZÜLMÜŞ
            "    e = os.environ.copy()\n"
            "    subprocess.run(['a'], env=e)\n"              # ad üzerinden devralma
            "    ok = build_spawn_env(overrides={'X': os.environ.get('X')})\n"
            "    subprocess.run(['a'], env=ok)\n"             # SÜZÜLMÜŞ (tek değer)
            "    asyncio.create_subprocess_exec('a')\n"       # env yok
            "    os.system('a')\n"                            # süzülemez
        )
        sc = _Scanner("<test>")
        tree = ast.parse(kaynak)
        sc.collect_imports(tree)
        sc.visit(tree)
        assert len(sc.sites) == 8, [str(s) for s in sc.sites]
        assert sum(1 for s in sc.sites if s.gated) == 2, [str(s) for s in sc.sites]
        assert len(sc.ungateable) == 1

    def test_the_scanner_refuses_an_env_built_by_an_unapproved_producer(self):
        """`env=<çağrı>` yalnız ONAYLI bir üretici çağrılıyorsa süzülü sayılır.

        Dış denetim bulgusu (2026-07-29, `spawn-gate-opaque-env`): tarayıcı
        `os.environ` metnini LEXICAL olarak içermeyen her ifadeyi güvenli
        sayıyordu. Yani gövdesi `os.environ.copy()` dönen bir `full_env()`
        yardımcısı "süzülü" görünüyordu. Canlı olarak ölçüldü: probe böyle bir
        yardımcı kurup çocuğa `LOCAL_APP_TOKEN`'ı geçirdi ve kapı YEŞİL kaldı.

        Kodda şu an böyle bir yer YOK — bu yüzden canlı bir açık değil, kapının
        verdiği GARANTİNİN sandığımızdan dar olması. Bu test o garantiyi
        genişletir: onaylı üretici listesinde olmayan bir çağrı süzülü sayılmaz
        ve o nokta ya düzeltilir ya gerekçesiyle muafiyet kütüğüne girer.
        """
        kaynak = (
            "import os\n"
            "import subprocess\n"
            "def full_env():\n"
            "    return os.environ.copy()\n"          # opak: gövde başka bir yerde
            "def f():\n"
            "    subprocess.run(['a'], env=full_env())\n"        # onaylı DEĞİL
            "    subprocess.run(['a'], env=build_spawn_env())\n" # onaylı
            "    e = full_env()\n"
            "    subprocess.run(['a'], env=e)\n"                 # ad üzerinden, onaylı DEĞİL
        )
        sc = _Scanner("<test>")
        tree = ast.parse(kaynak)
        sc.collect_imports(tree)
        sc.visit(tree)
        assert [s.gated for s in sc.sites] == [False, True, False], [str(s) for s in sc.sites]

    def test_the_scanner_sees_a_function_local_import_alias(self):
        """`_register_mcp` içindeki `import subprocess as sp` fonksiyon-YEREL.
        Modül başındaki import'lara bakan bir tarayıcı bu noktaları hiç görmez —
        yani tam olarak 2026-07-29'da bulunan sızıntıyı kaçırırdı."""
        kaynak = (
            "def f():\n"
            "    import subprocess as sp\n"
            "    sp.run(['a'])\n"
        )
        sc = _Scanner("<test>")
        tree = ast.parse(kaynak)
        sc.collect_imports(tree)
        sc.visit(tree)
        assert len(sc.sites) == 1 and not sc.sites[0].gated


# Daha önce KAPATILMIŞ noktalar. Tarayıcı körleşirse (import biçimi değişir,
# bir dosya taramadan düşer, sınıf/fonksiyon adı değişir) burası kırmızı olur.
# Gerekçe: bir kapı testinin en kötü arıza biçimi hiçbir şey bulamayıp yeşil
# kalmasıdır — "muafiyet yok, demek ki temiz" ile "hiç bakmadım" aynı görünür.
_ANCHORS: Dict[Tuple[str, str], int] = {
    # 2026-09-05: was 2; the agy one-shot spawn moved to a persistent
    # stream-json process in agy_session.py (AgyStreamSession._start).
    ("app/providers/cli_base.py", "BaseCLIProvider.analyze_code"): 1,
    ("app/providers/codex_session.py", "CodexSession.start"): 1,
    ("app/providers/codex_session.py", "fetch_codex_skills"): 1,
    ("app/providers/oneshot_cli.py", "probe_named_models"): 1,
    ("app/unity_ai_mcp/unity_mcp_manager.py", "UnityMCPManager.start_server"): 1,
    # 2026-09-25 (P3): `uv export` of the server lock before the uvx launch.
    ("app/unity_ai_mcp/unity_mcp_manager.py", "UnityMCPManager._lock_constraints"): 1,
    # 2026-07-29'da kapatılanlar
    ("app/providers/claude_provider.py", "ClaudeCodeProvider._register_mcp"): 4,
    ("app/providers/codex_provider.py", "CodexProvider._register_mcp"): 4,
    ("app/agentic/agent_runner.py", "AgentRunner._run_claude_session"): 1,
    ("app/routes/config_routes.py", "_run_cli_capture"): 1,
    # 2026-07-29 doğrulama turu: ffmpeg/yt-dlp. Muafiyet kütüğünden çıkarıldı.
    ("app/providers/video_extract.py", "_run"): 1,
}


class TestTarayiciKorlesmesin:
    def test_the_already_closed_spawn_sites_are_still_seen_as_gated(self):
        sites, _ = _scan_app()
        gated: Dict[Tuple[str, str], int] = {}
        for s in sites:
            if s.gated:
                gated[_key(s)] = gated.get(_key(s), 0) + 1
        sapan = {k: (v, gated.get(k, 0)) for k, v in _ANCHORS.items() if gated.get(k, 0) != v}
        assert not sapan, (
            "kapatılmış noktalar artık 'süzülmüş' olarak görünmüyor — ya düzeltme "
            "geri alındı ya da tarayıcı körleşti (beklenen, bulunan):\n  "
            + "\n  ".join(f"{k[0]} :: {k[1]} → {v}" for k, v in sorted(sapan.items())))

    def test_the_only_lsp_call_site_passes_an_allowlist_env(self):
        """`LspClient.start`'ın `env` parametresinin VARSAYILANI None — yani
        kapı orada kurulamaz, çağrı yerinde kurulu. Bu iki yerin uyuşması
        kimse tarafından ölçülmüyordu; bu depodaki arızaların ortak şekli
        tam olarak "birbiriyle uyuşması gereken iki yer uyuşmuyor"."""
        path = os.path.join(_APP_ROOT, "omnisharp", "omnisharp_manager.py")
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        cagrilar = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "start"
            and any(kw.arg == "env" for kw in n.keywords)
        ]
        assert cagrilar, "omnisharp_manager artık `start(..., env=...)` çağırmıyor"
        for c in cagrilar:
            env_kw = next(kw for kw in c.keywords if kw.arg == "env")
            assert isinstance(env_kw.value, ast.Call), "env sabit bir ifade değil, üretilmiyor"
            fn = env_kw.value.func
            adi = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            assert adi == "_spawn_env", f"beklenmedik env kaynağı: {adi}"
