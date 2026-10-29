#!/bin/bash
# Unity MCP için gerekli uv araç zincirini (uv + uvx) macOS'a indirir.
# Bu ikililer installer'a gömülür (electron-builder.yml → extraResources: uv),
# böylece kullanıcının Mac'inde uv kurulu olmasa da Unity MCP çalışır.
# Boyutları büyük olduğu için git'e konmaz (.gitignore); build öncesi bunu çalıştır.
#
# Only Apple silicon is fetched, into vendor/uv/darwin-arm64/: Intel Macs are not
# supported (owner decision, 29 Oct 2026), so the dmg is arm64-only and an x64 uv
# would be dead weight. The arch subdirectory stays because _get_uvx() and the
# Windows flat layout (fetch_uv.ps1 → uv/uvx.exe) both depend on it.
#
# Sürüm SABİT, `latest` değil: adres ve beklenen sha256 scripts/pinned_assets.json
# içinde (anahtar uv/darwin-arm64). `latest` kullanmak, her build'in
# o anda yayında olan farklı bir ikiliyi installer'a gömmesi demekti — ne tekrar
# üretilebilir bir çıktı ne de doğrulanacak bir beklenti kalıyordu.
# Sürüm yükseltmek için: `python3 scripts/pinned_assets.py refresh`, sonra diff'i incele.
#
# Kullanım:  bash Backend/vendor/fetch_uv.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$SCRIPT_DIR/uv"
# Betik Backend/vendor/ altında; repo kökü iki üstü.
PINNED="$(cd "$SCRIPT_DIR/../.." && pwd)/scripts/pinned_assets.py"
mkdir -p "$DEST"

# ── Kütük CLI'ının çıkış kodu SINIFLARI ───────────────────────────────
# Sözleşme scripts/pinned_assets.py docstring'inde yazılı ve buradaki tek kaynağı o:
#   0 = tamam
#   1 = BÜTÜNLÜK arızası — indirilen baytlar pinle uyuşmuyor. ASLA tekrar denenmez.
#   2 = kullanım / ORTAM hatası — yanlış argüman, desteklenmeyen Python sürümü.
#   3 = İŞLETİM arızası — anahtar kütükte yok, dosya okunamadı, kütük bozuk.
#
# Sınıfları ayırmak ZORUNLU. "sıfır değil → uyuşmazlık" indirgemesi denetimde iki
# yanlış teşhis üretti (2026-07-28): okunamayan bir dosya (3) operatöre "baytlar
# pinle uyuşmuyor" diye raporlanıyordu — projenin doktrini "uyuşmazlık asla tekrar
# denenmez" olduğu için en pahalı yanlış yönlendirme buydu; ve Python 3.9'da import
# kapısına takılan koşu (2) "bu anahtar kütükte yok" gibi görünüyordu.
#
# Bu betikte HER sınıf build'i KIRIYOR (`return 1` + dosya başındaki `set -e`):
# uv olmadan Unity MCP hiç çalışmıyor, yani hiçbir sınıf "atla, devam et" ile
# kapatılamaz — sessizce bozuk installer göndermek demek olur. Sonuç aynı olsa da
# MESAJ ayrı, çünkü operatörün yapacağı iş sınıfa göre değişiyor: pin'i incele /
# Python'u değiştir / kütüğü onar.
#
# Python'un kendi stderr'i YUTULMUYOR: aşağıdaki çağrıların hiçbirinde 2> yönlendirmesi
# yok, yani `[pinned] …` açıklaması bu mesajlardan hemen önce ekrana düşüyor. Asıl
# teşhis orada; buradaki satırlar yalnızca sınıfı ve "ne yapmalı"yı söylüyor.
report_pinned_failure() {
  local rc="$1" key="$2" stage="$3"
  case "$rc" in
    1)
      echo "HATA: doğrulama başarısız ($key — $stage): indirilen baytlar sabitlenmiş" >&2
      echo "      özetle uyuşmuyor. Tekrar DENENMEZ — 'birkaç kez dene' bir saldırgana" >&2
      echo "      yalnızca birkaç şans daha verir. Ayrıntı yukarıdaki [pinned] satırlarında." >&2
      ;;
    2)
      echo "HATA: ortam hatası ($key — $stage): Python sürümü ya da çağrı biçimi uygun değil." >&2
      echo "      Bu bir BÜTÜNLÜK arızası DEĞİL: hiçbir asset doğrulanmadı, hiçbir özet" >&2
      echo "      uyuşmazlığı bulunmadı. Çözüm yukarıda Python'un kendi mesajında yazıyor" >&2
      echo "      (stok macOS python3 3.9.6 yetmiyor; en az 3.10 gerekiyor)." >&2
      ;;
    3)
      echo "HATA: işletim hatası ($key — $stage): anahtar kütükte yok, dosya okunamadı ya da" >&2
      echo "      kütük ayrıştırılamadı ($PINNED). Düzeltilebilir bir durum ve bütünlük" >&2
      echo "      arızasıyla ilgisi YOK. Ayrıntı yukarıdaki [pinned] satırında." >&2
      ;;
    *)
      echo "HATA: beklenmedik çıkış kodu $rc ($key — $stage)." >&2
      echo "      $PINNED sözleşmesi yalnız 0/1/2/3 tanımlıyor; bilinmeyen bir kod o" >&2
      echo "      sözleşmenin değiştiğine işaret eder. Bilinen bir sınıfa SOKULMUYOR:" >&2
      echo "      yanlış sınıflandırma, sınıflandırmamaktan pahalıya geliyor." >&2
      ;;
  esac
  # Ortak kapanış: sınıf ne olursa olsun build burada duruyor. Tek yerde yazılı,
  # çünkü aşamaların birine eklenip diğerine eklenmeyen bir satır "bazı sınıflar
  # devam ediyor" izlenimi veriyordu.
  echo "      Build DURDURULDU — uv olmadan Unity MCP hiç çalışmıyor." >&2
}

