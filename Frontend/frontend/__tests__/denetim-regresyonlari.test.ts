/**
 * 2 Ağu 2026 dış-göz denetiminden terfi eden regresyon testleri.
 *
 * Her blok, bir kırmızı takım probe'unun kalıcı karşılığı. Probe'ların kendisi
 * repoya girmiyor; iddiaları giriyor. Hepsi ÜRÜNÜN gövdesini çağırıyor —
 * bu turun en pahalı dersi, kopyalanan bir mantığın kopyasından ayrışmasıydı.
 */
import fs from 'fs'
import os from 'os'
import path from 'path'

import { describe, it, expect, beforeEach, afterEach } from 'vitest'

import { ayniBelgeMi } from '../main/helpers/create-window'
import { okumaKarariVer } from '../main/helpers/file-security'
import { yerelYolaCevir, linkTuru } from '../renderer/lib/chatLink'

describe('navigasyon ölçütü — "aynı yol" değil "aynı belge"', () => {
  const SAYFA = 'app://./home/'

  it('aynı belge: izin (yeniden yükleme)', () => {
    expect(ayniBelgeMi(SAYFA, SAYFA)).toBe(true)
  })

  it('SORGU DİZESİ farklıysa AYNI BELGE DEĞİL', () => {
    // ⚠️ Bulgu `incomplete-navigation-guard`: ilk sürüm yalnız `pathname`
    // karşılaştırıyordu, `?x=1` geçiyordu ve o navigasyon SPA'yı yine
    // boşaltıyordu — muhafız, var olma sebebi olan sınıfa açık kalmıştı.
    expect(ayniBelgeMi('app://./home/?conversation=63', SAYFA)).toBe(false)
  })

  it('sondaki eğik çizgi farkı AYNI belge sayılıyor', () => {
    // `next.config.js` `trailingSlash: true`; `/home` ile `/home/` aynı sayfa.
    // Normalleştirmezsek meşru bir bağlantı sessizce ölürdü (bulgu
    // `silent-navigation-rejection`).
    expect(ayniBelgeMi('app://./home', SAYFA)).toBe(true)
  })

  it('hash farkı AYNI belge sayılıyor — çapa boşaltmıyor', () => {
    expect(ayniBelgeMi('app://./home/#bolum', SAYFA)).toBe(true)
  })

  it('başka yol AYNI belge değil', () => {
    expect(ayniBelgeMi('app://./settings/', SAYFA)).toBe(false)
  })

  it('ayrıştırılamayan adres FIRLATIYOR — çağıran fail-closed yorumluyor', () => {
    // ⚠️ Bulgu `fail-open-navigation`: boş/bozuk mevcut adres `catch`'e
    // düşüyordu ve `catch` `preventDefault` çağırmadığı için navigasyon
    // GEÇİYORDU. Fırlatmanın kendisi sözleşmenin parçası.
    expect(() => ayniBelgeMi(SAYFA, '')).toThrow()
    expect(() => ayniBelgeMi(SAYFA, 'not a url')).toThrow()
  })
})

describe('will-redirect ürünün KENDİ açılışını kesmiyor', () => {
  /**
   * ⚠️ YAŞANMIŞ ARIZA (2 Ağu 2026): denetim bulgusunu kapatmak için eklediğim
   * `will-redirect` kancası origin-İÇİ yönlendirmeleri de engelliyordu ve
   * uygulama HİÇ AÇILMADI — `next.config.js` `trailingSlash: true` kullandığı
   * için dev sunucusu `/home` → `/home/` kanonikleştirmesini 302 ile yapıyor,
   * yani ürünün kendi açılışı bu olaydan geçiyor. Pencere beyaz kaldı.
   *
   * Bulgunun çekirdeği DIŞARI çıkan yönlendirmeydi; onu `isOwnOrigin` zaten
   * kapatıyor.
   *
   * ⭐ Ders: fail-closed sertleştirmenin de bir kapsamı olmalı. "Daha çok
   * engelle" güvenlik değil; çalışmayan bir ürünün güvenlik vaadi de yoktur.
   *
   * Ana süreç jsdom'da koşmuyor, ölçülebilir tek biçim kaynak metni — ama
   * iddia dar ve mekanik: origin-içi dal `preventDefault` içermemeli.
   */
  const src = fs.readFileSync(
    path.resolve(__dirname, '../main/helpers/create-window.ts'), 'utf8'
  )
  const handler = src.split("on('will-redirect'")[1].split('\n  })')[0]

  it('origin-içi yönlendirme ENGELLENMİYOR', () => {
    expect(handler).toMatch(/if\s*\(isOwnOrigin\(url\)\)\s*return/)
  })

  it('dışarı çıkan yönlendirme HÂLÂ engelleniyor', () => {
    // Ters yön: kancayı tamamen silen bir mutant üstteki testi geçerdi ve
    // denetim bulgusunu geri açardı.
    expect(handler).toContain('preventDefault')
    expect(handler).toContain('openExternally')
  })
})

