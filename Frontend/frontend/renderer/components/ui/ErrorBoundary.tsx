import React from 'react';
import { AlertTriangle } from 'lucide-react';

import { aktifDil, ceviriUygula } from '../../lib/i18n';

/**
 * Render sırasındaki bir hatayı yakalayıp BEYAZ EKRAN yerine okunabilir bir
 * kart gösterir.
 *
 * ⚠️ Neden var (ölçülmüş arıza, kullanıcı bildirimi 2 Ağu 2026): dosya
 * ağacındaki bir dosyaya tıklandığında pencere bomboş kalıyor ve uygulamayı
 * KAPATIP AÇMAK gerekiyordu. Sebep React'in varsayılan davranışı: yakalanmayan
 * bir render hatasında tüm ağaç unmount ediliyor ve geriye boş bir kök kalıyor.
 * Ölçüm: bu dosyadan önce depoda `componentDidCatch` /
 * `getDerivedStateFromError` SIFIR eşleşme veriyordu.
 *
 * Bu bileşen iki iş yapıyor ve ikisi de ayrı ayrı gerekli:
 *   1. Kurtarma — kullanıcı uygulamayı kapatmadan devam edebiliyor.
 *   2. TEŞHİS — hatanın mesajı ve yığını EKRANDA. Beyaz ekranın en pahalı
 *      yanı bilgiyi yok etmesiydi: kullanıcı "bozuldu" diyebiliyordu ama
 *      NEYİN bozulduğunu kimse göremiyordu. Kart bu yüzden hatayı gizlemiyor.
 *
 * ⚠️ i18n hook'la DEĞİL sözlükten okunuyor. İki ölçülmüş kısıt:
 *   • Hata sınırı olabilmek için class component olmak zorunda (React yalnız
 *     class API'sinde `getDerivedStateFromError` sunuyor) ve class'ta hook
 *     çağrılamaz.
 *   • The language provider is inside `home.tsx`, below this boundary, so
 *     context alone would always return its default. `aktifDil()` shares
 *     the page's announced language and otherwise reads `app-lang`, falling
 *     back to English ('en'). A separate reader would create a second source
 *     of truth that could disagree with the page.
 */

interface ErrorBoundaryProps {
  children: React.ReactNode;
  /** Testte enjekte edilebilsin diye ayrı: varsayılan `location.reload()`. */
  onReload?: () => void;
}

