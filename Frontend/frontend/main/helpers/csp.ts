import { app, session } from 'electron'

/**
 * Content-Security-Policy — 2026-07-27 denetiminin sertleştirme planındaki
 * üçüncü katman.
 *
 * İlk iki katman sayfaya DIŞARIDAN girmeyi engelliyor:
 *   - create-window.ts — gezinme/popup/webview politikası: uzak bir belge
 *     renderer'a hiç giremiyor.
 *   - ipc-trust.ts — her IPC çağrısının gönderen çerçevesi doğrulanıyor.
 *
 * İkisinin de yakalayamadığı senaryo şu: belge BİZİM belgemiz, ama içinde
 * saldırganın verisi çalışıyor. Bu uygulamada güvenilmeyen metin normal yoldan
 * DOM'a giriyor — model çıktısı Markdown olarak render ediliyor, workspace
 * dosyalarının içeriği editöre ve sohbete basılıyor, backend yanıtları
 * arayüze dağılıyor. Böyle bir enjeksiyon "başka bir sayfaya gitmek" istemez;
 * olduğu yerde kalıp iki şey yapar: (a) uzaktan KOD yükler, (b) eline geçeni
 * dışarıdaki bir origin'e SIZDIRIR. CSP tam olarak bu iki fiili kısıtlıyor.
 *
 * Neden meta tag değil de yanıt başlığı: `frame-ancestors` gibi direktifler
 * meta ile hiç çalışmıyor ve meta ancak parse edildiği noktadan sonrasını
 * bağlıyor. `app://` şeması için `webRequest.onHeadersReceived`'in gerçekten
 * ateşlendiği ölçüldü (Electron 34.5.8, mainFrame dahil) — varsayılmadı.
 */

/**
 * MONACO ARTIK CDN'DEN GELMİYOR — buradaki jsdelivr izinleri 2026-07-27'de
 * kaldırıldı. Editör dosyaları `scripts/copy-monaco.js` ile node_modules'tan
 * `renderer/public/monaco/` altına kopyalanıyor ve uygulamanın kendi
 * origin'inden (`app://./monaco/vs`, dev'de `/monaco/vs`) servis ediliyor;
 * yapılandırma renderer/components/home/monaco-loader.ts'te.
 *
 * Kaldırılan izinler (her biri politikadan çıkarılıp CANLI doğrulandı):
 *   - script-src'den jsdelivr öneki  → yerine 'self' yetiyor
 *   - style-src'den jsdelivr öneki   → editor.main.css artık same-origin
 * Kazanç: renderer bağlamında (yani `window.ipc`'yi gören bağlamda) çalışan
 * uzak JavaScript kalmadı, ve editör çevrimdışı açılıyor.
 *
 * KALDIRILMAYAN: `worker-src blob:` — bkz. aşağıdaki direktif yorumu. Yerel
 * servise geçmek bu ihtiyacı ORTADAN KALDIRMADI; ölçüldü.
 */

/**
 * Politikayı üretir. `isProd` parametre olarak alınıyor (modül düzeyinde
 * okunmuyor) ki test iki dalı da doğrudan sınayabilsin.
 */
