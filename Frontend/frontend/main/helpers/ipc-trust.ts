import fs from 'fs'
import path from 'path'
import { app, dialog, BrowserWindow, IpcMainInvokeEvent } from 'electron'
import { isUnityProjectDir } from './unity-project'

/**
 * IPC güven katmanı — 2026-07-27 denetiminin D grubu bulguları.
 *
 * Denetimde ölçülen sorun tek bir kökten çıkıyordu: main süreç HER IPC
 * çağıranını meşru arayüz sayıyordu. Somut sonuçları:
 *
 *   - `app-token-get` backend bearer'ını soran her belgeye veriyordu
 *   - `terminal-spawn` + `terminal-write` kullanıcının kabuğunu açıp içine
 *     keyfi byte yazıyordu; gönderen, cwd ya da onay hiç kontrol edilmiyordu
 *   - Dosya uçlarında sınırın KENDİSİ (`workspacePath`) çağırandan geliyordu;
 *     saldırgan `read-file($HOME/.codex/auth.json, $HOME)` diyerek kimlik
 *     bilgisi okuyabiliyordu — hapis, hapsedilenin seçtiği bir hapisti
 *
 * İki bağımsız savunma var, çünkü tek başına ikisi de yetmiyor:
 *
 *   1. KAYNAK KAPISI (`isOwnFrame`): çağrı bizim sayfamızdan mı geliyor?
 *      Uzak bir belgenin renderer'a girmesini gezinme politikası zaten
 *      engelliyor (bkz. create-window.ts); bu, o politikanın delindiği
 *      durumda ikinci kilit.
 *   2. YETKİLİ KÖK KÜTÜĞÜ (`isTrustedRoot`): workspace kökü yalnızca
 *      kullanıcının NATIVE diyalogla seçtiği bir yol olabilir. Renderer ele
 *      geçse bile daha geniş bir kök uyduramaz.
 */

const isProd = process.env.NODE_ENV === 'production'

// Nextron dev'de pencereyi `electron . <port>` diye başlatır. Prod'da argv[2]
// port değil (OS'un geçirdiği herhangi bir argüman olabilir), o yüzden yalnızca
// sayıysa ve yalnızca dev'de kabul ediliyor.
const devPort = /^\d+$/.test(process.argv[2] || '') ? process.argv[2] : null

/**
 * Uygulamanın kendi origin'i: prod'da electron-serve'ün `app://./home` adresi
 * (host '.'), dev'de Next dev sunucusu.
 *
 * Neden `url.origin` ile karşılaştırılmıyor: `app:` non-special bir şema ve
 * `new URL('app://./home').origin` JavaScript'te "null" STRING'ini döndürüyor.
 * Naif bir origin eşitliği bu yüzden her `app:` URL'ini birbirine — ve saldırgan
 * bir `app://evil/` adresini bize — eşit sayardı. Protokol ve host ayrı bakılıyor.
 */
export const isOwnOrigin = (rawUrl: string): boolean => {
  let url: URL
  try {
    url = new URL(rawUrl)
  } catch {
    return false
  }

  if (isProd) {
    return url.protocol === 'app:' && (url.host === '.' || url.host === '')
  }

  if (url.protocol !== 'http:') return false
  if (url.hostname !== 'localhost' && url.hostname !== '127.0.0.1') return false
  // Port bilinmiyorsa (argv[2] verilmeden elle başlatılan dev oturumu) loopback'in
  // tamamına izin ver; aksi halde HMR reload'ları engellenir. Prod'a sızmaz çünkü
  // bu dal yalnızca dev'de çalışıyor.
  return devPort === null || url.port === devPort
}

/**
 * IPC çağrısı bizim sayfamızdan mı geldi?
 *
 * `senderFrame` iframe dahil gerçek gönderen çerçeveyi verir; `sender.getURL()`
 * üst çerçeveyi döndürdüğü için gömülü bir üçüncü-taraf çerçeve onunla ayırt
 * edilemezdi. Çerçeve yoksa (destroyed) reddediyoruz — bilinmeyen gönderen,
 * güvenilmeyen gönderendir.
 */
export const isOwnFrame = (event: IpcMainInvokeEvent): boolean => {
  const frameUrl = event.senderFrame?.url
  if (!frameUrl) return false
  return isOwnOrigin(frameUrl)
}

// ── Yetkili workspace kökleri ────────────────────────────────────────────────

/**
 * Kütük neden KALICI: kullanıcı klasörü bir kez seçiyor, sonraki açılışlarda
 * workspace backend'in "son workspace" kaydından geri yükleniyor ve o akış
 * native diyalogdan geçmiyor. Kütük diske yazılmasaydı her yeniden başlatmada
 * dosya ağacı ölürdü — yani güvenlik düzeltmesi ürünü kırardı.
 */
const registryPath = () => path.join(app.getPath('userData'), 'trusted-workspaces.json')

let trustedRoots: string[] | null = null

const normalize = (p: string): string | null => {
  try {
    if (!p) return null
    const abs = path.resolve(p)
    // Symlink'i çöz: aksi halde workspace'in içine kurulan bir bağ, kütükteki
    // kök ile karşılaştırmayı geçip başka bir ağaca işaret edebilirdi.
    return fs.existsSync(abs) ? fs.realpathSync(abs) : abs
  } catch {
    return null
  }
}

