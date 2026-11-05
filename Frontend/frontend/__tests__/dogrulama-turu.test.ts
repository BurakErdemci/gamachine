/**
 * 2 Ağu 2026 DOĞRULAMA turundan terfi eden testler.
 *
 * Bu turun konusu, bir önceki turun düzeltmeleriydi — ve bulguların en pahalısı
 * şuydu: `fa8b810` kapattığını İDDİA ETTİĞİ yarışı kapatmıyordu. Commit mesajı
 * ayrıca "probe'un iddiası artık bir test" diyordu; öyle bir test yoktu.
 * Buradaki blokların varlık sebebi o: iddia edilen her kapanış, ürünün kendi
 * gövdesi çalıştırılarak ölçülüyor.
 */
import fs from 'fs'
import os from 'os'
import path from 'path'

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

import { ayniBelgeMi } from '../main/helpers/create-window'
import {
  alternatifVeriAkisiMi,
  okumaKarariVer,
  taniticiKapsamdaMi,
} from '../main/helpers/file-security'
import { yerelYolaCevir } from '../renderer/lib/chatLink'

/** Windows'ta junction, POSIX'te dizin sembolik bağı — ikisi de yetki istemiyor. */
const dizinBagi = (hedef: string, bag: string): boolean => {
  try {
    fs.symlinkSync(hedef, bag, process.platform === 'win32' ? 'junction' : 'dir')
    return true
  } catch {
    return false // bu platformda/izinde kurulamıyor — iddia ölçülemez
  }
}

describe('okuma kapısı: KARAR ile AÇILACAK YOL tek çözümden geliyor', () => {
  let kok = ''
  let ws = ''

  beforeEach(() => {
    // `.native`: the gate resolves with it, and only it expands a Windows 8.3 short name
    // (RUNNER~1) in the temp dir to the long one the gate returns.
    kok = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), 'dogrulama-')))
    ws = path.join(kok, 'ws')
    fs.mkdirSync(path.join(ws, 'gercek'), { recursive: true })
    fs.writeFileSync(path.join(ws, 'gercek', 'note.txt'), 'icerideki')
  })
  afterEach(() => {
    try { fs.rmSync(kok, { recursive: true, force: true }) } catch { /* yok */ }
  })

  it('izin verilen dosya için çözülmüş yol da dönüyor', () => {
    const k = okumaKarariVer(path.join(ws, 'gercek', 'note.txt'), ws)
    expect(k.izinli).toBe(true)
    expect(k.cozulmusYol).toBe(path.join(ws, 'gercek', 'note.txt'))
  })

  it('KARARIN dayandığı yol, junction ÇÖZÜLMÜŞ hâlidir', () => {
    // ⚠️ Bulgu `check-open-resolve-divergence`: eskiden kapı kendi içinde bir
    // kez, çağıran `resolvedReadPath` ile ikinci kez çözüyordu. İki çözüm
    // arasında `pivot` başka yere çevrilince KONTROL EDİLEN dosya ile AÇILAN
    // dosya ayrışıyordu. Tek çözüm noktası, ayrışmanın kaynağını kaldırıyor.
    if (!dizinBagi(path.join(ws, 'gercek'), path.join(ws, 'pivot'))) return
    const k = okumaKarariVer(path.join(ws, 'pivot', 'note.txt'), ws)
    expect(k.izinli).toBe(true)
    // Ters yön: ham yolu döndüren bir mutant burada yakalanıyor.
    expect(k.cozulmusYol).toBe(path.join(ws, 'gercek', 'note.txt'))
  })

  it('workspace DIŞINA çözülen yol reddediliyor', () => {
    fs.mkdirSync(path.join(kok, 'disari'), { recursive: true })
    fs.writeFileSync(path.join(kok, 'disari', 'sir.txt'), 'DISARIDAKI SIR')
    if (!dizinBagi(path.join(kok, 'disari'), path.join(ws, 'pivot'))) return
    expect(okumaKarariVer(path.join(ws, 'pivot', 'sir.txt'), ws).izinli).toBe(false)
  })
})

