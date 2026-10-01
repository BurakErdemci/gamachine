/**
 * Test ortamı kurulumu — Node'un Web Storage'ı jsdom'unkini gölgeliyor.
 *
 * Ölçüm (2026-07-30, Node v25.2.1, Windows): Node artık `localStorage`'ı
 * VARSAYILAN olarak global'e koyuyor, ama `--localstorage-file` verilmediği
 * sürece o nesne kullanılamaz durumda — `localStorage.getItem` `undefined` ve
 * her erişim "`--localstorage-file` was provided without a valid path" uyarısı
 * basıyor. Global tanım bir getter ve jsdom'un sağlam `window.localStorage`'ını
 * gölgeliyor, dolayısıyla ürün kodundaki her `window.localStorage.getItem`
 * çağrısı TypeError atıyordu: bu makinede 238 testin 42'si bu yüzden kırmızıydı
 * (`useChat.ts:42`), macOS'taki eski Node'da aynı ağaç yeşildi.
 *
 * Yani arıza ürünün değil ortamın — düzeltmesi de ortamda. Kendi
 * implementasyonumuzu koyuyoruz: bağımlılık eklemiyor, deterministik, ve
 * jsdom'un hangi sürümde neyi sağladığına bağlı değil.
 */
import { beforeEach } from 'vitest'
import { aktifDilAyarla } from './renderer/lib/i18n'

const createStorage = (): Storage => {
  const map = new Map<string, string>()
  return {
    get length() { return map.size },
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    getItem: (k: string) => (map.has(String(k)) ? map.get(String(k))! : null),
    setItem: (k: string, v: string) => { map.set(String(k), String(v)) },
    removeItem: (k: string) => { map.delete(String(k)) },
    clear: () => { map.clear() },
  } as Storage
}

const install = (target: object) => {
  for (const ad of ['localStorage', 'sessionStorage'] as const) {
    Object.defineProperty(target, ad, {
      value: createStorage(),
      configurable: true,
      writable: true,
    })
  }
}

install(globalThis)
// jsdom + `globals: true` altında `window` genelde `globalThis`'in kendisi;
// ayrı nesne olduğu kurulumlarda ikisine de yazmak gerekiyor.
if (typeof window !== 'undefined' && (window as unknown) !== globalThis) {
  install(window)
}

// Also at load time: some tests compute labels with `cevir()` at module scope,
// before any beforeEach runs, and must agree with what the component renders.
aktifDilAyarla('tr')

// Testler arası sızıntıyı kapat: kalıcı KULLANICI TERCİHLERİ burada tutuluyor
// (`generationMode`, dil), yani bir testin yazdığı anahtar bir sonrakinin
// başlangıç varsayımını sessizce değiştirebilir.
beforeEach(() => {
  globalThis.localStorage?.clear()
  globalThis.sessionStorage?.clear()
  // Components rendered outside the app's provider used to fall back to
  // Turkish, and the suite's copy assertions were written against that. The
  // product default became English (2 Oct 2026); the out-of-provider fallback
  // is pinned here so those renders keep their language, while `home.tsx`
  // still sets its own language exactly as in the app.
  aktifDilAyarla('tr')
})
