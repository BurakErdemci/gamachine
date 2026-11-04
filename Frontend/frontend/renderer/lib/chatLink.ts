import { defaultUrlTransform } from 'react-markdown';

/**
 * Sohbet balonundaki markdown linklerinin sınıflandırılması ve URL dönüşümü.
 *
 * ⚠️ Neden var (ölçülmüş arıza, kullanıcı bildirimi 2 Ağu 2026): asistan bir
 * dosya oluşturup link veriyordu; tıklanınca uygulama "sanki yeni açılmış gibi"
 * sıfırlanıyor, dosya açılmıyordu.
 *
 * ⭐ Bu bir ÇÖKME DEĞİL, NAVİGASYON'du. Ayrımı yapmak pahalıya mal oldu: React
 * ağacı hatasız yok olduğu için ne hata sınırı devreye giriyor ne de render
 * yolunda fırlatan bir nokta bulunabiliyordu. Bir arızayı yanlış sınıfa koymak,
 * onu aramayı imkânsız hale getiriyor.
 */

/**
 * ⚠️ İKİNCİ ÖLÇÜM, İLK DÜZELTMEYİ ÇÜRÜTTÜ.
 *
 * İlk düzeltmede modelin GÖRELİ yol ürettiğini varsaymıştım
 * (`[a.txt](a.txt)`). Veritabanındaki ham mesaj bunu yanlışladı — model
 * MUTLAK Windows yolu üretmiş, üstelik boşluklu ve açılı parantezli:
 *
 *     [test-dosyasi.txt](<C:\Unity Projeler\Körebe\test-dosyasi.txt>)
 *
 * Ve `react-markdown@10`'un `defaultUrlTransform`'u `C:`'yi bir PROTOKOL
 * sayıyor (ilk iki nokta protokol kabul ediliyor, dizgede ileri bölü yok,
 * güvenli şema listesi `https?|ircs?|mailto|xmpp`) → dönüş `''`. Bu dönüşüm
 * `a` bileşeni çağrılmadan ÖNCE koşuyor, yani override'a boş dizge ulaşıyordu
 * ve link `<a href="">` olarak çiziliyordu. Boş `href` tarayıcıda MEVCUT
 * dokümana çözülür → tıklama tam sayfa navigasyonu → SPA unload.
 *
 * ⭐ Ders: bir override'ın çağrıldığını doğrulamak, ona DOĞRU VERİNİN
 * ulaştığını doğrulamak değildir.
 *
 * Bu sarmalayıcı yalnız sürücü harfli yolları ve `file:` şemasını geçiriyor;
 * geri kalan HER ŞEY `defaultUrlTransform`'a gidiyor. Güvenlik davranışını
 * (özellikle `javascript:` gibi şemaların silinmesi) yeniden YAZMIYORUZ —
 * kopyalanan bir güvenlik kararı bu depoda adı konmuş bir arıza sınıfı.
 */
/**
 * ⚠️ ÜÇÜNCÜ ÖLÇÜM — ikinci düzeltme de eksikti.
 *
 * `urlTransform`'a gelen değer HAM DEĞİL, **yüzde-kodlanmış**: mdast-util-to-hast
 * dönüşümden ÖNCE kodluyor. Ölçüldü (vitest, kurulu paketler):
 *
 *     girdi:  C:\Unity Projeler\Körebe\test-dosyasi.txt
 *     gelen:  C:%5CUnity%20Projeler%5CK%C3%B6rebe%5Ctest-dosyasi.txt
 *
 * Yani `^[a-zA-Z]:[\\/]` deseni eşleşmiyordu ve yol yine siliniyordu. Desen
 * artık kodlanmış ayırıcıyı da tanıyor.
 *
 * ⭐ Ders: bir dizgenin "ham" geldiğini VARSAYMAK, biçimini ölçmemektir. Aynı
 * arıza üç turda üç farklı katmanda çıktı — göreli/mutlak, silinen href,
 * kodlanmış ayırıcı — ve üçünde de varsayım ölçümle çürüdü.
 */
const SURUCU_YOLU = /^[a-zA-Z]:(%5[cC]|%2[fF]|[\\/])/;
const UNC_YOLU = /^(\\\\|%5[cC]%5[cC])/;

export function chatUrlTransform(url: string): string {
  // `gamachine-file:<id>`: an image the agent showed through its show_image tool;
  // ChatFileImage fetches it by id. Any other custom scheme still goes to the default.
  if (/^gamachine-file:[A-Za-z0-9-]{8,64}$/.test(url)) return url;
  // `C:\...`, `C:/...`, `C:%5C...`, `\\sunucu\pay` — hepsi yerel yol, URL değil.
  if (SURUCU_YOLU.test(url) || UNC_YOLU.test(url)) return url;
  // `file:` de yerel: `linkTuru` onu yerel-dosya sayıyor, dolayısıyla burada
  // silmek ikisi arasında sessiz bir çelişki bırakırdı.
  if (/^file:/i.test(url)) return url;
  return defaultUrlTransform(url);
}