export const buildPolicy = (isProd: boolean): string => {
  // Aşağıdaki her kaynak ölçülerek eklendi: kaldırıldığında renderer'da somut
  // bir CSP ihlali üreten kaynaklar. Ölçüm yöntemi, prod build'i `app://`
  // üzerinden yükleyip politikayı tek tek daraltmak ve `securitypolicyviolation`
  // olaylarını toplamaktı.

  // 'self' = prod'da app://, dev'de Next dev sunucusu. Monaco da buraya dahil:
  // yerelden servis edildiği için ayrı bir kaynak ifadesi GEREKMİYOR.
  const scriptSrc = ["'self'"]
  // Backend dinamik portta dinliyor (bkz. background.ts getBackendBaseUrl), o
  // yüzden port joker: sabit bir port yazmak ilk açılışta uygulamayı kırardı.
  // localhost:8080 ayrıca Unity MCP köprüsünün sağlık ucu (TerminalPanel).
  const connectSrc = ["'self'", 'http://127.0.0.1:*', 'http://localhost:*']

  if (!isProd) {
    // ── YALNIZCA DEV ───────────────────────────────────────────────────────
    // Next dev sunucusu webpack'i `eval` tabanlı devtool ile derliyor ve hata
    // katmanını satır içi <script>'lerle enjekte ediyor; HMR de websocket
    // kullanıyor. Üçü de prod'da GEREKMİYOR — ölçüldü: prod HTML'inde tek bir
    // çalıştırılabilir satır içi script yok. (`__NEXT_DATA__` bir
    // `application/json` veri bloğu; HTML'in "prepare the script element"
    // algoritması onu CSP kontrolünden ÖNCE eliyor, dolayısıyla nonce
    // gerektirmiyor. Prod sayfası tam yüklenip hidrate olurken sıfır ihlal
    // üretti.)
    //
    // Gevşeklik bilerek tek bir dalda toplanmış: prod dizileri yukarıda
    // kapanıyor, bu blok yalnızca dev'de çalışıyor. 'unsafe-eval' +
    // 'unsafe-inline' prod'a sızsaydı politikanın kod-yükleme tarafı tümüyle
    // anlamsızlaşırdı — CSP'nin asıl işi o taraf.
    scriptSrc.push("'unsafe-inline'", "'unsafe-eval'")
    connectSrc.push('ws://127.0.0.1:*', 'ws://localhost:*')
  }

  const directives: Record<string, string[]> = {
    // Aşağıda açıkça yazılmayan her şey (media-src, manifest-src, ...) buraya
    // düşer. Uygulamada <video>/<audio> yok — video ekleri emoji ile temsil
    // ediliyor — o yüzden media-src ayrıca gevşetilmedi.
    'default-src': ["'self'"],

    'script-src': scriptSrc,

    // blob: KALDI ve bu kasıtlı. Monaco yerelden servis edilmeye geçince "artık
    // same-origin, blob sarmalayıcısına gerek kalmaz" beklentisi doğaldı —
    // YANLIŞ ÇIKTI. monaco-editor 0.55.1'in AMD paketi worker'ı KOŞULSUZ blob
    // üzerinden kuruyor; kaynakta origin karşılaştırması yok:
    //   min/vs/editor/editor.main.js →
    //   MonacoEnvironment.getWorker = (_, label) => new Worker(F(<workerUrl>))
    //   F(e) = URL.createObjectURL(new Blob(["… importScripts(e) …"]))
    // Yani sarmalayıcı origin'e bakmadan her zaman devrede.
    // Ayrıca canlı ölçüldü (prod app:// build'i, aşağıdaki iki koşu):
    //   blob: YOKKEN  → "Refused to create a worker from 'blob:app://./…'"
    //                   ihlali + json worker hiç boot etmedi (marker gelmedi)
    //   blob: VARKEN  → sıfır ihlal, worker boot etti, marker geldi
    // DİKKAT: editör iki durumda da GÖZLE AÇILIYOR görünüyor (editorDom=true,
    // C# tokenizasyonu çalışıyor). "Editör görünüyor" bu direktifin doğruluğunun
    // kanıtı DEĞİL; ölen şey dil worker'ı oluyor.
    // Not: blob: URL sayfanın origin'ini miras alsa da ('blob:app://./…') CSP
    // kaynak eşleştirmesinde 'self' bunu KARŞILAMIYOR — blob ayrı bir şema.
    //
    // 'self' ise ölçüldü ve Monaco için GEREKMİYOR: yalnız `worker-src blob:`
    // ile de worker boot etti, sıfır ihlal çıktı (worker'ın importScripts
    // ettiği same-origin dosya script-src'ye tabi, worker-src'ye değil).
    // Yine de bırakıldı çünkü genişleme yüzeyi yok: 'self' sadece bizim kendi
    // dosyalarımızdan worker kurmaya izin verir, uzak kod yükletmez.
    'worker-src': ["'self'", 'blob:'],

    // 'unsafe-inline' bilinçli bir taviz: Monaco tema/CSS'ini, xterm ve
    // framer-motion çalışma anında <style> enjekte ediyor. Bunu nonce'a
    // çevirmek üçüncü parti kütüphanelerin içine girmeyi gerektirirdi.
    // Kabul edilebilir olmasının sebebi: satır içi stil KOD ÇALIŞTIRMAZ, ve
    // Font styles and Monaco CSS are bundled; no remote style origin is needed.
    'style-src': ["'self'", "'unsafe-inline'"],

    // Fonts are bundled. Monaco's embedded codicon font still requires data:.
    'font-src': ["'self'", 'data:'],

    // data: ölçülmüş bir ihtiyaç: sohbete eklenen görseller ve önizlemeleri
    // FileReader.readAsDataURL ile data: URI olarak render ediliyor
    // (animated-ai-chat.tsx, ChatPanel.tsx).
    // ⛔ `https://images.unsplash.com` KALDIRILDI (8 Ağu 2026): o izin yalnızca
    // `AuthScreen.tsx`'in uzak hero görseli için vardı ve o dosya ölü koddu
    // (hiçbir yerden import edilmiyordu) — silindi. Uygulama artık hiçbir uzak
    // görsel çekmiyor, yani izin sürdürülürse bedeli olan ama karşılığı olmayan
    // bir dış kaynak açık kalırdı. Ölü kodu silmek CSP'yi de daralttı.
    // blob: ise ÖLÇÜLMÜŞ DEĞİL, temkinli bir izin: kodda createObjectURL
    // bulunmadı. Güvenlik bedeli yok (blob'u sayfanın kendisi üretir, dışarı
    // veri taşımaz), o yüzden bırakıldı.
    'img-src': ["'self'", 'data:', 'blob:'],

    // Sızdırmayı asıl kapatan direktif bu. Uygulama dışarıya yalnızca kendi
    // yerel backend'ine konuşuyor; sağlayıcı API'leri (Claude/Codex/...)
    // renderer'dan değil backend üzerinden çağrılıyor — ölçüldü, renderer
    // kaynağında uzak bir API origin'i yok.
    'connect-src': connectSrc,

    // <object>/<embed>: eski eklenti yüzeyi, uygulamada hiç kullanılmıyor.
    'object-src': ["'none'"],
    // Enjekte edilen bir <base>, göreli script yollarını saldırganın
    // sunucusuna yönlendirebilirdi; uygulama <base> kullanmıyor.
    'base-uri': ["'none'"],
    // Uygulamada iframe yok; webview zaten create-window.ts'te reddediliyor.
    'frame-src': ["'none'"],
    'frame-ancestors': ["'none'"],

    // 'none' DEĞİL, bilerek 'self'. Giriş formunun submit handler'ı
    // (useAuth.ts noopAuthSubmit) preventDefault çağırmıyor, yani gerçek bir
    // form gezinmesi olabiliyor; 'none' bunu bloklayıp mevcut davranışı
    // değiştirirdi. 'self' ise asıl tehdidi — kimlik alanlarının uzak bir
    // endpoint'e POST edilmesini — kapatıyor.
    'form-action': ["'self'"],
  }

  return Object.entries(directives)
    .map(([name, sources]) => `${name} ${sources.join(' ')}`)
    .join('; ')
}