fetch_arch() {
  local uv_triple="$1"   # astral-sh/uv release asset triple (arşiv içindeki dizin adı da bu)
  local out_subdir="$2"  # vendor/uv/<out_subdir>
  local key="$3"         # pinned_assets.json anahtarı
  local url rc
  # Kod `|| rc=$?` ile YAKALANIYOR, `if !` ile değil: `if !` yalnız "sıfır mı değil mi"
  # bırakıyor ve sınıf bilgisi orada kayboluyor. Düz `komut; rc=$?` de olmaz — bu dosyada
  # `set -e` var, başarısız komut rc satırına gelmeden betiği öldürürdü. `||` listesi
  # `set -e`'yi bu komut için askıya alıyor ve `$?` doğrudan komuttan geliyor (bu depoda
  # ölçüldü: bir pipe'tan sonraki `$?` yanlış komutu ölçüyor).
  rc=0
  url="$(python3 "$PINNED" url "$key")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    report_pinned_failure "$rc" "$key" "adres okuma"
    return 1
  fi
  local tmp; tmp="$(mktemp -d)"
  echo "uv indiriliyor ($out_subdir): $url"
  curl -fsSL "$url" -o "$tmp/uv.tar.gz"
  # Bütünlük kapısı ÇIKARMADAN ÖNCE ve İNDİRİLEN ARŞİV üzerinde: çıkarılmış dosyaya
  # bakmak, bozuk arşivi zaten açtıktan sonra kontrol etmek olurdu.
  # Uyuşmazlıkta build KIRILIR, atlanmaz: uv olmadan Unity MCP hiç çalışmıyor, yani
  # "uyarı verip devam et" son kullanıcıya sessizce bozuk bir installer göndermektir.
  rc=0
  python3 "$PINNED" verify "$key" "$tmp/uv.tar.gz" || rc=$?
  if [ "$rc" -ne 0 ]; then
    rm -rf "$tmp"
    report_pinned_failure "$rc" "$key" "bütünlük doğrulaması"
    return 1
  fi
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  local extracted="$tmp/uv-${uv_triple}"
  local target="$DEST/$out_subdir"
  rm -rf "$target"; mkdir -p "$target"
  cp "$extracted/uv" "$extracted/uvx" "$target/"
  chmod +x "$target/uv" "$target/uvx"
  rm -rf "$tmp"
  echo "Tamam → $target"
  ls -lh "$target"
}

fetch_arch "aarch64-apple-darwin" "darwin-arm64" "uv/darwin-arm64"

echo ""
echo "=== uv araç zinciri hazır: $DEST ==="