describe('SINIFI KAPATAN kontrol: açılmış tanıtıcı gerçekten kapsamda mı', () => {
  let kok = ''
  let ws = ''
  let disari = ''

  beforeEach(() => {
    kok = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'tanitici-')))
    ws = path.join(kok, 'ws')
    disari = path.join(kok, 'disari')
    fs.mkdirSync(path.join(ws, 'gercek'), { recursive: true })
    fs.mkdirSync(disari, { recursive: true })
    fs.writeFileSync(path.join(ws, 'gercek', 'note.txt'), 'icerideki')
    fs.writeFileSync(path.join(disari, 'note.txt'), 'DISARIDAKI SIR')
  })
  afterEach(() => {
    try { fs.rmSync(kok, { recursive: true, force: true }) } catch { /* yok */ }
  })

  it('dokunulmamış dosyada GEÇİYOR', () => {
    // Ters yön: her şeyi reddeden bir mutant aşağıdakileri de geçerdi.
    const p = path.join(ws, 'gercek', 'note.txt')
    const fd = fs.openSync(p, 'r')
    try {
      expect(taniticiKapsamdaMi(fs.fstatSync(fd, { bigint: true }), p, ws)).toBe(true)
    } finally { fs.closeSync(fd) }
  })

  it('AÇILDIKTAN SONRA bileşen takas edilirse REDDEDİLİYOR', () => {
    // Tek çözüm yarışı daraltıyor ama kapatmıyor: açılacak yolun bir bileşeni
    // `openSync`'ten hemen önce de takas edilebilir. Sınıfı kapatan kontrol bu.
    //
    // Takas edilen bileşen junction'ın KENDİSİ — saldırganın gerçekte yapacağı
    // şey de bu. (Dizini yeniden adlandırmak Windows'ta işe yaramıyor: içinde
    // açık tanıtıcı varken `rename` EPERM veriyor. Ölçüldü, 2 Ağu 2026.)
    const pivot = path.join(ws, 'pivot')
    if (!dizinBagi(path.join(ws, 'gercek'), pivot)) return
    const p = path.join(pivot, 'note.txt')
    const fd = fs.openSync(p, 'r')
    try {
      const st = fs.fstatSync(fd, { bigint: true })
      expect(taniticiKapsamdaMi(st, p, ws)).toBe(true) // takastan ÖNCE
      fs.rmSync(pivot, { recursive: true, force: true })
      if (!dizinBagi(disari, pivot)) return
      // Aynı yol dizgesi artık workspace DIŞINDAKİ dosyayı adlandırıyor.
      expect(taniticiKapsamdaMi(st, p, ws)).toBe(false)
    } finally { fs.closeSync(fd) }
  })

  it('yol kapsamda ama TANITICI başka dosyaysa REDDEDİLİYOR', () => {
    // Kapsama kontrolünü tek başına bırakan bir mutant burada düşüyor:
    // saldırgan açıştan sonra geri takas ederse yol yine içeride görünür.
    const icerideki = path.join(ws, 'gercek', 'note.txt')
    const fd = fs.openSync(path.join(disari, 'note.txt'), 'r')
    try {
      const disaridakiSt = fs.fstatSync(fd, { bigint: true })
      // ÖNKOŞUL: iki dosyanın TAM (64 bit) tanıtıcıları farklı olmalı. Eşit
      // çıkarsa ortam çakışan kimlik dağıtmış demektir — o zaman aşağıdaki red
      // iddiası hiçbir şey ölçmez, çünkü kimlik kontrolü zaten ısıramaz.
      // Bilerek atlanmıyor, gürültüyle düşüyor.
      expect(
        fs.statSync(icerideki, { bigint: true }).ino,
        'ortam iki AYRI dosyaya aynı inode verdi; alttaki red iddiası ölçüsüz kalır',
      ).not.toBe(disaridakiSt.ino)
      expect(taniticiKapsamdaMi(disaridakiSt, icerideki, ws)).toBe(false)
    } finally { fs.closeSync(fd) }
  })

  it('TANITICI yolla eşleşse bile yol KAPSAM DIŞIYSA reddediliyor', () => {
    // Bu testin varlık sebebi bir MUTASYON ölçümü: kapsama kontrolünü silen
    // mutant, yukarıdaki testlerin hepsini geçiyordu — çünkü onlarda kimlik
    // kontrolü zaten ısırıyordu. Kapsamanın TEK BAŞINA gerekli olduğu durum bu:
    // tanıtıcı gerçekten o yolun gösterdiği dosya, ama o dosya dışarıda.
    const pivot = path.join(ws, 'pivot')
    if (!dizinBagi(disari, pivot)) return
    const p = path.join(pivot, 'note.txt')
    const fd = fs.openSync(p, 'r')
    try {
      // Kimlik EŞLEŞİYOR (aynı dosya) — reddi sağlayan yalnız kapsama.
      const st = fs.fstatSync(fd, { bigint: true })
      expect(fs.statSync(p, { bigint: true }).ino).toBe(st.ino)
      expect(taniticiKapsamdaMi(st, p, ws)).toBe(false)
    } finally { fs.closeSync(fd) }
  })

  it('2^53 ÜSTÜNDE 1 fark eden tanıtıcılar AYRI sayılıyor', () => {
    // Kimlik karşılaştırması TAM olmalı. `fs.Stats.ino` bir JS sayısı, yani
    // double: 2^53 üstünde komşu tamsayılar aynı değere yuvarlanıyor —
    // `Number(2**60) === Number(2**60 + 1)` bigint tarafında DOĞRU. Double'a dayanan
    // eski karşılaştırma bu iki AYRI dosyayı aynı sayar, yani kapı yanlış
    // KABUL verirdi. NTFS dosya referans numarası 64 bit olduğu için bu
    // büyüklük gerçek: bu diskte 400 dosyanın 201'i 2^53 üstünde ino aldı.
    // `BigInt(2) ** BigInt(60)`, not `2n ** 60n`: tsconfig targets ES2017.
    const IKI_USSU_60 = BigInt(2) ** BigInt(60)
    const p = path.join(ws, 'gercek', 'note.txt')
    const fd = fs.openSync(p, 'r')
    try {
      const st = fs.fstatSync(fd, { bigint: true })
      const komsu = { ...st, ino: IKI_USSU_60 + BigInt(1), dev: st.dev } as unknown as fs.BigIntStats
      const casus = vi.spyOn(fs, 'statSync').mockReturnValue(
        { ...st, ino: IKI_USSU_60, dev: st.dev } as unknown as fs.BigIntStats,
      )
      try {
        expect(taniticiKapsamdaMi(komsu, p, ws)).toBe(false)
      } finally { casus.mockRestore() }
    } finally { fs.closeSync(fd) }
  })

  it('var olmayan yolda FIRLATMIYOR, reddediyor', () => {
    const fd = fs.openSync(path.join(ws, 'gercek', 'note.txt'), 'r')
    try {
      expect(taniticiKapsamdaMi(fs.fstatSync(fd, { bigint: true }), path.join(ws, 'yok.txt'), ws)).toBe(false)
    } finally { fs.closeSync(fd) }
  })
})

