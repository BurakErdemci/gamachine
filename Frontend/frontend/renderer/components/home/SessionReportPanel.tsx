import React, { useCallback, useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Layers, RefreshCw, X } from 'lucide-react';
import { useLang } from '../../lib/i18n';
import { SlashCommandCard } from './SlashCommandCard';
import type { ContextUsage } from './types';

type Durum =
  | 'yukleniyor' | 'ok' | 'estimate' | 'no_data'
  | 'no_session' | 'busy' | 'unsupported' | 'outdated' | 'error';

interface Rapor {
  durum: Durum;
  text?: string;
  /** On `ok`: the text came from the last successful reading, NOT a live one. */
  bayat?: boolean;
  /** Age of a stale reading, in seconds. Serving it without the age would pass it off as fresh. */
  yasS?: number;
  /** On `estimate`: context counted from the conversation's stored messages. */
  ctx?: ContextUsage & { total_chars?: number; max_chars?: number };
}

/** Seconds -> "12s" / "3m" / "2h". The age is the only honest label a stale report can carry. */
const yasMetni = (t: (k: any, p?: any) => string, s: number): string => {
  if (s < 60) return t('report.ageSeconds', { n: s });
  if (s < 3600) return t('report.ageMinutes', { n: Math.floor(s / 60) });
  return t('report.ageHours', { n: Math.floor(s / 3600) });
};

interface Props {
  open: boolean;
  onClose: () => void;
  API: string;
  sessionToken: string;
  convId: number | null;
  /** Gerçek bağlam metni geldiğinde haber ver — gösterge tahmin yerine bunu kullanır. */
  onContextText?: (text: string) => void;
}

/**
 * `/usage` ve `/context` raporlarını SOHBETE YAZMADAN gösteren panel.
 *
 * Bugüne kadar bu iki rapora ancak sohbete `/usage` yazarak bakılabiliyordu,
 * yani her bakış geçmişe bir mesaj çifti bırakıyordu ve akış sürerken
 * bakılamıyordu. Panel aynı kartları (`SlashCommandCard`) kullanıyor —
 * ayrı bir görünüm yazmak, aynı çıktının iki ayrı yorumunu doğururdu.
 *
 * Dört "veri yok" hâli AYRI AYRI yazılıyor: oturum yok · tur akıyor ·
 * bu sağlayıcıda yok · hata. Hepsini tek bir boş kutuya çevirmek, kullanıcıya
 * "bozuk" ile "henüz değil"i aynı şey gibi gösterirdi.
 */
