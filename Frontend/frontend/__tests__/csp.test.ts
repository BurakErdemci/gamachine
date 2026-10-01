import { describe, it, expect, vi } from 'vitest'

// csp.ts modül tepesinde electron'u import ediyor; jsdom ortamında böyle bir
// modül yok. Politika üretimi saf bir fonksiyon olduğu için mock yeterli.
vi.mock('electron', () => ({
  app: { on: vi.fn() },
  session: { defaultSession: { webRequest: { onHeadersReceived: vi.fn() } } },
}))

import { buildPolicy } from '../main/helpers/csp'

const directive = (policy: string, name: string): string => {
  const found = policy
    .split(';')
    .map((part) => part.trim())
    .find((part) => part === name || part.startsWith(`${name} `))
  if (!found) throw new Error(`'${name}' direktifi politikada yok: ${policy}`)
  return found
}

/**
 * Bu blok İKİ YÖNÜ birden bağlıyor ve sebebi yaşanmış bir arıza:
 * eski testlerin hepsi kapının fazla DAR olmadığını sınıyordu, hiçbiri fazla
 * GENİŞ olmadığını sınamıyordu — ve ürünü kıran/riske atan yön oydu.
 *
 *   DAR yön  = politikada gereksiz bir uzak kaynak KALMASIN (güvenlik regresyonu)
 *   GENİŞ yön = Monaco'nun ÇALIŞMASI için gereken izinler DURSUN (ürün regresyonu)
 *
 * Aşağıdaki "gerekli" iddiaların hepsi 2026-07-27'de canlı ölçüldü: prod
 * (`app://./home`) build'i yüklenip politika tek tek daraltıldı ve hem
 * `securitypolicyviolation` olayları hem de Monaco dil worker'ının canlılığı
 * (bozuk JSON'a marker düşüyor mu) izlendi.
 */
