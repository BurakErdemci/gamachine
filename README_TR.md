<div align="center">

# Gamachine

**Tüm kodlama ajanlarını ve canlı Unity kontrolünü tek bir masaüstü uygulamasında birleştiren agentic geliştirme stüdyosu**

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Electron](https://img.shields.io/badge/Electron-34-47848F?style=for-the-badge&logo=electron&logoColor=white)](https://electronjs.org)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178C6?style=for-the-badge&logo=typescript&logoColor=white)](https://typescriptlang.org)
[![Unity MCP](https://img.shields.io/badge/Unity_MCP-Gömülü-7B2FBE?style=for-the-badge&logo=unity&logoColor=white)](./unity-mcp)
[![License](https://img.shields.io/badge/Lisans-MIT-green?style=for-the-badge)](LICENSE)

*Claude Code, Codex, Antigravity (agy), bulut API'leri ve canlı Unity Editor kontrolü — hepsi aynı sohbet penceresinde, aynı onay sisteminin arkasında, aynı projeyi tanıyarak çalışır.*

[English README](./README.md)

<br/>

![Gamachine Demo](docs/media/demo.gif)

*Kod editöründe doğal dille geliştir, ardından Unity Editor'ü canlı olarak kontrol et — tek pencereden.*

<sub>Neden böyle kurulduğunu merak ediyorsan: mimari kararları, yanlış çıkanları ve
bedellerini bir günlükte tuttum →
**[Engineering notes](docs/engineering-notes.md)** (İngilizce)</sub>

<br/>

### İndir

**[⬇ En son sürümü indir](https://github.com/BurakErdemci/gamachine/releases/latest)**

| Platform | Dosya |
|---|---|
| **Windows 10/11** (x64) | `Gamachine-Setup-<sürüm>.exe` |
| **macOS** (Apple Silicon) | `Gamachine-<sürüm>-arm64.dmg` |

Sonrası sana ait: bir API anahtarı gir ya da zaten kullandığın bir CLI ajanını
göster. Bir sağlayıcı bağlanana kadar sohbet kilitli kalıyor — **sana habersiz
üçüncü taraf bir AI aracı kurmuyoruz.**

<sub>Derlemeler henüz imzalı değil, o yüzden ilk açılışta işletim sistemi uyarı
veriyor: Windows SmartScreen → *Daha fazla bilgi* → *Yine de çalıştır*;
macOS → sağ tık → *Aç*, ya da
`xattr -cr "/Applications/Gamachine.app"`. Intel Mac desteklenmiyor.
Kaynaktan derlemeyi tercih ediyorsan: [Kurulum](#-kurulum).</sub>

</div>

---

## 🎯 Tek Çatı Altında: Bu Proje Neyi Birleştiriyor?

Bugün bir Unity geliştiricisi farklı işler için farklı pencereler açmak zorunda: kod üretmek için bir CLI, Editor'ü kontrol etmek için ayrı bir eklenti, sohbet için bir başka uygulama. Her birinin kendi onay mantığı, kendi konfigürasyonu, projeden kendi kopuk görüşü var.

**Gamachine bunların hepsini tek bir uygulamada toplar** — ve hepsini *tek bir onay kapısının* arkasına alır. Bir modeli açılır menüden seçersin; ister Claude Code CLI olsun, ister Codex, ister Antigravity (agy), ister GitHub Copilot CLI, ister doğrudan bulut API'si — hepsi:

- **aynı sohbet penceresinde** çalışır,
- **aynı projeyi** workspace olarak görür,
- **aynı dosya/terminal onay kartlarını** gösterir (kapsamı aşağıda: silme ve tehlikeli komutlar her yolda onaylı; kod yazma CLI yolunda onaylı, bulut API yolunda bilinçli olarak onaysız),
- ve istenirse **aynı canlı Unity Editor'e** MCP üzerinden komut verir.

### Ajan × Yetenek Matrisi

| | Sohbet & Analiz | Dosya yaz/düzenle | Terminal (onaylı) | Canlı Unity Editor kontrolü | Auth kaynağı |
|---|:---:|:---:|:---:|:---:|---|
| **Claude Code** (CLI) | ✅ | ✅ MCP — onaylı | ✅ MCP | ✅ unityMCP — **onaylı** | Anthropic aboneliğin |
| **Codex** (CLI) | ✅ | ✅ MCP — onaylı | ✅ MCP | ✅ unityMCP | OpenAI aboneliğin |
| **Antigravity / agy** (CLI) | ✅ | ✅ `unityai` köprüsü — onaylı | ✅ köprü | ✅ unityMCP (HTTP) | Google aboneliğin |
| **GitHub Copilot** (CLI) | ✅ | ✅ MCP — onaylı | ✅ MCP | ✅ unityMCP | Copilot aboneliğin |
| **Cursor** (CLI) | ✅ | ✅ MCP — onaylı | ✅ MCP | ✅ unityMCP | Cursor aboneliğin |
| **OpenCode** (CLI) | ✅ | ✅ MCP — onaylı | ✅ MCP | ✅ unityMCP | Ücretsiz / kendi anahtarın |
| **Kimi Code** (CLI) | ✅ | ✅ MCP — onaylı | ✅ MCP | ✅ unityMCP | Moonshot aboneliğin |
| **Bulut API** (Claude/GPT/Gemini/…) | ✅ | ✅ function calling — **onaysız** | ✅ function calling | ✅ function calling | Kendi API anahtarın |
| **Ollama** (yerel) | ✅ | ✅ (uyumlu modeller) — **onaysız** | ✅ | ⚠️ kısmi | Maliyet yok, offline |

Bu matrisin sağladığı şey basit ama nadir: **kaynak ne olursa olsun deneyim aynı.** Codex'ten Claude Code'a geçmek bir açılır menü; alıştığın diff ekranı, terminal onayı ve Unity entegrasyonu olduğu gibi kalır.

> **Önemli ayrım:** Onay kartları **dosya silme ve tehlikeli terminal komutları** için her yolda çıkar; **dosya yazma** için CLI ajanlarında çıkar, **bulut API / Ollama function-calling yolunda çıkmaz**. **Canlı Unity sahne işlemleri** için durum sağlayıcıya göre değişir: Claude yolunda sahneyi *değiştiren* unityMCP çağrıları artık onay kartı açar, Codex ve agy yolunda açmaz. Tam tablo ve gerekçeler: [Onay kapsamı](docs/security.md#️-approval-scope-what-is-and-isnt-confirmed-an-honesty-note).

---

## 🚀 Neden Gamachine?

Unity ekosistemindeki AI araçları genelde iki uçtan birine düşer: ya sadece kod yazar, ya da sadece sohbet eder. Gerçek bir geliştirme ortağının yaptığını — projeyi anlamak, hatayı bulmak, düzeltmek, terminalde test etmek ve Unity Editor'de görmek — tek başına yapamaz.

| Geleneksel AI Asistanlar | Gamachine |
|---|---|
| Tek sağlayıcıya kilitli | 7 CLI ajanı (Claude Code, Codex, agy, Copilot, Cursor, OpenCode, Kimi Code), 8+ bulut API (NVIDIA NIM ücretsiz havuz dahil), Ollama — tek menüden |
| Kod yazar, projeyi görmez | Workspace'teki tüm `.cs` dosyalarını tarar, mimari haritasını çıkarır |
| Dosya sistemine erişemez | Dosya oku/yaz/sil — hepsi workspace'e kilitli; silme ve tehlikeli komutlar onay kartıyla |
| Unity Editor'den habersiz | MCP ile sahneye GameObject ekler, bileşen bağlar, konsolu okur |
| Terminal çalıştıramaz | Güvenli terminal katmanı; tehlikeli komutlar onay ister |
| Her sohbet sıfırdan başlar | Kalıcı hafıza + proje analizi ile bağlamı korur |
| Arka plan işi bitince ortada kalır | Sohbet kendi kendine uyanır, neyin bittiğini söyler |
| Kurulum derdi | `uv`, OmniSharp + .NET SDK, ffmpeg/yt-dlp, çevrimdışı konuşma modelleri — hepsi **uygulamaya gömülü**, sıfır ek kurulum |

---

## ✨ Özellikler

**Çok ajan, tek deneyim** — 7 CLI ajanı, 8+ bulut API'si ve yerel Ollama; mesaj
başına değiştirilebilir. Bir CLI seçildiğinde backend o aracın MCP yapılandırmasını
çağrı anında yazıyor, elle kurulum gerekmiyor. Antigravity her turda yeni süreç
açmak yerine sohbet başına tek bir uzun ömürlü oturumda çalışıyor.

**Model listesi bizim değil, senin hesabının** — her bulut sağlayıcısına senin
anahtarınla "bana hangi modelleri veriyorsun" diye soruluyor; adının yanındaki
etiketi, bağlam boyutunu ve fiyatı da herkese açık bir katalog sağlıyor. Anahtarı
olmayan sağlayıcı listeden kaybolmuyor, "doğrulanmadı" diye işaretleniyor — çünkü
"bu model var" ile "senin hesabında var" iki ayrı iddia.

**Otonom agentic döngü** — görev → düşün → araç çağır → değerlendir → tekrarla;
ilerleme koruması durduruyor (aynı araç/argüman/sonuç beş kez tekrarlarsa), sert sigorta 300 adım. Her adım SSE ile canlı akıyor ve Dur düğmesi iki katmanda
birden iptal ediyor: akışı kesiyor *ve* backend'de bekleyen onay kapılarını reddediyor.

**Hepsi için tek onay kapısı** — dosya yazımında yan yana diff, silmede içerik
önizlemesi, terminalde komutun kendisi. CLI ajanları ve bulut API'leri aynı arayüzden
geçiyor. Neyin onaylandığı ve neyin **bilerek** onaylanmadığı yazılı:
[Approval scope](docs/security.md#️-approval-scope-what-is-and-isnt-confirmed-an-honesty-note).

**Canlı Unity Editor kontrolü, kurulum yok** — MCP üzerinden 51 Editor aracı: sahne,
GameObject, prefab, materyal, fizik, build ayarları. Ayrıca
[oyunu oynayabiliyor](docs/unity-mcp.md#-the-ai-can-now-play-the-game-manage_input):
play moduna giriyor, girdi gönderiyor, ekran görüntüsü alıp yaptığı işi
değerlendiriyor. `uv` araç zinciri uygulamanın içinde geliyor.

**Proje farkındalığı** — workspace'teki bütün `.cs` dosyaları taranıp parçalanıyor;
"Projeyi Öğren" sınıfları, kalıtım ilişkilerini ve kilit metotları bir mimari
haritaya çıkarıyor. `/compact` uzun sohbetleri token sınırına çarpmadan özetliyor;
kullanım/bağlam paneli nerede olduğunu gösteriyor — canlı sayıya ulaşılamıyorsa
"bayat" ya da "tahmin" rozetiyle, dürüstçe.

**Sohbet kendi kendine devam ediyor** — sen bakmayı bıraktıktan sonra biten bir
alt ajan ya da arka plan komutu, sen bir şey yazana kadar sohbeti boşta bırakırdı.
Artık sohbet uyanıyor, turu kaldığı yerden alıyor ve neyin bittiğini söylüyor. Ekranda
bir onay kartı varken bekliyor; art arda üç uyanıştan sonra duruyor.

**3D modeller ve görseller yerinde açılıyor** — dosya ağacında bir `.fbx`, `.glb`,
`.gltf`, `.obj`, `.stl`, `.ply` ya da `.dae` dosyasına tıkla; metin editörü yerine
önizlemede açılıyor: kamerayı çevir, zaman çizgisini kaydır, animasyonu 0.25x–2x
hızda oynat. Yalnızca animasyon taşıyan bir dosyaya, kendi kemiklerinden bir manken
kuruluyor ki izleyecek bir şey olsun. Doku ve sprite'lar aynı alanda açılıyor;
gerçek boyut modu piksel sanatını bulanıklaştırmadan gösteriyor.

**Makineden çıkmayan dikte** — sohbet kutusundaki mikrofon düğmesi, sen konuşurken
söylediklerini kutuya yazıyor; okuyorsun, düzeltiyorsun, Enter'a basıyorsun. Türkçe
ve İngilizce tanıma modelleri yükleyicinin içinde geliyor: çevrimdışı çalışıyor ve
hiçbir ses kaydı hiçbir yere yüklenmiyor.

**C# zekası, kurulum yok** — OmniSharp LSP yan süreci Monaco editöründe gerçek
Roslyn analizi veriyor; ihtiyaç duyduğu .NET SDK'sı üç platformda da gömülü.
(Runtime değil SDK olması bir tercih değil, ölçülmüş bir zorunluluk —
[Architecture](docs/architecture.md).)

**Gerçek effort kontrolü** — effort seçimi her sağlayıcıda gerçekten etki ediyor;
arayüz yalnızca o modelin desteklediği seviyeleri gösteriyor.

**Video → sohbet** — sohbete bir video bağlantısı (YouTube dahil) ya da dosyası
bırak; gömülü ffmpeg + yt-dlp kareleri ve transkripti çıkarıp analiz hattına veriyor.

**Etrafında gerçek bir IDE** — Monaco editörü, gerçek PTY üzerinde xterm.js
terminali, diff görüntüleyici, canlı düşünme bloğu ve iki dilli TR/EN arayüz.

---

## 📚 Dokümantasyon

Ayrıntılı dokümanlar **İngilizce** tutuluyor: en çok değişen katman orası ve iki dilde
sürdürmek, kodla arasındaki sürüklenmeyi büyütüyor. README iki dilli kalıyor.

| | |
|---|---|
| [Engineering notes](docs/engineering-notes.md) | Kararlar, hatalar ve bedelleri |
| [Architecture](docs/architecture.md) | Süreç yerleşimi, agentic döngü, araç katmanı, SSE |
| [Approval & security](docs/security.md) | Neyin onaylandığı, neyin bilerek onaylanmadığı |
| [Unity MCP integration](docs/unity-mcp.md) | 51 Editor aracı, URL'ye göre sabit araç listesi ve girdi sistemi |
| [Supported providers](docs/providers.md) | Bütün CLI ajanları, bulut API'leri, yerel modeller |
| [Building from source](docs/building.md) | Geliştirme kurulumu ve dmg / installer üretimi |

---

## ⚙️ Kurulum

**Uygulamayı kullanmak için:**
[Releases](https://github.com/BurakErdemci/gamachine/releases/latest) sayfasından
indir, bir API anahtarı gir ya da zaten kullandığın bir CLI ajanını göster. Python,
`uv`, OmniSharp, .NET SDK ve ffmpeg/yt-dlp uygulamanın içinde geliyor — başka bir
kurulum gerekmiyor.

**Kaynaktan derlemek için:** Python 3.13+ ve Node.js 20+ gerekiyor (Unity MCP
entegrasyonunu istiyorsan Unity Editor de). Adımlar, ortam değişkenleri ve paketleme
[Building from source](docs/building.md) dosyasında.

---

## 💡 Kullanım

1. **Workspace seç** — Unity projenin klasörünü seç; backend `.cs` dosyalarını tarar.
2. **Model seç** — Ayarlar'dan sağlayıcı/model seç. Bulut API ise anahtarını gir (şifreli saklanır); CLI ise makinende kurulu olması yeter.
3. **Konuş** —

```
"PlayerController.cs'teki performans sorunlarını bul"
"Inventory için ScriptableObject tabanlı ItemData scripti oluştur"
"NullReferenceException PlayerController.cs:47 — sebebi ne, düzelt"
"Sahneye bir Player capsule ekle ve Rigidbody bağla"   # Unity MCP açıkken
/compact                                                # uzun sohbeti özetle
```

4. **Onayla** — AI onay gerektiren bir işlem isteyince akış durur, diff/komut kartı açılır; onayla veya reddet. (Bulut API modellerinde dosya *yazma* kart açmaz — bkz. [Onay kapsamı](docs/security.md#️-approval-scope-what-is-and-isnt-confirmed-an-honesty-note).)
5. **Bak ve konuş** — dosya ağacında bir `.fbx`'e ya da dokuya tıkla, yerinde önizlensin; yazmak yerine mikrofona basıp söyle.

---

## 🤝 Katkıda Bulunma

Kalite kapısı üç ayrı test paketinden oluşuyor — **toplam ~3100 test**:

```bash
# Backend (~1118 test)
cd Backend && pytest
#   Windows: venv\Scripts\python.exe -m pytest    (ortam: PYTHONUTF8=1)

# unity-mcp sunucusu (~1596 test)
cd unity-mcp/Server && pytest

# Frontend (~396 test) + TypeScript kapısı
cd Frontend/frontend && npm test && npx tsc --noEmit
```

CI'da (`.github/workflows/test.yml`) dört job birden koşar: **Backend testleri**, **unity-mcp Server testleri**, **Frontend kapısı (tsc + vitest)** ve **PowerShell sözdizimi denetimi**. Dördü de yeşil olmadan sürüm çıkmaz.

1. Repo'yu fork'la
2. Feature branch aç (`git checkout -b feat/harika-ozellik`)
3. Testleri çalıştır
4. Pull request aç

> 💡 **Yerel yeşil, CI hakkında hiçbir şey söylemez.** Bu depoda defalarca ölçüldü: platform farkı (Windows ↔ Linux), yol ayırıcıları ve ortam değişkenleri yerelde geçen bir testi CI'da kırabiliyor. Push'tan sonra `gh run list` bir alışkanlık değil, bir adım.

---

## 👤 Geliştirici

**Burak Emre Erdemci**

Unity geliştirme sürecini AI ile kökten dönüştürmek isteyen geliştiriciler için açık kaynak bir portfolyo ve araştırma çalışması.

---

## 📄 Lisans

**[MIT](LICENSE)** — açık kaynak, hiçbir ek koşul yok:

- ✅ **Kullanabilirsin** — kişisel, eğitim, araştırma, kurumsal, her amaçla
- ✅ **İnceleyebilir, değiştirebilir, fork'layabilir, yeniden dağıtabilirsin**
- ✅ **Yaptığın oyunları satabilirsin** — bu uygulamayla ürettiğin işler tamamen senindir

Üçüncü parti bileşenlerin lisansları için: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)
(Gömülü FFmpeg'in lisansı **platforma göre değişiyor** — Windows'ta LGPL-3.0,
macOS/Linux'ta GPL-3.0 — ve her durumda yalnızca ayrı bir süreç olarak çağrılıyor.)

Güvenlik açığı bildirimi: [SECURITY.md](SECURITY.md) ·
Katkı rehberi: [CONTRIBUTING.md](CONTRIBUTING.md)

---

## Markalar

Bu proje Unity Technologies ile **bağlantılı değildir**, onun tarafından
desteklenmiyor ve onaylanmıyor. "Unity" ve "Unity Technologies", Unity
Technologies veya bağlı kuruluşlarının ABD'de ve diğer ülkelerdeki ticari
markaları ya da tescilli ticari markalarıdır. Aynı şekilde "Unreal Engine" Epic
Games'in, "Godot" Godot Foundation'ın, "Claude" Anthropic'in, "Codex" ve "GPT"
OpenAI'ın, "Gemini" ve "Antigravity" Google'ın, "GitHub Copilot" GitHub'ın,
"Cursor" Anysphere'in markalarıdır. Bu adlar yalnızca uyumluluğu **tanımlamak**
için kullanılıyor.
