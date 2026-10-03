import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useLang } from '../../lib/i18n';
import { Sparkles, ChevronDown, Download, Upload, Gauge, Rocket } from 'lucide-react';
import { ContextUsage } from './types';
import { GenerationModeSelector, GenerationMode } from './GenerationModeSelector';
import { StripUse } from './UsageMeters';
import type { UsageFamilyId, UsageLimits } from '../../lib/usageLimits';

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
  /** Plan usage (`useUsageLimits`) and the current chat's family/model for the "Kota" group. */
  usage?: UsageLimits | null;
  usageFamily?: UsageFamilyId | null;
  modelId?: string | null;
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
  setUltracode,
  usage = null,
  usageFamily = null,
  modelId = null,
}) => {
  const { t } = useLang();
  const [showMemoryMenu, setShowMemoryMenu] = useState(false);
  const [showThinkingMenu, setShowThinkingMenu] = useState(false);
  const [showMore, setShowMore] = useState(false);

  const yuzde = contextUsage?.percent ?? 0;

  // Labels and descriptions per level; which levels are LISTED is the backend registry's call
  // (effortCaps.levels). `pips` is the mockup's 3-step effort meter for each level.
  const LEVEL_META: Record<string, { label: string; desc: string; pips: number }> = {
    auto:    { label: t('effort.label.auto'),    desc: t('effort.desc.auto'),    pips: 0 },
    off:     { label: t('effort.label.off'),     desc: t('effort.desc.off'),     pips: 0 },
    none:    { label: t('effort.label.none'),    desc: t('effort.desc.none'),    pips: 0 },
    minimal: { label: t('effort.label.minimal'), desc: t('effort.desc.minimal'), pips: 1 },
    low:     { label: t('effort.label.low'),     desc: t('effort.desc.low'),     pips: 1 },
    medium:  { label: t('effort.label.medium'),  desc: t('effort.desc.medium'),  pips: 2 },
    high:    { label: t('effort.label.high'),    desc: t('effort.desc.high'),    pips: 3 },
    xhigh:   { label: t('effort.label.xhigh'),   desc: t('effort.desc.xhigh'),   pips: 3 },
    max:     { label: t('effort.label.max'),     desc: t('effort.desc.max'),     pips: 3 },
  };
  const levels = (effortCaps?.levels?.length ? effortCaps.levels : ['auto'])
    .filter((l) => LEVEL_META[l]);
  const effLevel: string = levels.includes(thinkingLevel) ? thinkingLevel : 'auto';
  const activeMeta = LEVEL_META[effLevel] || LEVEL_META.auto;
  const onlyAuto = levels.length <= 1; // the model has no effort control: the panel only informs
  const ultra = isClaudeSubscription && ultracode;
  const shownLabel = ultra ? 'Ultracode' : activeMeta.label;
  const shownPips = ultra ? 3 : activeMeta.pips;
  // The memory bar: five segments of the context window (mockup `.energy`), short so the
  // strip also fits the usage group.
  const energyOn = contextUsage ? Math.min(5, Math.max(0, Math.round(yuzde / 20))) : 0;
  const memoryTitle = !contextUsage
    ? t('usage.noData')
    : contextUsage.real
      ? t('usage.realTitle', {
          yuzde: contextUsage.percent,
          used: contextUsage.real.used,
          total: contextUsage.real.total,
        })
      : t('usage.estimateTitle', { yuzde: contextUsage.percent, sayi: contextUsage.message_count });

  return (
    // The mockup's status strip under the composer box: thinking, memory, plan usage, more
    // settings (the key hint moved to the send button's tooltip). The less used controls (mode,
    // project memory, usage report) live behind "More settings"; the popover stays mounted
    // (hidden) so nothing it holds loses state.
    <div className="strip" data-testid="composer-strip">
      <div className="strip-anchor">
        <button
          type="button"
          className="strip-item"
          data-level={ultra ? 'ultracode' : effLevel}
          aria-expanded={showThinkingMenu}
          aria-label={t('strip.thinkingAria', { seviye: shownLabel })}
          onClick={() => setShowThinkingMenu(!showThinkingMenu)}
          title={effortCaps?.note || t('effort.title')}
        >
          {t('strip.thinking')}
          <span className="pips" aria-hidden="true">
            {[1, 2, 3].map(n => <i key={n} className={n <= shownPips ? 'on' : undefined} />)}
          </span>
          <b>{shownLabel}</b>
        </button>

        <AnimatePresence>
          {showThinkingMenu && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowThinkingMenu(false)} />
              <motion.div
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 6 }}
                className="strip-pop strip-effort"
              >
                <div className="strip-pop-head">
                  <span className="strip-pop-k">Effort</span>
                  <span className="strip-pop-v">{activeMeta.label}</span>
                </div>

                {/* Segmented bar: only the levels the model really supports */}
                <div className="seg" role="group" aria-label="Effort">
                  {levels.map((id) => {
                    const meta = LEVEL_META[id];
                    const active = effLevel === id && !ultra;
                    return (
                      <button
                        key={id}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setThinkingLevel(id as ThinkingLevel)}
                        title={meta.desc}
                        className="seg-btn"
                      >
                        {meta.label}
                      </button>
                    );
                  })}
                </div>

                {/* The active level's description, or the model's own note */}
                <p className="strip-pop-note">
                  {ultra
                    ? t('effort.ultracodeDesc')
                    : (onlyAuto && effortCaps?.note) ? effortCaps.note : activeMeta.desc}
                </p>

                {/* Claude only: Ultracode, its own switch row */}
                {isClaudeSubscription && (
                  <button
                    type="button"
                    onClick={() => setUltracode?.(!ultracode)}
                    title={t('ultracode.title')}
                    aria-pressed={ultracode}
                    className="strip-pop-row"
                  >
                    <span className="strip-pop-row-l"><Rocket size={14} aria-hidden="true" />Ultracode</span>
                    <span className="strip-pop-dot" data-on={ultracode || undefined} aria-hidden="true" />
                  </button>
                )}
              </motion.div>
            </>
          )}
        </AnimatePresence>
      </div>

      {/* Context window + usage gauge. Drawn whenever a chat is active, before its first turn
          too ("a place that is always visible"); without data it SAYS so: an empty bar would
          read as "zero full", which nobody measured. A click summarises the chat, as before. */}
      {activeConvId && (
        <>
          <span className="strip-sep" aria-hidden="true" />
          {/* The title must describe the number next to it; `real` decides both, so the number
              cannot be a measurement while the title calls it an estimate (measured 30 Aug 2026:
              `%7 · 69.9k/1m` sat under "approximate fill... this is an estimate"). */}
          <button
            type="button"
            data-testid="context-gauge"
            data-level={!contextUsage ? 'none' : yuzde >= 90 ? 'full' : yuzde >= 75 ? 'high' : 'ok'}
            onClick={() => compactConversation()}
            disabled={isCompacting}
            title={memoryTitle}
            aria-label={`${t('strip.memory')}: ${memoryTitle}`}
            className="strip-item strip-memory"
            data-guide="strip-memory"
          >
            {t('strip.memory')}
            <span className="energy" aria-hidden="true">
              {Array.from({ length: 5 }, (_, i) => <i key={i} className={i < energyOn ? 'on' : undefined} />)}
            </span>
            {/* "~" ONLY on an estimate. When the real figure arrives (the `/context` report) the
                mark goes and used/window is written as is: an estimate mark over a measured
                number would be a lie too, the other way round. The word "dolu" lives in the
                title/aria-label; used/window shows only where the strip has room (CSS). */}
            <b>
              <span data-testid="context-percent" className="num">
                {!contextUsage
                  ? t('usage.noData')
                  : contextUsage.real
                    ? <>{t('strip.pct', { yuzde })}<span className="strip-mem-detail"> · {contextUsage.real.used}/{contextUsage.real.total}</span></>
                    : `~${t('strip.pct', { yuzde })}`}
              </span>
            </b>
            {/* Past the compaction threshold: one quiet marker, no ping. */}
            {contextUsage?.should_compact && <span className="strip-alert" data-should-compact aria-hidden="true" />}
            {isCompacting && <span className="strip-busy">{t('memory.compacting')}</span>}
          </button>
        </>
      )}

      {/* Plan usage of the chat's model family; renders nothing without numbers. */}
      <StripUse limits={usage} family={usageFamily} modelId={modelId} withSep />

      <span className="strip-sep strip-sep-last" aria-hidden="true" />
      <div className="strip-anchor">
        <button
          type="button"
          className="strip-item strip-more"
          data-guide="strip-more"
          aria-expanded={showMore}
          aria-controls="strip-more-pop"
          onClick={() => setShowMore(v => !v)}
        >
          <span className="strip-more-t">{t('strip.more')}</span>
          <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>
        </button>
        {showMore && <div className="fixed inset-0 z-40" onClick={() => { setShowMore(false); setShowMemoryMenu(false); }} />}
        <div id="strip-more-pop" className="strip-pop strip-more-pop" hidden={!showMore}>
          <GenerationModeSelector value={generationMode} onChange={setGenerationMode} />

          {/* Learn the project, and the memory menu */}
          <div className="strip-pop-pair">
            <button
              type="button"
              onClick={() => analyzeProject()}
              disabled={isAnalyzingProject}
              className="strip-pop-row"
              title={t('memory.learnTitle')}
              data-busy={isAnalyzingProject || undefined}
            >
              <span className="strip-pop-row-l">
                <Sparkles size={14} aria-hidden="true" />
                {isAnalyzingProject ? t('memory.learning') : t('memory.learnProject')}
              </span>
            </button>
            <button
              type="button"
              onClick={() => setShowMemoryMenu(!showMemoryMenu)}
              disabled={isAnalyzingProject || !activeConvId}
              aria-expanded={showMemoryMenu}
              className="icon-btn"
            >
              <ChevronDown size={14} aria-hidden="true" />
            </button>
          </div>
          {showMemoryMenu && (
            <div className="strip-pop-sub">
              <button type="button" className="strip-pop-row" onClick={() => { analyzeProject(); setShowMemoryMenu(false); }}>
                <span className="strip-pop-row-l"><Sparkles size={14} aria-hidden="true" />{t('memory.refresh')}</span>
              </button>
              <button type="button" className="strip-pop-row" onClick={async () => { setShowMemoryMenu(false); await exportMemory(); }}>
                <span className="strip-pop-row-l"><Download size={14} aria-hidden="true" />{t('memory.export')}</span>
              </button>
              <button type="button" className="strip-pop-row" onClick={async () => { setShowMemoryMenu(false); await importMemory(); }}>
                <span className="strip-pop-row-l"><Upload size={14} aria-hidden="true" />{t('memory.import')}</span>
              </button>
            </div>
          )}

          {/* The per-turn token/cost readout was REMOVED on 5 Sep 2026. The counter only summed
              SSE `turn_usage` events: 4 of the 8 run paths report no tokens at all, it restarted
              from zero with the app and reset on every conversation switch, so the number
              answered none of the questions asked of it. The real figures live in the usage
              panel, which states their source. */}
          {/* Opens the usage / context reports WITHOUT writing to the chat (`/usage` used to leave
              a message pair in the history for every look). */}
          {activeConvId && onToggleReports && (
            <button
              type="button"
              data-testid="reports-toggle"
              onClick={onToggleReports}
              title={t('report.title')}
              aria-pressed={reportsOpen}
              className="strip-pop-row"
            >
              <span className="strip-pop-row-l"><Gauge size={14} aria-hidden="true" />{t('report.button')}</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