/**
 * Yüzde-kodlanmış bir `href`'i diskte kullanılabilir yola çevirir.
 *
 * ⚠️ Bu adım ATLANIRSA link "çalışıyor" görünür ama dosya AÇILMAZ:
 * `openFile` yolu doğrudan `read-file` IPC'sine veriyor ve
 * `C:%5CUnity%20Projeler%5C...` diskte yok. Yani navigasyonu durdurup yanlış
 * yol göndermek, arızayı "sıfırlanma"dan "hiçbir şey olmuyor"a taşımaktan
 * ibaret olurdu — kullanıcı için ikisi de bozuk.
 *
 * Bozuk bir kodlamada `decodeURIComponent` fırlatıyor; o durumda ham dizge
 * dönüyor — açılamazsa `openFile` zaten gerekçeli uyarı veriyor.
 */
export function yerelYolaCevir(href: string): string {
  let ham = href;

  // ⚠️ `file:` URL'i bir YOL DEĞİL (dış denetim bulgusu,
  // `renderer-gate-path-shape-mismatch`). `chatUrlTransform` onu bilerek
  // geçiriyordu ama ana süreç yol dizgesi bekliyor: `path.isAbsolute('file:///C:/a')`
  // Windows'ta `false`, dolayısıyla eski hâlde ürün onu workspace ALTINA
  // ekleyip `C:\ws\file:\C:\a` gibi var olmayan bir yol kuruyordu. Yani
  // "destekliyoruz" görünen bir dal hiç çalışmıyordu — sessiz bir yalan.
  if (/^file:/i.test(ham)) {
    try {
      const u = new URL(ham);
      // `/C:/a/b.txt` → `C:/a/b.txt`; POSIX'te baştaki `/` korunuyor.
      const p = decodeURIComponent(u.pathname);
      // ⚠️ Host ATILMIYOR (doğrulama turu bulgusu `file-url-host-silently-dropped`).
      // `file://sunucu/pay/x.txt` bir UNC yolunu adlandırıyor; yalnız `pathname`
      // alınırsa `/pay/x.txt` kalıyor ve bu Windows'ta BAŞKA bir dosyayı
      // (`C:\pay\x.txt`) gösteriyor. Kapı onu zaten workspace dışı diye
      // reddediyor, yani güvenlik sorunu değil — ama yine "destekleniyor görünüp
      // hiç çalışmayan dal" biçimi, kapatmaya çalıştığımız sınıfın ta kendisi.
      if (u.host) return `\\\\${u.host}${p.replace(/\//g, '\\')}`;
      ham = /^\/[a-zA-Z]:/.test(p) ? p.slice(1) : p;
      return ham;
    } catch {
      // Ayrıştırılamayan bir `file:` — aşağıdaki genel yola düşsün.
    }
  }

  try {
    return decodeURIComponent(ham);
  } catch {
    return ham;
  }
}

export type LinkTuru = 'dis' | 'yerel-dosya' | 'capa' | 'bos';

/**
 * `dis`         → tarayıcıda açılacak (http/https/mailto). Bunlara DOKUNMUYORUZ:
 *                 `will-navigate` onları zaten `preventDefault` +
 *                 `shell.openExternal`a yönlendiriyor.
 * `capa`        → `#bolum`. Sayfa içi çapa meşru ve navigasyona yol açmıyor.
 * `bos`         → `href` boş. ⚠️ Bu `capa` DEĞİL: boş href mevcut dokümana
 *                 çözülür, yani tıklama sayfayı YENİDEN YÜKLER. Arızanın son
 *                 hâli tam olarak buydu. Engelleniyor ama açılacak dosya da yok.
 * `yerel-dosya` → geri kalan HER ŞEY: göreli yol, `./`, `../`, `file:`,
 *                 POSIX mutlak yol, Windows sürücü yolu, UNC.
 *
 * Ölçüt neden "dış olanı say, gerisi yerel"? Ters kurgu (yerel biçimleri
 * saymak) her yeni yol biçiminde sessizce açılırdı — ve bu tam olarak yaşandı:
 * ilk sürüm göreli yolu düşünüp mutlak sürücü yolunu kaçırdı. Dış şemaların
 * kümesi ise SONLU ve biliniyor.
 */
export function linkTuru(href: string | undefined | null): LinkTuru {
  const h = (href ?? '').trim();
  if (!h) return 'bos';
  if (h.startsWith('#')) return 'capa';
  // Şema karşılaştırması küçük harfe indirgenerek: `HTTPS://` de dış link.
  if (/^(https?|mailto):/i.test(h)) return 'dis';
  return 'yerel-dosya';
}
