import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useLang } from '../../lib/i18n';
import {
  Brain,
  Sparkles,
  ChevronDown,
  Download,
  Upload,
  Gauge,
  Rocket
} from 'lucide-react';
import { ContextUsage } from './types';
import { GenerationModeSelector, GenerationMode } from './GenerationModeSelector';

// Kanonik effort skalası — hangi seviyelerin GÖSTERİLECEĞİ backend kayıtçısından
// (/effort-capabilities) gelir; provider+model gerçekte neyi destekliyorsa o.
export type ThinkingLevel =
  'auto' | 'off' | 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';

export interface EffortCaps {
  levels: string[];
  default?: string;
  note?: string;
}

interface ControlPanelProps {
  thinkingLevel: ThinkingLevel;
  // Chooses a level: home.tsx `chooseEffort`, which also switches Ultracode off.
  // The phone's effort request goes through the same function.
  setThinkingLevel: (val: ThinkingLevel) => void;
  // Backend kayıtçısından aktif provider+model'in gerçek seviye listesi.
  effortCaps?: EffortCaps | null;
  generationMode: GenerationMode;
  setGenerationMode: (mode: GenerationMode) => void;
  isAnalyzingProject: boolean;
  activeConvId: number | null;
  analyzeProject: (silent?: boolean) => Promise<void>;
  exportMemory: () => Promise<void>;
  importMemory: () => Promise<void>;
  compactConversation: () => Promise<void>;
  isCompacting: boolean;
  // `null` = ÖLÇÜM YOK (istek başarısız ya da hiç yapılmadı), sıfır tahmin
  // değil. Üretici taraf (useChat) bu ayrımı gönderiyor; burada `undefined` ile
  // aynı dala düşüyor ve gösterge "henüz veri yok" diyor. Sıfır bir halka
  // çizmek, hiç ölçmediğimiz bir şeyi ölçtük demek olurdu.
  contextUsage?: ContextUsage | null;
  /** Kullanım/bağlam panelini aç-kapa. Panelin kendisi home.tsx'te mount ediliyor. */
  reportsOpen?: boolean;
  onToggleReports?: () => void;
  // Claude-only (subscription + claude-* model). Diğer sağlayıcılarda gizlenir.
  isClaudeSubscription?: boolean;
  ultracode?: boolean;
  setUltracode?: (v: boolean) => void;
}