const load = (): string[] => {
  if (trustedRoots) return trustedRoots
  try {
    const raw = fs.readFileSync(registryPath(), 'utf-8')
    const parsed = JSON.parse(raw)
    trustedRoots = Array.isArray(parsed) ? parsed.filter((x) => typeof x === 'string') : []
  } catch {
    trustedRoots = []
  }
  return trustedRoots
}

const persist = () => {
  try {
    fs.writeFileSync(registryPath(), JSON.stringify(trustedRoots ?? [], null, 2), { mode: 0o600 })
  } catch (err: any) {
    console.error('[ipc-trust] kütük yazılamadı:', err?.message || err)
  }
}

/**
 * Bir kökü yetkili listesine ekler. YALNIZCA native klasör diyaloğunun sonucu
 * ve kullanıcının uygulamayı ilk kez bu sürümle açtığındaki tek seferlik devir
 * (bkz. confirmLegacyRoot) buraya girer — renderer'dan gelen bir yol asla.
 */
export const registerTrustedRoot = (rawPath: string): void => {
  const root = normalize(rawPath)
  if (!root) return
  const roots = load()
  if (!roots.includes(root)) {
    roots.push(root)
    persist()
    console.log('[ipc-trust] yetkili workspace eklendi:', root)
  }
}

/** Yol, yetkili köklerden birinin kendisi mi ya da altında mı? */
export const isTrustedRoot = (rawPath: string): boolean => {
  const target = normalize(rawPath)
  if (!target) return false
  return load().some((root) => target === root || target.startsWith(root + path.sep))
}

/**
 * Bu sürümden ÖNCE seçilmiş bir workspace kütükte yok; kullanıcı hiçbir şey
 * yapmadığı hâlde dosya ağacı ölürdü. Migrasyon o yüzden gerekli — ama BEDAVA
 * değil.
 *
 * Önceki hâli "kütük boşsa ilk geçerli dizini sessizce devral" idi ve bir
 * denetim bunu haklı olarak yüksek önemli bir bulgu saydı: kütüğü boşaltabilen
 * ya da boş olduğu anı yakalayan bir çağıran, KENDİ seçtiği kökü yetkili
 * yaptırabiliyordu. "Pencere dar" demek "pencere yok" demek değil, ve o kaydı
 * bir yorumla gerekçelendirmiş olmam kapattığım anlamına gelmiyordu.
 *
 * Artık karar kullanıcıya soruluyor — native bir modal ile. Bu kasıtlı: native
 * diyalogu renderer süremez, yani çağıran ne kadar ele geçmiş olursa olsun
 * cevabı veren gerçek kullanıcıdır. Bedeli, sürüm yükseltmesinden sonraki İLK
 * açılışta tek bir onay; ürünün "0 sürtünme" sözünü bir kerelik bir kutu ile
 * ödüyoruz, sessiz bir güven varsayımıyla değil.
 */
export const confirmLegacyRoot = (rawPath: string): boolean => {
  if (load().length > 0) return false
  const root = normalize(rawPath)
  if (!root) return false
  try {
    if (!fs.statSync(root).isDirectory()) return false
  } catch {
    return false
  }

  const approved = askAuthorize(
    root,
    'Uygulama bu klasörün içindeki dosyaları okuyup yazabilecek. ' +
      'Bu soruyu bir kez görüyorsun: daha önce seçtiğin çalışma klasörü ' +
      'yeni güvenlik kaydında yok. Emin değilsen İptal de ve klasörü ' +
      'uygulama içinden yeniden seç.',
  )
  if (!approved) return false
  registerTrustedRoot(root)
  return true
}

/**
 * Native "Yetkilendir / İptal" kutusu. Senkron modal: karar verilmeden hiçbir
 * dosya işlemi ilerlemesin; renderer bu kutuyu süremez.
 */
const askAuthorize = (root: string, explanation: string, parent?: BrowserWindow): boolean => {
  const options = {
    type: 'question' as const,
    buttons: ['Yetkilendir', 'İptal'],
    defaultId: 1,
    cancelId: 1,
    title: 'Çalışma klasörünü yetkilendir',
    message: 'Bu klasöre erişim izni verilsin mi?',
    detail: `${root}\n\n${explanation}`,
  }
  const response = dialog.showMessageBoxSync
    ? (parent ? dialog.showMessageBoxSync(parent, options) : dialog.showMessageBoxSync(options))
    : 1
  if (response !== 0) {
    console.warn('[ipc-trust] kullanıcı kök yetkilendirmesini reddetti:', root)
    return false
  }
  return true
}

/**
 * Kütük doluyken, son projeler listesinden tıklanan yetkisiz bir Unity projesi
 * için tek tıkla native onay. Yalnızca var olan bir Unity proje klasörü sorulur;
 * onay yoksa hiçbir şey kaydedilmez.
 */
export function confirmRecentRoot(rawPath: string, parent?: BrowserWindow): boolean {
  if (isTrustedRoot(rawPath)) return true
  const root = normalize(rawPath)
  if (!root || !isUnityProjectDir(root)) return false
  if (!askAuthorize(root, 'Uygulama bu klasörün içindeki dosyaları okuyup yazabilecek.', parent)) return false
  // The modal can stay open for minutes: register only if the path still resolves to the
  // project the user saw, not to whatever was swapped in meanwhile.
  if (normalize(rawPath) !== root || normalize(root) !== root || !isUnityProjectDir(root)) return false
  registerTrustedRoot(root)
  return true
}