describe('okuma kapısı — sabit bağ ve alternatif veri akışı', () => {
  let ws = ''

  beforeEach(() => {
    ws = fs.mkdtempSync(path.join(os.tmpdir(), 'denetim-regresyon-'))
  })
  afterEach(() => {
    try { fs.rmSync(ws, { recursive: true, force: true }) } catch { /* yok */ }
  })

  it('sıradan bir metin dosyası kabul ediliyor', () => {
    // Ters yön: her şeyi reddeden bir mutant aşağıdaki iki testi de geçerdi.
    const p = path.join(ws, 'notes.txt')
    fs.writeFileSync(p, 'merhaba')
    expect(okumaKarariVer(p, ws).izinli).toBe(true)
  })

  it('SABİT BAĞ reddediliyor', () => {
    // ⚠️ Bulgu `hardlink-containment-bypass`: `realpathSync` sembolik bağı ve
    // junction'ı çözüyor ama sabit bağ bir bağ DEĞİL, ikinci bir addır —
    // gerçek yolu workspace içinde kalır, baytları dışarıdakinin.
    // ⭐ Aynı sınıf backend'de zaten kapalıydı; ayrışan iki kopyaydı.
    const disarisi = path.join(os.tmpdir(), `denetim-sir-${process.pid}.json`)
    fs.writeFileSync(disarisi, '{"token":"disaridaki-sir"}')
    const yem = path.join(ws, 'notes.json')
    try {
      fs.linkSync(disarisi, yem)
    } catch {
      return // bu platformda sabit bağ kurulamıyor — iddia ölçülemez
    }
    try {
      expect(okumaKarariVer(yem, ws).izinli).toBe(false)
    } finally {
      try { fs.rmSync(disarisi, { force: true }) } catch { /* yok */ }
    }
  })

  it('ALTERNATİF VERİ AKIŞI — iki platformda İKİ DOĞRU cevap', () => {
    // ⚠️ Bulgu `alternate-data-stream-extension-bypass`: Windows
    // `host.exe:notes.txt`'yi `host.exe`'nin akışı olarak okuyor ama
    // `path.extname` sondaki `.txt`'yi dosyanın uzantısı sanıyordu — yani
    // uzantı beyaz listesi aslında `.exe` olan baytları yetkilendiriyordu.
    //
    // ⚠️ Bu iddia ÖNCE platform kipini koşulsuz iddia ediyordu ve CI'da
    // (Linux) kırmızı verdi — üründe açık YOKTU (ölçüldü 2 Ağu 2026). Bu
    // deponun kayıtlı sınıfı: bir davranış platforma bağlıysa testin de
    // bağlı olması gerekir, ve doğru araç `skipif` değil — o, iddiayı bir
    // ortamda sessizce yok ediyor. İki cevap da burada YAZILI duruyor.
    //
    // Ölçütün platformdan bağımsız hâli `alternatifVeriAkisiMi(yol, platform)`
    // ile ayrıca sınanıyor (`dogrulama-turu.test.ts`).
    const yol = path.join(ws, 'host.exe:notes.txt')
    if (process.platform === 'win32') {
      expect(okumaKarariVer(yol, ws).izinli).toBe(false)
    } else {
      // POSIX'te iki nokta sıradan bir dosya adı karakteri: bu bir AKIŞ değil,
      // gerçekten `host.exe:notes.txt` adlı bir dosya. Reddetmek burada ürün
      // hatası olurdu — macOS'ta Finder `/` içeren klasör adlarını diskte `:`
      // olarak saklıyor, yani meşru dosyalar açılamaz hale gelirdi.
      expect(okumaKarariVer(yol, ws).izinli).toBe(true)
    }
  })
})

describe('file: URL yola çevriliyor', () => {
  it('Windows file: URL sürücü yoluna dönüşüyor', () => {
    // ⚠️ Bulgu `renderer-gate-path-shape-mismatch`: `file:` bilerek
    // geçiriliyordu ama ana süreç yol dizgesi bekliyor
    // (`path.isAbsolute('file:///C:/a')` Windows'ta false), yani o dal hiç
    // çalışmıyordu — "destekliyoruz" görünen sessiz bir yalan.
    expect(yerelYolaCevir('file:///C:/ws/notes.txt')).toBe('C:/ws/notes.txt')
  })

  it('yüzde-kodlama file: içinde de çözülüyor', () => {
    expect(yerelYolaCevir('file:///C:/Unity%20Projeler/a.txt')).toBe('C:/Unity Projeler/a.txt')
  })

  it('file: hâlâ yerel dosya sayılıyor — sınıflandırma ile çevirim uyumlu', () => {
    expect(linkTuru('file:///C:/ws/a.txt')).toBe('yerel-dosya')
  })
})