export const SessionReportPanel: React.FC<Props> = ({
  open, onClose, API, sessionToken, convId, onContextText,
}) => {
  const { t, lang } = useLang();
  const numberLocale = lang === 'tr' ? 'tr-TR' : 'en-US';
  const [usage, setUsage] = useState<Rapor>({ durum: 'yukleniyor' });
  const [context, setContext] = useState<Rapor>({ durum: 'yukleniyor' });

  const getir = useCallback(async (kind: 'usage' | 'context'): Promise<Rapor> => {
    if (!API || !convId) return { durum: 'no_session' };
    try {
      const res = await fetch(`${API}/session-report/${convId}/${kind}`, {
        headers: { 'X-Session-Token': sessionToken },
      });
      // 404 = bu uç sunucuda YOK, yani çalışan arka uç bu sürümden eski.
      // "Hata" diye göstermek kullanıcıyı log okumaya yollardı; oysa yapılacak
      // şey belli ve tek: uygulamayı yeniden başlat. Ölçüldü 30 Ağu 2026 —
      // panel ilk denemede tam bu yüzden "Rapor alınamadı" dedi.
      if (res.status === 404) return { durum: 'outdated' };
      if (!res.ok) return { durum: 'error' };
      const d = await res.json();
      if (d.status === 'ok') {
        return { durum: 'ok', text: d.text || '', bayat: !!d.stale, yasS: d.age_s };
      }
      // No live reading, but the conversation's STORED messages yield a count.
      // A separate state: put in the same box as `ok`, an estimate would read as
      // a measurement.
      if (d.status === 'estimate') return { durum: 'estimate', ctx: d.context_usage };
      if (d.status === 'no_data') return { durum: 'no_data' };
      if (d.status === 'no_session' || d.status === 'busy' || d.status === 'unsupported') {
        return { durum: d.status };
      }
      return { durum: 'error' };
    } catch (err) {
      console.error('[session-report] request/parse failed', { kind, conversationId: convId }, err);
      return { durum: 'error' };
    }
  }, [API, convId, sessionToken]);

  /**
   * Uçuştaki çekimin KİMLİĞİ. Yalnız en son başlatılan çekim state'e yazabilir.
   *
   * Eskiden her `Promise.all` sonucu koşulsuz yazılıyordu. Ölçüldü 30 Ağu 2026:
   * A sohbeti için açılan panel kapatılıp B için yeniden açıldığında, A'nın geç
   * dönen yanıtı B'nin AÇIK panelini eziyordu — kullanıcı yanlış sohbetin
   * raporunu, doğru sanarak okuyordu. Bir raporun hangi sohbete ait olduğu
   * ekranda hiçbir yerde yazmadığı için de fark edilemezdi.
   *
   * Sayaç `convId` karşılaştırmasına tercih edildi: aynı sohbette arka arkaya
   * basılan "yenile" de aynı yarışı üretiyor ve kimlik testi orada `convId`
   * eşit olduğu için geçerdi.
   */
  const nesilRef = useRef(0);

  const tazele = useCallback(async () => {
    const nesil = ++nesilRef.current;
    setUsage({ durum: 'yukleniyor' });
    setContext({ durum: 'yukleniyor' });
    const [u, c] = await Promise.all([getir('usage'), getir('context')]);
    if (nesil !== nesilRef.current) return;
    setUsage(u);
    setContext(c);
    if (c.durum === 'ok' && c.text) onContextText?.(c.text);
  }, [getir, onContextText]);

  useEffect(() => { if (open) void tazele(); }, [open, tazele]);

  if (!open) return null;

  const bos = (r: Rapor) => {
    // `ok` BURADA açıkça sayılıyor. Bu dala yalnız metni boş olan BAŞARILI bir
    // yanıt düşüyor (HTTP 200 + `status: "ok"` + `text: ""`), ve o eskiden
    // sondaki `: 'report.error'` varsayılanına kayıyordu: sunucu doğru
    // çalışmışken kullanıcıya "Rapor alınamadı, ayrıntı loglarda" deniyordu —
    // yani okunacak bir log bile yoktu. Varsayılana düşmeye bırakılan her yeni
    // durum aynı yalanı üretir; onun için dal listelenerek yazılıyor.
    const anahtar = r.durum === 'yukleniyor' ? 'report.loading'
      : r.durum === 'ok' ? 'report.emptyOk'
      : r.durum === 'no_data' ? 'report.noData'
      : r.durum === 'no_session' ? 'report.noSession'
      : r.durum === 'busy' ? 'report.busy'
      : r.durum === 'unsupported' ? 'report.unsupported'
      : r.durum === 'outdated' ? 'report.outdated'
      : 'report.error';
    return <p data-testid={`report-empty-${r.durum}`} className="text-[12px] text-[color:var(--ink-faint)] px-1 py-2">{t(anahtar as any)}</p>;
  };

  /**
   * Context count derived from the stored conversation.
   *
   * When the live `/context` is unavailable (no session, a turn is streaming,
   * or Codex has no such command) the section used to stay EMPTY - in practice
   * the context section never showed data at all. The number here is not a
   * measurement and is not presented as one: the "estimate" badge and a note
   * saying what is not counted both live on the card itself.
   */
  const tahminKarti = (c: NonNullable<Rapor['ctx']>) => {
    const y = Math.max(0, Math.min(100, c.percent ?? 0));
    const renk = y >= 80 ? 'bg-[color:var(--accent)]' : y >= 50 ? 'bg-[color:var(--accent)]' : 'bg-[color:var(--ok)]';
    return (
      <div data-testid="report-context-estimate" className="bg-[color:var(--paper-bg)] rounded-xl border border-[color:var(--paper-line)] overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[color:var(--paper-line)]">
          <Layers size={14} className="text-[color:var(--ink-dim)]" />
          <span className="text-[12px] font-semibold uppercase tracking-wider text-[color:var(--ink-dim)]">
            {t('report.estimateTag')}
          </span>
        </div>
        <div className="px-4 py-3 space-y-2.5">
          <div>
            <div className="flex items-baseline justify-between mb-1">
              <span className="text-[12px] text-[color:var(--ink-dim)] font-medium">
                {t('report.estimateMeta', {
                  sayi: c.message_count ?? 0,
                  harf: (c.total_chars ?? 0).toLocaleString(numberLocale),
                })}
              </span>
              <span className="text-[12px] font-semibold tabular-nums text-[color:var(--ink-dim)]">~%{y}</span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-[color:var(--paper-sunk)] overflow-hidden">
              <div className={`h-full rounded-full ${renk} transition-all duration-500`} style={{ width: `${Math.max(y, 1)}%` }} />
            </div>
          </div>
          {c.last_turn && (typeof c.last_turn.input_tokens === 'number' || typeof c.last_turn.output_tokens === 'number') && (
            <p className="text-[12px] text-[color:var(--ink-dim)]">
              {t('report.estimateLastTurn', {
                giris: (c.last_turn.input_tokens ?? 0).toLocaleString(numberLocale),
                cikis: (c.last_turn.output_tokens ?? 0).toLocaleString(numberLocale),
              })}
            </p>
          )}
          <p className="text-[12px] text-[color:var(--ink-faint)] leading-relaxed">{t('report.estimateNote')}</p>
        </div>
      </div>
    );
  };

  const bolum = (baslik: string, kind: 'usage' | 'context', r: Rapor) => (
    <div className="space-y-1.5">
      <div className="text-[12px] uppercase tracking-wider text-[color:var(--ink-faint)] font-bold px-1">{baslik}</div>
      {r.durum === 'ok' && r.text ? (
        <>
          <SlashCommandCard command={kind} text={r.text} />
          {r.bayat && (
            // Serving a stale report without its age would pass it off as
            // fresh; quota windows roll and the user must know which moment
            // the numbers belong to.
            <p data-testid={`report-stale-${kind}`} className="text-[12px] text-[color:var(--ink-faint)] px-1">
              {t('report.stale', { sure: yasMetni(t, r.yasS ?? 0) })}
            </p>
          )}
        </>
      ) : r.durum === 'estimate' && r.ctx ? (
        tahminKarti(r.ctx)
      ) : (
        bos(r)
      )}
    </div>
  );

  return (
    <motion.div
      data-testid="session-report-panel"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="absolute bottom-full left-0 right-0 mb-2 mx-2 z-30 rounded-xl border border-[color:var(--paper-line)] bg-[color:var(--paper-raised)] shadow-2xl overflow-hidden"
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[color:var(--paper-line)]">
        <span className="text-[12px] font-semibold uppercase tracking-wider text-[color:var(--ink-dim)]">
          {t('report.title')}
        </span>
        <button
          data-testid="report-refresh"
          onClick={() => void tazele()}
          title={t('report.refresh')}
          className="ml-auto p-1 rounded-md text-[color:var(--ink-faint)] hover:text-[color:var(--ink)] hover:bg-[color:var(--paper-sunk)] transition-colors"
        >
          <RefreshCw size={12} />
        </button>
        <button
          data-testid="report-close"
          onClick={onClose}
          className="p-1 rounded-md text-[color:var(--ink-faint)] hover:text-[color:var(--ink)] hover:bg-[color:var(--paper-sunk)] transition-colors"
        >
          <X size={13} />
        </button>
      </div>
      <div className="p-3 space-y-3 max-h-[52vh] overflow-y-auto">
        {bolum(t('report.usage'), 'usage', usage)}
        {bolum(t('report.context'), 'context', context)}
      </div>
    </motion.div>
  );
};
