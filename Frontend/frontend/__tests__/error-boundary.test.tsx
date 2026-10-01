/**
 * Beyaz ekranın kapanması — ErrorBoundary.
 *
 * Arıza (kullanıcı bildirimi, 2 Ağu 2026): dosya ağacında AI'ın düzenlediği
 * (git durumu yeşil) bir dosyaya tıklanınca pencere bomboş kalıyor ve
 * uygulamayı KAPATIP AÇMAK gerekiyor. Sebep React'in varsayılanı: yakalanmayan
 * bir render hatasında tüm ağaç unmount ediliyor. Depoda hiç hata sınırı yoktu
 * (ölçüm: `getDerivedStateFromError` sıfır eşleşme).
 *
 * Testler iki AYRI iddiayı ölçüyor ve ikisi de gerekli:
 *   1. Kurtarma — kullanıcı uygulamayı kapatmadan devam edebiliyor.
 *   2. Teşhis — hatanın METNİ ekranda. Beyaz ekranın en pahalı yanı bilgiyi
 *      yok etmesiydi; yalnız "bir şey ters gitti" diyen bir kart o kaybı
 *      tekrarlardı, sadece daha kibar biçimde.
 */
import { readFileSync } from 'fs'
import { resolve } from 'path'

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'

import { ErrorBoundary } from '../renderer/components/ui/ErrorBoundary'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const PATLAMA = 'getLineContent çağrısı geçersiz satır numarası aldı'

const Patlayan = () => {
  throw new Error(PATLAMA)
}