export const ControlPanel: React.FC<ControlPanelProps> = ({
  thinkingLevel,
  setThinkingLevel,
  effortCaps,
  generationMode,
  setGenerationMode,
  isAnalyzingProject,
  activeConvId,
  analyzeProject,
  exportMemory,
  importMemory,
  compactConversation,
  isCompacting,
  contextUsage,
  reportsOpen = false,
  onToggleReports,
  isClaudeSubscription = false,
  ultracode = false,
  setUltracode
}) => {
  const { t } = useLang();
  const [showMemoryMenu, setShowMemoryMenu] = useState(false);
  const [showThinkingMenu, setShowThinkingMenu] = useState(false);

  const yuzde = contextUsage?.percent ?? 0;

  // Seviye görselleri — hangi seviyelerin listeleneceğine backend kayıtçısı karar
  // verir (effortCaps.levels). Burada yalnız etiket/renk/açıklama eşlemesi var.
  const LEVEL_META: Record<string, { label: string; color: string; desc: string }> = {
    auto:    { label: t('effort.label.auto'),    color: 'text-sky-400',     desc: t('effort.desc.auto') },
    off:     { label: t('effort.label.off'),     color: 'text-slate-500',   desc: t('effort.desc.off') },
    none:    { label: t('effort.label.none'),    color: 'text-slate-500',   desc: t('effort.desc.none') },
    minimal: { label: t('effort.label.minimal'), color: 'text-teal-400',    desc: t('effort.desc.minimal') },
    low:     { label: t('effort.label.low'),     color: 'text-emerald-400', desc: t('effort.desc.low') },
    medium:  { label: t('effort.label.medium'),  color: 'text-violet-400',  desc: t('effort.desc.medium') },
    high:    { label: t('effort.label.high'),    color: 'text-fuchsia-400', desc: t('effort.desc.high') },
    xhigh:   { label: t('effort.label.xhigh'),   color: 'text-orange-400',  desc: t('effort.desc.xhigh') },
    max:     { label: t('effort.label.max'),     color: 'text-red-400',     desc: t('effort.desc.max') },
  };
  const levels = (effortCaps?.levels?.length ? effortCaps.levels : ['auto'])
    .filter((l) => LEVEL_META[l]);
  const effLevel: string = levels.includes(thinkingLevel) ? thinkingLevel : 'auto';
  const activeMeta = LEVEL_META[effLevel] || LEVEL_META.auto;
  const onlyAuto = levels.length <= 1; // model effort desteklemiyor → bilgi amaçlı panel
  const triggerLabel = isClaudeSubscription && ultracode
    ? 'Ultracode'
    : `Effort ${activeMeta.label}`;

  return (
    // `flex-wrap`: bu şerit sabit genişlikte bir çekmecenin içinde ve her yeni
    // düğme onu sessizce taşırıyor — 30 Ağu 2026'da gösterge eklenince "Hafıza"
    // etiketi kesildi. Taşan içerik kırpılmak yerine alt satıra iniyor.
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 px-1 mt-1.5">
      <GenerationModeSelector value={generationMode} onChange={setGenerationMode} />
      <div className="w-px h-3 bg-slate-800" />
      
      {/* Effort Selector — dinamik segmented bar (seviyeler backend kayıtçısından) */}
      <div className="relative">
        <button
          onClick={() => setShowThinkingMenu(!showThinkingMenu)}
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all ${
            effLevel !== 'auto' || (isClaudeSubscription && ultracode)
              ? 'bg-violet-500/15 border border-violet-500/30 text-violet-400 shadow-[0_0_15px_rgba(139,92,246,0.1)]'
              : 'text-slate-500 hover:text-slate-300 hover:bg-slate-800/30'
          } ${showThinkingMenu ? 'bg-violet-500/20 text-violet-300' : ''}`}
          title={effortCaps?.note || t('effort.title')}
        >
          <Brain size={11} className={effLevel !== 'auto' ? 'animate-pulse' : ''} />
          <span>{triggerLabel}</span>
          {!ultracode && (effLevel === 'xhigh' || effLevel === 'max') && <Gauge size={10} className="text-orange-400" />}
          {isClaudeSubscription && ultracode && <Rocket size={10} className="text-cyan-400" />}
          <ChevronDown size={10} className={`opacity-50 transition-transform duration-200 ${showThinkingMenu ? 'rotate-180' : ''}`} />
        </button>

        {/* Segmented panel */}
        <AnimatePresence>
          {showThinkingMenu && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowThinkingMenu(false)} />
              <motion.div
                initial={{ opacity: 0, y: 10, scale: 0.95 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 10, scale: 0.95 }}
                className="absolute bottom-10 left-0 w-[300px] bg-[#0a0a0f] border border-slate-800 rounded-xl shadow-2xl z-50 p-2.5 overflow-hidden"
              >
                <div className="flex items-center justify-between px-0.5 pb-2">
                  <span className="text-[8.5px] font-semibold uppercase tracking-wider text-slate-500">Effort</span>
                  <span className={`text-[8.5px] font-medium ${activeMeta.color}`}>{activeMeta.label}</span>
                </div>

                {/* Segmented bar — yalnız modelin GERÇEKTEN desteklediği seviyeler */}
                <div className="flex w-full rounded-lg border border-slate-800 bg-black/40 p-0.5 gap-0.5">
                  {levels.map((id) => {
                    const meta = LEVEL_META[id];
                    const active = effLevel === id && !(isClaudeSubscription && ultracode);
                    return (
                      <button
                        key={id}
                        onClick={() => setThinkingLevel(id as ThinkingLevel)}
                        title={meta.desc}
                        className={`flex-1 min-w-0 px-1 py-1.5 rounded-md text-[9px] font-semibold truncate transition-all ${
                          active
                            ? `${meta.color} bg-white/[0.07] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.08)]`
                            : 'text-slate-500 hover:text-slate-300 hover:bg-white/[0.03]'
                        }`}
                      >
                        {meta.label}
                      </button>
                    );
                  })}
                </div>

                {/* Aktif seviye açıklaması / model notu */}
                <div className="px-0.5 pt-2 text-[9px] leading-relaxed text-slate-500">
                  {isClaudeSubscription && ultracode
                    ? t('effort.ultracodeDesc')
                    : (onlyAuto && effortCaps?.note) ? effortCaps.note : activeMeta.desc}
                </div>

                {/* Claude-only: Ultracode — bağımsız mod satırı */}
                {isClaudeSubscription && (
                  <>
                    <div className="my-2 h-px bg-slate-800" />
                    <button
                      onClick={() => setUltracode?.(!ultracode)}
                      title={t('ultracode.title')}
                      className={`w-full text-left px-2 py-1.5 rounded-lg text-[10px] transition-all hover:bg-white/5 flex items-center justify-between ${
                        ultracode ? 'text-cyan-400 bg-white/5' : 'text-slate-400'
                      }`}
                    >
                      <span className="flex items-center gap-1.5 font-medium"><Rocket size={11} />Ultracode</span>
                      <span className={`w-1.5 h-1.5 rounded-full ${ultracode ? 'bg-current shadow-[0_0_8px_currentColor]' : 'bg-slate-700'}`} />
                    </button>
                  </>
                )}
              </motion.div>
            </>
          )}
        </AnimatePresence>
      </div>

      <div className="w-px h-3 bg-slate-800" />

      {/* Projeyi Öğren & Hafıza Menüsü */}
      <div className="relative flex items-center">
        <button
          onClick={() => analyzeProject()}
          disabled={isAnalyzingProject}
          className={`flex items-center gap-1.5 px-3 py-1 rounded-l-lg text-[11px] font-medium transition-all ${
            isAnalyzingProject
              ? 'bg-blue-500/20 text-blue-400 animate-pulse'
              : 'text-slate-500 hover:text-blue-400 hover:bg-blue-500/5'
          }`}
          title={t('memory.learnTitle')}
        >
          {isAnalyzingProject ? (
            <div className="flex items-center gap-2">
              <div className="w-2.5 h-2.5 border-2 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
              <span>{t('memory.learning')}</span>
            </div>
          ) : (
            <>
              <Sparkles size={11} className="text-blue-500" />
              <span>{t('memory.learnProject')}</span>
            </>
          )}
        </button>
        <button
          onClick={() => setShowMemoryMenu(!showMemoryMenu)}
          disabled={isAnalyzingProject || !activeConvId}
          className={`px-1.5 py-1 border-l border-slate-800 rounded-r-lg text-slate-500 hover:text-blue-400 hover:bg-blue-500/5 transition-all ${
            showMemoryMenu ? 'bg-blue-500/10 text-blue-400' : ''
          }`}
        >
          <ChevronDown size={12} className={`transition-transform duration-200 ${showMemoryMenu ? 'rotate-180' : ''}`} />
        </button>

        <AnimatePresence>
          {showMemoryMenu && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowMemoryMenu(false)} />
              <motion.div
                initial={{ opacity: 0, y: 10, scale: 0.95 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 10, scale: 0.95 }}
                className="absolute bottom-10 right-0 w-48 bg-[#0a0a0f] border border-slate-800 rounded-xl shadow-2xl z-50 py-1.5 overflow-hidden"
              >
                <button
                  onClick={() => { analyzeProject(); setShowMemoryMenu(false); }}
                  className="w-full flex items-center justify-between px-3 py-2 text-[11px] text-slate-300 hover:bg-blue-600/10 hover:text-blue-400 transition-all"
                >
                  <span>{t('memory.refresh')}</span>
                  <Sparkles size={11} />
                </button>
                <button
                  onClick={async () => { setShowMemoryMenu(false); await exportMemory(); }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-[11px] text-slate-300 hover:bg-blue-600/10 hover:text-blue-400 transition-colors"
                >
                  <Download size={13} />
                  {t('memory.export')}
                </button>
                <button
                  onClick={async () => { setShowMemoryMenu(false); await importMemory(); }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-[11px] text-slate-300 hover:bg-emerald-600/10 hover:text-emerald-400 transition-colors"
                >
                  <Upload size={13} />
                  {t('memory.import')}
                </button>
              </motion.div>
            </>
          )}
        </AnimatePresence>
      </div>

      {/* Kalıcı bağlam + kullanım göstergesi.
          Eskiden `contextUsage.percent > 0` koşuluyla çiziliyordu, yani ilk tur
          bitene kadar hiç görünmüyordu — "sürekli görünen bir yer" isteğinin tam
          tersi. Artık aktif sohbet varsa hep duruyor ve verisi yokken bunu
          SÖYLÜYOR; boş bir halka "doluluk sıfır" diye okunurdu. */}
      {activeConvId && (
        <>
          <div className="w-px h-3 bg-slate-800" />
          {/* Başlık YANINDAKİ SAYIYI anlatmak zorunda; ayrımı `real` yapıyor,
              çünkü yüzdenin kendisi de aşağıda ona bakarak çiziliyor. İki ayrı
              koşul kullanmak, sayının ölçüm ama başlığın tahmin dediği kartı
              yeniden mümkün kılardı — ölçüldü 30 Ağu 2026: `%7 · 69.9k/1m`
              sayısının üstünde "Yaklaşık doluluk… bu bir tahmin" yazıyordu ve
              kullanıcı hangisinin doğru olduğunu seçemiyordu. */}
          <button
            data-testid="context-gauge"
            onClick={() => compactConversation()}
            disabled={isCompacting}
            title={!contextUsage
              ? t('usage.noData')
              : contextUsage.real
                ? t('usage.realTitle', {
                    yuzde: contextUsage.percent,
                    used: contextUsage.real.used,
                    total: contextUsage.real.total,
                  })
                : t('usage.estimateTitle', { yuzde: contextUsage.percent, sayi: contextUsage.message_count })}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors group relative ${
              yuzde >= 90 ? 'bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20'
              : yuzde >= 75 ? 'bg-amber-500/10 border border-amber-500/30 text-amber-400 hover:bg-amber-500/20'
              : 'text-slate-500 hover:text-slate-300 border border-transparent hover:border-slate-800/50 hover:bg-slate-800/30'
            }`}
          >
            <div className="relative w-3.5 h-3.5 flex items-center justify-center">
              <svg className="w-full h-full transform -rotate-90" viewBox="0 0 36 36">
                <path
                  className="text-slate-800"
                  d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className={`${yuzde >= 90 ? 'text-red-500'
                      : yuzde >= 75 ? 'text-amber-500'
                        : 'text-blue-500'
                    } transition-all duration-500`}
                  strokeDasharray={`${yuzde}, 100`}
                  d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="4"
                />
              </svg>
              {contextUsage?.should_compact && (
                <span className="absolute -top-1 -right-1 w-1.5 h-1.5 bg-red-500 rounded-full animate-ping" />
              )}
            </div>
            {/* "~" YALNIZ tahminde. Gerçek sayı geldiğinde (bkz. `/context`
                raporu) işaret kalkıyor ve kullanılan/pencere olduğu gibi
                yazılıyor — tahmin işaretini ölçülmüş bir sayının üstünde
                bırakmak da bir yalan olurdu, ters yönde. */}
            <span data-testid="context-percent">
              {!contextUsage
                ? t('usage.noData')
                : contextUsage.real
                  ? `%${yuzde} · ${contextUsage.real.used}/${contextUsage.real.total}`
                  : `~%${yuzde}`}
            </span>
            <span>{isCompacting ? t('memory.compacting') : t('memory.compact')}</span>
          </button>

          {/* The per-turn token/cost readout was REMOVED on 5 Sep 2026. The
              counter only summed SSE `turn_usage` events: 4 of the 8 run paths
              report no tokens at all, the counter restarted from zero whenever
              the app restarted and reset on every conversation switch — so the
              number on screen answered none of the questions the user was
              actually asking of it ("what did this chat cost"). The real
              figures live in the "Kullanım" panel, which states their source. */}
          {/* Kullanım/bağlam raporlarını SOHBETE YAZMADAN açan düğme. Bu iki
              rapora bugüne kadar ancak sohbete `/usage` yazarak bakılabiliyordu,
              yani her bakış geçmişe bir mesaj çifti bırakıyordu. */}
          {onToggleReports && (
            <button
              data-testid="reports-toggle"
              onClick={onToggleReports}
              title={t('report.title')}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors border ${
                reportsOpen
                  ? 'bg-blue-500/10 border-blue-500/30 text-blue-400'
                  : 'text-slate-500 hover:text-slate-300 border-transparent hover:border-slate-800/50 hover:bg-slate-800/30'
              }`}
            >
              <Gauge size={12} />
              <span>{t('report.button')}</span>
            </button>
          )}
        </>
      )}
    </div>
  );
};