/**
 * Politikayı kurar. `app.whenReady()` sonrasında çağrılmalı: `session.defaultSession`
 * ondan önce yok.
 */
export const applyContentSecurityPolicy = (isProd: boolean): void => {
  const policy = buildPolicy(isProd)

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    // Yalnızca belge yanıtlarına yazılıyor. CSP belgeye uygulanır ve oradan
    // alt kaynaklara, blob worker'lara ve türetilen bağlamlara miras kalır;
    // her yanıta yazmak backend'in XHR cevaplarına anlamsız başlık eklerdi.
    if (details.resourceType !== 'mainFrame' && details.resourceType !== 'subFrame') {
      callback({})
      return
    }

    // Var olan CSP başlıkları önce SİLİNİYOR. Sebep: iki CSP başlığı birleşmez,
    // KESİŞİR — ikisini de sağlayan bir politika ortaya çıkar ve teşhisi zordur.
    // Anahtarlar büyük/küçük harf duyarsız olduğu için tek bir yazımı silmek
    // yetmiyor; hepsi taranıyor.
    const headers = { ...details.responseHeaders }
    for (const name of Object.keys(headers)) {
      const lower = name.toLowerCase()
      if (lower === 'content-security-policy' || lower === 'content-security-policy-report-only') {
        delete headers[name]
      }
    }
    headers['Content-Security-Policy'] = [policy]
    callback({ responseHeaders: headers })
  })

  console.log(`[csp] politika kuruldu (${isProd ? 'prod' : 'dev'}): ${policy}`)

  // İhlal sessiz kalmasın. Paketlenmiş uygulamada DevTools konsolu kullanıcıya
  // görünmüyor; background.ts console.* çağrılarını kalıcı log dosyasına
  // tee'liyor, yani buradan geçen satır sahada aranabilir hale geliyor. Bu
  // kasıtlı bir emniyet ağı: politikayı canlı sınayamadığım akışlar (giriş
  // sonrası sohbet, xterm, syntax highlighter) bir gün ihlal üretirse sonuç
  // "sebepsiz bozuk arayüz" değil, log'da adı geçen bir direktif olur.
  app.on('web-contents-created', (_event, contents) => {
    // Electron 34 imzası: (event, level, message, line, sourceId).
    contents.on('console-message' as any, (...args: any[]) => {
      const message = typeof args[2] === 'string' ? args[2] : String(args[1]?.message ?? '')
      if (message.includes('Content Security Policy')) {
        console.error('[csp] İHLAL:', message.slice(0, 500))
      }
    })
  })
}