beforeEach(() => {
  // React bir sınır yakaladığında kendi `console.error`'ını basıyor; testin
  // çıktısını kirletmesin. Depoda yerleşik desen.
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('ErrorBoundary — kurtarma', () => {
  it('hata yokken çocukları OLDUĞU GİBİ geçiriyor', () => {
    // Ters yön şart: her zaman hata kartı gösteren bir mutant, aşağıdaki
    // testlerin hepsini geçerdi.
    render(
      <ErrorBoundary>
        <div>normal içerik</div>
      </ErrorBoundary>
    )
    expect(screen.getByText('normal içerik')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('çocuk render sırasında fırlatınca EKRAN BOŞ KALMIYOR', () => {
    render(
      <ErrorBoundary>
        <Patlayan />
      </ErrorBoundary>
    )
    // Asıl iddia bu: kullanıcı bir şey GÖRÜYOR. Arızanın kendisi "hiçbir şey
    // görünmüyor"du, dolayısıyla ölçüt de görünürlük olmalı.
    const kart = screen.getByRole('alert')
    expect(kart).toBeTruthy()
    expect(kart.textContent).toBeTruthy()
  })

  it('yeniden yükle düğmesi gerçekten tetikliyor', () => {
    // Düğmenin VARLIĞI yetmez: metni doğru ama hiçbir şey yapmayan bir düğme
    // kullanıcıyı yine uygulamayı kapatmaya bırakır.
    const onReload = vi.fn()
    render(
      <ErrorBoundary onReload={onReload}>
        <Patlayan />
      </ErrorBoundary>
    )
    fireEvent.click(screen.getByText('Yeniden Yükle'))
    expect(onReload).toHaveBeenCalledTimes(1)
  })
})

describe('ErrorBoundary — teşhis', () => {
  it('hatanın KENDİ METNİ kartta görünüyor', () => {
    // Bu testin sebebi doğrudan bir arıza: beyaz ekran, hangi bileşenin neden
    // patladığını herkesten gizliyordu. Genel bir özür metni aynı kaybı
    // tekrarlardı — kartın değeri hatayı TAŞIMASINDA.
    render(
      <ErrorBoundary>
        <Patlayan />
      </ErrorBoundary>
    )
    expect(screen.getByRole('alert').textContent).toContain(PATLAMA)
  })

  it('hata konsola da düşüyor — kart kapatılsa bile iz kalıyor', () => {
    render(
      <ErrorBoundary>
        <Patlayan />
      </ErrorBoundary>
    )
    const cagrilar = (console.error as any).mock.calls as unknown[][]
    const bizimkiler = cagrilar.filter(c => String(c[0]).includes('[ErrorBoundary]'))
    expect(bizimkiler.length).toBeGreaterThan(0)
  })
})

describe('ErrorBoundary — dil', () => {
  it('app-lang=en iken İngilizce metin gösteriyor', () => {
    // Bu bileşen `LangContext`'i GÖREMİYOR (provider `home.tsx` içinde, sınır
    // onu saran katmanda). Dil bu yüzden localStorage'dan okunuyor; test o
    // yolun gerçekten çalıştığını ölçüyor, yoksa dil sessizce hep `tr` kalırdı.
    // The test setup announces Turkish for out-of-provider renders; forget it
    // so this test measures the storage path it is about.
    aktifDilAyarla(null)
    localStorage.setItem('app-lang', 'en')
    render(
      <ErrorBoundary>
        <Patlayan />
      </ErrorBoundary>
    )
    expect(screen.getByText('Reload')).toBeTruthy()
  })

  it('anahtar yokken ham anahtarı basmıyor — Türkçe varsayılan', () => {
    localStorage.setItem('app-lang', 'tr')
    render(
      <ErrorBoundary>
        <Patlayan />
      </ErrorBoundary>
    )
    expect(screen.getByRole('alert').textContent).not.toContain('error.title')
  })
})

describe('sınır KENDİ render ında çökmüyor (denetim bulgusu)', () => {
  /**
   * ⚠️ `fallback-self-failure`: `tamMetin()` render yolunda `error.message` ve
   * `error.stack` okuyor. Fırlatılan değerin bunları sıradan veri alanı olarak
   * taşıması GARANTİ DEĞİL — Proxy ya da patlayan getter olabilir. Bir sınır
   * kendi render'ındaki hatayı YAKALAYAMAZ ve üstünde ikinci sınır da yok,
   * yani korumasız tek bir okuma beyaz ekranı geri getiriyordu.
   */
  const dusmanNesne = () =>
    new Proxy(new Error('orijinal'), {
      get(hedef, anahtar) {
        if (anahtar === 'message' || anahtar === 'stack') {
          throw new Error('getter patladı')
        }
        return (hedef as any)[anahtar]
      },
    })

  const Dusman = () => { throw dusmanNesne() }

  it('patlayan getter varken sınır YİNE de kart gösteriyor', () => {
    render(
      <ErrorBoundary>
        <Dusman />
      </ErrorBoundary>
    )
    // Asıl iddia: ekran BOŞ değil. Arıza "hiçbir şey görünmüyor"du, ölçüt de
    // görünürlük olmalı.
    const kart = screen.getByRole('alert')
    expect(kart).toBeTruthy()
    expect(kart.textContent).toBeTruthy()
  })

  it('ÖLÇÜM NOTU: React düşman değeri sınırdan ÖNCE etkisizleştiriyor', () => {
    // ⚠️ Bu test bir ÇÜRÜTMEYİ sabitliyor. Denetim bulgusu, patlayan bir
    // `message`/`stack` getter'ının sınırın KENDİ render'ında fırlayıp beyaz
    // ekranı geri getireceğini söylüyordu (`unverified` etiketliydi, doğru
    // ihtiyatla). Ölçüldü: React kendi hata işleme yolunda `error.message`'ı
    // sınırdan önce okuyor, getter orada patlıyor ve sınıra SIRADAN bir
    // `Error` ulaşıyor. Yani o vektör React 18'de erişilemez.
    //
    // `guvenliOku` yine de duruyor: ucuz, ve bu davranış React'in iç
    // ayrıntısı — sürüm değişince geri gelebilir. Bu test o varsayımın ne
    // zaman çöktüğünü söyleyecek olan şey.
    render(<ErrorBoundary><Dusman /></ErrorBoundary>)
    expect(screen.getByRole('alert').textContent).toContain('getter patladı')
  })

  it('yeniden yükle düğmesi bu durumda da çalışıyor', () => {
    const onReload = vi.fn()
    render(
      <ErrorBoundary onReload={onReload}>
        <Dusman />
      </ErrorBoundary>
    )
    fireEvent.click(screen.getByText('Yeniden Yükle'))
    expect(onReload).toHaveBeenCalledTimes(1)
  })
})

describe('kopyala YALANCI başarı göstermiyor (denetim bulgusu)', () => {
  /**
   * ⚠️ `misleading-success-state`: `writeText` bir promise döndürüyor, senkron
   * `try/catch` reddini göremiyor. Eski hâli reddi beklemeden "Kopyalandı"
   * yazıyordu — kullanıcı panoda olmayan bir metni yapıştırmaya çalışırdı.
   */
  const panoKur = (sonuc: 'ok' | 'red') => {
    const writeText = vi.fn(() =>
      sonuc === 'ok' ? Promise.resolve() : Promise.reject(new Error('NotAllowedError'))
    )
    Object.defineProperty(globalThis.navigator, 'clipboard', {
      value: { writeText }, configurable: true,
    })
    return writeText
  }

  it('pano REDDEDERSE kopyalandı YAZMIYOR', async () => {
    panoKur('red')
    render(<ErrorBoundary><Patlayan /></ErrorBoundary>)
    fireEvent.click(screen.getByText('Ayrıntıyı kopyala'))
    await new Promise(r => setTimeout(r, 0))
    expect(screen.queryByText('Kopyalandı')).toBeNull()
  })

  it('pano KABUL EDERSE kopyalandı yazıyor', async () => {
    // Ters yön: hiçbir zaman "Kopyalandı" yazmayan bir mutant üstteki testi
    // geçer ama düğmeyi geri bildirimsiz bırakırdı.
    panoKur('ok')
    render(<ErrorBoundary><Patlayan /></ErrorBoundary>)
    fireEvent.click(screen.getByText('Ayrıntıyı kopyala'))
    await new Promise(r => setTimeout(r, 0))
    expect(screen.getByText('Kopyalandı')).toBeTruthy()
  })
})

describe('_app.tsx sınırı gerçekten AĞACA koyuyor', () => {
  /**
   * İKİNCİL kontrol, kaynak metni okuyor. Birincil kanıt yukarıdaki DOM
   * testleri; buradaki soru farklı: "sınır uygulamanın ağacında mı". Depoda
   * ölçülmüş bir ders var — `ToastContainer` bütün birim testleri geçiyordu ve
   * hiçbir yere mount EDİLMEMİŞTİ. Sınır için aynı hata daha pahalıya gelirdi:
   * testler yeşil, kullanıcı yine beyaz ekran görür.
   */
  const src = readFileSync(resolve(__dirname, '../renderer/pages/_app.tsx'), 'utf8')

  it('ErrorBoundary sayfa bileşenini SARIYOR', () => {
    expect(src).toMatch(/<ErrorBoundary>[\s\S]{0,200}<Component\s/)
  })

  it('ConfirmDialogHost sınırın DIŞINDA kalıyor', () => {
    // Kasıtlı bir karar: içeri alınsaydı bir render hatası onay diyaloğunu da
    // unmount edip `confirmDialog()`'u native `confirm()`e düşürürdü (bilinen
    // Electron focus-kilit arızası). Karar yorumla yazılı; bu test onu
    // bağlayıcı yapıyor.
    const sinirIci = src.match(/<ErrorBoundary>([\s\S]*?)<\/ErrorBoundary>/)
    expect(sinirIci).toBeTruthy()
    expect(sinirIci![1]).not.toContain('ConfirmDialogHost')
    expect(src).toContain('<ConfirmDialogHost />')
  })
})