describe('CSP — Monaco yerelden servis ediliyor', () => {
  const prod = buildPolicy(true)
  const dev = buildPolicy(false)

  // ── DAR YÖN ───────────────────────────────────────────────────────────────

  it('hiçbir dalda uzak SCRIPT kaynağı yoktur', () => {
    // Asıl kazanç bu: `window.ipc`yi gören bağlamda uzaktan JS çalışmıyor.
    // Monaco eskiden cdn.jsdelivr.net'ten geliyordu; artık app://./monaco/vs.
    for (const [name, policy] of [['prod', prod], ['dev', dev]] as const) {
      const scriptSrc = directive(policy, 'script-src')
      expect(scriptSrc, `${name}: script-src'de uzak kaynak var`).not.toMatch(/https?:\/\//)
    }
  })

  it('has no remote style or font origin in either policy', () => {
    // Bundled fonts and Monaco styles must load without remote origins.
    for (const [name, policy] of [['prod', prod], ['dev', dev]] as const) {
      expect(directive(policy, 'style-src'), `${name}: style origins`).toBe("style-src 'self' 'unsafe-inline'")
      expect(directive(policy, 'font-src'), `${name}: font origins`).toBe("font-src 'self' data:")
    }
  })

  it('politikanın hiçbir yerinde jsdelivr geçmiyor', () => {
    // Bu satır kırılırsa: monaco yeniden CDN'e bağlanmış demektir. Doğru
    // düzeltme izni geri eklemek DEĞİL, scripts/copy-monaco.js + monaco-loader.ts
    // zincirinin neden koşmadığını bulmaktır.
    expect(prod).not.toContain('jsdelivr')
    expect(dev).not.toContain('jsdelivr')
  })

  // ── GENİŞ YÖN ─────────────────────────────────────────────────────────────

  it("worker-src'de blob: DURUYOR — yerelleştirme bu ihtiyacı kaldırmadı", () => {
    // Beklenti "same-origin olunca blob gerekmez" idi; YANLIŞ çıktı.
    // monaco-editor 0.55.1 AMD paketi worker'ı koşulsuz blob'la kuruyor
    // (editor.main.js: getWorker → new Worker(URL.createObjectURL(new Blob([
    //  "…importScripts(<workerUrl>)…"])))) — origin karşılaştırması yok.
    // Ölçüm, blob: çıkarılınca:
    //   "Refused to create a worker from 'blob:app://./…' … worker-src 'self'"
    //   + json worker hiç boot etmedi (12sn boyunca marker gelmedi)
    // AMA editör DOM'u yine kuruldu ve C# tokenizasyonu çalıştı — yani gözle
    // bakan biri bu regresyonu göremez. Kapı bu yüzden burada.
    expect(directive(prod, 'worker-src')).toContain('blob:')
    expect(directive(dev, 'worker-src')).toContain('blob:')
  })

  it("script-src ve style-src'de 'self' DURUYOR — monaco artık oradan geliyor", () => {
    // Monaco'nun 14 dosyası app://./monaco/vs altından çekiliyor (ölçüldü);
    // editor.main.css de aynı origin'den <link> ile geliyor. 'self' düşerse
    // editör hiç açılmaz.
    expect(directive(prod, 'script-src')).toContain("'self'")
    expect(directive(prod, 'style-src')).toContain("'self'")
  })
})

describe('CSP — prod politikası', () => {
  const policy = buildPolicy(true)

  it("script-src'de 'unsafe-eval' ya da 'unsafe-inline' YOKTUR", () => {
    // Dev gevşekliğinin prod'a sızması, politikanın kod-yükleme tarafını
    // tümüyle anlamsızlaştırır. Kapı burada.
    const scriptSrc = directive(policy, 'script-src')
    expect(scriptSrc).not.toContain("'unsafe-eval'")
    expect(scriptSrc).not.toContain("'unsafe-inline'")
  })

  it('websocket kaynağı içermez', () => {
    expect(policy).not.toContain('ws://')
    expect(policy).not.toContain('wss://')
  })

  it('ölçülmüş zorunlu kaynakları içerir', () => {
    // Monaco artık same-origin: script-src 'self' onu da kapsıyor.
    expect(directive(policy, 'script-src')).toContain("'self'")
    // Monaco dil worker'ı blob: üzerinden kuruluyor.
    expect(directive(policy, 'worker-src')).toContain('blob:')
    // Monaco codicon ikon fontu bir data: URI.
    expect(directive(policy, 'font-src')).toContain('data:')
    // Sohbet ekleri FileReader.readAsDataURL ile data: URI olarak render ediliyor.
    expect(directive(policy, 'img-src')).toContain('data:')
    // Backend dinamik portta dinliyor.
    expect(directive(policy, 'connect-src')).toContain('http://127.0.0.1:*')
  })

  it('tehlikeli yüzeyleri kapatır', () => {
    expect(directive(policy, 'object-src')).toBe("object-src 'none'")
    expect(directive(policy, 'base-uri')).toBe("base-uri 'none'")
    expect(directive(policy, 'frame-src')).toBe("frame-src 'none'")
    expect(directive(policy, 'frame-ancestors')).toBe("frame-ancestors 'none'")
    // 'none' değil 'self': giriş formu preventDefault çağırmıyor, gerçek bir
    // form gezinmesi olabiliyor. Asıl tehdit uzak endpoint'e POST — o kapalı.
    expect(directive(policy, 'form-action')).toBe("form-action 'self'")
  })

  it('uzak origin izinleri yalnızca ölçülmüş olanlardır', () => {
    // Monaco jsdelivr'dan çıktıktan sonra listede yalnızca font/görsel kaldı;
    // hiçbiri KOD kaynağı değil (script-src'de tek bir uzak origin yok).
    const remoteHosts = policy.match(/https:\/\/[^\s;]+/g) ?? []
    const allowed = new Set([
      'https://fonts.googleapis.com',
      'https://fonts.gstatic.com',
      'https://images.unsplash.com',
    ])
    for (const host of remoteHosts) {
      expect(allowed.has(host), `politikada beklenmeyen uzak kaynak: ${host}`).toBe(true)
    }
  })
})

describe('CSP — dev politikası', () => {
  const policy = buildPolicy(false)

  it('HMR için eval, satır içi script ve websocket izni verir', () => {
    const scriptSrc = directive(policy, 'script-src')
    expect(scriptSrc).toContain("'unsafe-eval'")
    expect(scriptSrc).toContain("'unsafe-inline'")
    const connectSrc = directive(policy, 'connect-src')
    expect(connectSrc).toContain('ws://127.0.0.1:*')
    expect(connectSrc).toContain('ws://localhost:*')
  })

  it('gevşeme yalnızca script-src ve connect-src ile sınırlıdır', () => {
    // Dev ile prod arasındaki farkın kapsamı bilinçli olarak dar; başka bir
    // direktifin dallanması fark edilmeden politikayı aşındırırdı.
    const prod = buildPolicy(true)
    const asMap = (p: string) =>
      Object.fromEntries(
        p.split(';').map((part) => {
          const [name, ...rest] = part.trim().split(' ')
          return [name, rest.join(' ')]
        })
      )
    const devMap = asMap(policy)
    const prodMap = asMap(prod)
    const differing = Object.keys(prodMap).filter((k) => devMap[k] !== prodMap[k])
    expect(differing.sort()).toEqual(['connect-src', 'script-src'])
  })
})