interface ErrorBoundaryState {
  error: Error | null;
  stack: string | null;
  kopyalandi: boolean;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null, stack: null, kopyalandi: false };

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { error, kopyalandi: false };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Bileşen yığını, hatanın kendi yığınından daha kullanışlı: hangi
    // bileşende patladığını söylüyor. İkisini de saklıyoruz.
    this.setState({ stack: info.componentStack ?? null });
    console.error('[ErrorBoundary] render hatası yakalandı:', error, info.componentStack);
  }

  /**
   * ⚠️ HER ERİŞİM AYRI `try` İÇİNDE — dış denetim bulgusu (`fallback-self-failure`).
   *
   * `error.message` ve `error.stack` sıradan veri alanları OLMAK ZORUNDA DEĞİL:
   * fırlatılan değer bir Proxy ya da getter'ı patlayan bir nesne olabilir.
   * Bu fonksiyon render yolunda çağrılıyor ve **bir sınır kendi render'ında
   * fırlayan hatayı YAKALAYAMAZ** — üstünde ikinci bir sınır da yok. Yani
   * korumasız tek bir özellik okuması, tam da bu bileşenin kapatmak için var
   * olduğu beyaz ekranı geri getiriyordu. Probe ile üretildi.
   */
  private guvenliOku(al: () => string | null | undefined): string {
    try {
      const v = al();
      return typeof v === 'string' ? v : '';
    } catch (e) {
      return `<okunamadı: ${(() => { try { return String(e); } catch { return 'bilinmeyen'; } })()}>`;
    }
  }

  private tamMetin(): string {
    const { error, stack } = this.state;
    const mesaj = this.guvenliOku(() => error?.message);
    const yigin = this.guvenliOku(() => error?.stack);
    return [mesaj, yigin, stack ? `\nBileşen yığını:${stack}` : '']
      .filter(Boolean)
      .join('\n');
  }

  private kopyala = () => {
    // ⚠️ `writeText` bir PROMISE döndürüyor; senkron `try/catch` onun reddini
    // GÖREMİYOR. Eski hâli reddi beklemeden "Kopyalandı" yazıyordu, yani pano
    // izni reddedildiğinde kullanıcıya olmamış bir işi olmuş gibi gösteriyordu
    // (dış denetim bulgusu, probe ile üretildi). Yalancı başarı, başarısızlıktan
    // kötü: kullanıcı panoda olmayan bir metni yapıştırmaya çalışır.
    try {
      const p = navigator.clipboard?.writeText(this.tamMetin());
      if (!p) return;
      p.then(
        () => this.setState({ kopyalandi: true }),
        () => this.setState({ kopyalandi: false }),
      );
    } catch {
      /* pano API'si hiç yok — metin ekranda seçilebilir, kayıp yok */
    }
  };

  private yenidenYukle = () => {
    if (this.props.onReload) {
      this.props.onReload();
      return;
    }
    location.reload();
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    // Reuse the page's announced language, then storage, then English ('en'),
    // so the fallback does not introduce a second language source.
    const t = (k: string) => ceviriUygula(aktifDil(), k);

    return (
      <div
        role="alert"
        className="h-screen bg-[#0B0D12] flex items-center justify-center p-6"
      >
        <div className="bg-[#0a0a0a] border border-slate-700 rounded-2xl shadow-2xl w-[520px] max-w-[92vw] p-5">
          <div className="flex items-start gap-3">
            <div className="shrink-0 w-9 h-9 rounded-full bg-red-500/15 border border-red-500/30 flex items-center justify-center">
              <AlertTriangle size={16} className="text-red-400" />
            </div>
            <div className="min-w-0">
              <h2 className="text-white text-[15px] font-bold mb-1">{t('error.title')}</h2>
              <p className="text-[13px] text-slate-300 leading-relaxed">{t('error.body')}</p>
            </div>
          </div>

          <div className="mt-4">
            <div className="text-[10px] font-mono text-slate-400 mb-1 uppercase tracking-wider">
              {t('error.detailsLabel')}
            </div>
            {/* ⚠️ Bu uyarı bir dış denetim bulgusundan geliyor
                (`diagnostic-data-exposure`): aşağıdaki metin ham yığın izi ve
                bu üründe renderer tarafında MASKELEME YOK (`secret_redaction`
                yalnız backend'de). Mutlak kullanıcı yolları kesin, ve bir
                kütüphane hatası araya oturum belirteci ya da istek gövdesi
                koyabilir. Metni gizlemiyoruz — teşhis değerinin tamamı orada ve
                beyaz ekranın en pahalı yanı bu bilgiyi yok etmesiydi. Ama
                paylaşmadan önce ne paylaştığını bilmek kullanıcının hakkı. */}
            <div className="text-[10px] text-amber-400/80 mb-1.5 leading-relaxed">
              {t('error.shareWarning')}
            </div>
            {/* Hata metni React'in text child'ı olarak giriyor — kaçış React'in
                kendisinde, ayrıca sanitize etmeye gerek yok. `whitespace-pre-wrap`
                yığının okunabilir kalması için; kart büyümesin diye kaydırılıyor. */}
            <pre className="max-h-[220px] overflow-auto custom-scrollbar text-[11px] text-red-300 bg-red-500/10 border border-red-500/40 rounded-lg px-3 py-2 whitespace-pre-wrap break-words font-mono m-0">
              {this.tamMetin()}
            </pre>
          </div>

          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              onClick={this.kopyala}
              className="px-3.5 py-1.5 rounded-lg text-[12px] font-medium text-slate-300 bg-slate-800/60 hover:bg-slate-700/60 transition-colors"
            >
              {this.state.kopyalandi ? t('error.copied') : t('error.copy')}
            </button>
            <button
              onClick={this.yenidenYukle}
              className="px-3.5 py-1.5 rounded-lg text-[12px] font-semibold text-white bg-red-600 hover:bg-red-500 transition-colors"
            >
              {t('error.reload')}
            </button>
          </div>
        </div>
      </div>
    );
  }
}