describe('ayniBelgeMi ŞEMA ve HOST\'u da karşılaştırıyor', () => {
  // ⚠️ Bulgu `naive-origin-equality-reintroduced`: karşılaştırma `u.origin`
  // kullanıyordu ve `origin`, kayıtlı olmayan her şema için — üründe ÜRETİMDE
  // kullanılan `app:` dahil — sabit `"null"` dizgesi. Yani bu fonksiyon
  // üretimde HER `app:` adresini "aynı belge" sayıyordu.
  //
  // ⭐ Bu blok aynı zamanda bir önceki turun KÖR NOKTASI: eski testlerin altı
  // iddiası da tek bir origin (`app://.`) kullandığı için, `origin`'i tamamen
  // silen bir mutant hepsini geçiyordu.
  const SAYFA = 'app://./home/'

  it('BAŞKA HOST aynı belge DEĞİL', () => {
    expect(ayniBelgeMi('app://evil/home/', SAYFA)).toBe(false)
  })

  it('BOŞ host ile nokta host aynı belge DEĞİL', () => {
    expect(ayniBelgeMi('app:///home', SAYFA)).toBe(false)
  })

  it('BAŞKA ŞEMA aynı belge DEĞİL', () => {
    expect(ayniBelgeMi('http://evil.example/home/', SAYFA)).toBe(false)
    expect(ayniBelgeMi('https://./home/', SAYFA)).toBe(false)
  })

  it('aynı belge HÂLÂ aynı — muhafız meşru yeniden yüklemeyi kesmiyor', () => {
    expect(ayniBelgeMi(SAYFA, SAYFA)).toBe(true)
    expect(ayniBelgeMi('app://./home', SAYFA)).toBe(true)
  })
})

describe('alternatif veri akışı kontrolü PLATFORMA bağlı', () => {
  // ⚠️ Bulgu `ads-check-rejects-legitimate-posix-paths`. Bu blok, platformu
  // PARAMETRE alan bir gövde olmadan yazılamazdı: testler Windows'ta koşuyor,
  // dolayısıyla POSIX dalı `process.platform`'a bakan bir kodda ölçülemez —
  // ve ölçülemeyen dal, mutasyon testinde sessizce hayatta kalıyordu.

  it('Windows: akış yazımı reddediliyor', () => {
    expect(alternatifVeriAkisiMi('C:\\ws\\host.exe:notes.txt', 'win32')).toBe(true)
  })

  it('Windows: sürücü harfi tek başına akış SAYILMIYOR', () => {
    expect(alternatifVeriAkisiMi('C:\\ws\\notes.txt', 'win32')).toBe(false)
  })

  it('POSIX: iki nokta SIRADAN bir dosya adı karakteri', () => {
    // macOS Finder'da `Raporlar 1/2` adlı bir klasör diskte `Raporlar 1:2`
    // olarak duruyor. Platform koşulu olmadan kullanıcı o dosyayı açamıyordu.
    expect(alternatifVeriAkisiMi('/home/burcu/ws/build:2026.json', 'linux')).toBe(false)
    expect(alternatifVeriAkisiMi('/Users/burcu/ws/Raporlar 1:2/notes.txt', 'darwin')).toBe(false)
  })
})

describe('file: URL host korunuyor', () => {
  it('UNC file: URL UNC yoluna dönüşüyor', () => {
    // ⚠️ Bulgu `file-url-host-silently-dropped`: yalnız `pathname` alınınca
    // `file://sunucu/pay/x.txt` → `/pay/x.txt` oluyordu ve bu Windows'ta BAŞKA
    // bir dosyayı (`C:\pay\x.txt`) adlandırıyor. Güvenlik sorunu değil (kapı
    // reddediyor) ama yine "destekleniyor görünüp hiç çalışmayan dal".
    expect(yerelYolaCevir('file://sunucu/pay/x.txt')).toBe('\\\\sunucu\\pay\\x.txt')
  })

  it('host YOKSA eski davranış korunuyor', () => {
    expect(yerelYolaCevir('file:///C:/ws/notes.txt')).toBe('C:/ws/notes.txt')
  })
})
