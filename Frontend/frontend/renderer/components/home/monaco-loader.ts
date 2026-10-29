import { loader } from '@monaco-editor/react';
import { withBase } from '../../lib/basePath';

/**
 * Monaco'yu CDN'den DEĞİL, uygulamanın kendi origin'inden yükletir.
 *
 * Yapılandırılmadığında `@monaco-editor/loader` gömülü varsayılanına düşüyor ve
 * editörün tüm kodunu `https://cdn.jsdelivr.net/npm/monaco-editor@.../min/vs/`
 * adresinden çekiyordu. İki somut bedeli vardı:
 *   (a) `window.ipc`'ye erişebilen renderer bağlamında, hash pinlemesi olmayan
 *       uzak JavaScript çalışıyordu — tedarik zinciri yüzeyi;
 *   (b) editör çevrimdışı/kısıtlı ağda HİÇ açılmıyordu.
 *
 * Dosyalar `scripts/copy-monaco.js` ile node_modules'tan `renderer/public/monaco/`
 * altına kopyalanıyor (predev/prebuild). Next `public/` içeriğini export kökine
 * koyduğu için üretimde `app/monaco/`, dev'de `/monaco/` altından servis ediliyor.
 *
 * `loader` NEDEN `@monaco-editor/react`'ten import ediliyor: yapılandırma
 * loader paketinin modül düzeyindeki TEK bir state nesnesinde tutuluyor.
 * `@monaco-editor/loader` doğrudan import edilseydi ve node_modules'ta ikinci bir
 * kopya bulunsaydı, config bir kopyaya yazılıp init diğerinden koşabilirdi —
 * yani sessizce yine CDN'e düşerdik. React paketinin re-export'u aynı örneği
 * kullandığımızı garantiler.
 */

/**
 * ÖLÇÜLDÜ (Electron 34, electron-serve kurulumunun birebir kopyası olan bir
 * sondayla) — bu değer VARSAYILMADI:
 *
 *   location.href                                  → "app://./home"
 *   location.origin                                → "app://."
 *   new URL('/monaco/vs/loader.js', location.href) → "app://./monaco/vs/loader.js"
 *   <script src="/monaco/vs/loader.js">            → yüklendi ve çalıştı
 *
 * Yani kök-göreli `/…` yolu `app://` şemasında host'u ('.') KORUYOR;
 * `app:///…` diye bozulmuyor. (Şema `standard: true` ile kayıtlı olduğu için.)
 *
 * Neden mutlak, neden göreli değil: prod'da belge URL'i `app://./home`
 * (sonda `/` YOK) ama dev'de Next `trailingSlash: true` yüzünden `/home/`e
 * yönlendiriyor. Göreli bir `monaco/vs` prod'da `/monaco/vs`e, dev'de
 * `/home/monaco/vs`e çözülürdü — yani dev'de 404. Kök-göreli değer iki modda da
 * aynı yere düşüyor.
 *
 * Neden tam URL (`app://./monaco/vs`) değil: Monaco'nun kendi AMD loader'ı yolu
 * `isAbsolutePath = /^((http:\/\/)|(https:\/\/)|(file:\/\/)|(\/))/` ile sınıyor
 * (node_modules/monaco-editor/min/vs/loader.js). `app://` bu regex'e UYMUYOR;
 * mutlak sayılmayan bir yola `baseUrl` ekleniyor. Ayrıca tam URL dev'de
 * (http://localhost:PORT) yanlış olurdu. `/` ile başlayan değer ise hem regex'i
 * geçiyor hem de iki modda da doğru.
 */
export const MONACO_VS_PATH = withBase('/monaco/vs');

loader.config({ paths: { vs: MONACO_VS_PATH } });
