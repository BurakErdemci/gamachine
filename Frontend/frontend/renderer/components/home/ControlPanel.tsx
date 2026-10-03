import React, { useEffect, useId, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useLang } from '../../lib/i18n';
import { Sparkles, ChevronDown, Download, Upload, Gauge, Rocket, Paperclip, Film, Minimize2 } from 'lucide-react';
import { ContextUsage } from './types';
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
  /** The composer's own pickers (its attach button and its video dialog), reached from the menu. */
  onAttachFile?: () => void;
  onAddVideo?: () => void;
}

export const ControlPanel: React.FC<ControlPanelProps> = ({
  thinkingLevel,
  setThinkingLevel,
  effortCaps,
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
  onAttachFile,
  onAddVideo,
}) => {
  const { t } = useLang();
  const [showMemoryMenu, setShowMemoryMenu] = useState(false);
  const [showThinkingMenu, setShowThinkingMenu] = useState(false);
  const [showMore, setShowMore] = useState(false);
  const moreBtnRef = useRef<HTMLButtonElement>(null);
  const morePopRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const memoryOff = isAnalyzingProject || !activeConvId;

  const closeMore = (returnFocus: boolean) => {
    setShowMore(false);
    setShowMemoryMenu(false);
    if (returnFocus) moreBtnRef.current?.focus();
  };
  const menuItems = () =>
    Array.from(morePopRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? []);
  // Opening moves focus into the menu so the arrow keys work at once.
  useEffect(() => { if (showMore) menuItems()[0]?.focus(); }, [showMore]);
  const onMenuKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMore(true); return; }
    // Tab leaves a menu (WAI-ARIA menu button): close it and hand focus to the trigger without
    // preventing the default, so the browser's Tab / Shift+Tab then moves on from the trigger.
    if (e.key === 'Tab') { closeMore(true); return; }
    const items = menuItems();
    if (!items.length) return;
    const i = items.indexOf(document.activeElement as HTMLElement);
    const next = e.key === 'ArrowDown' ? (i + 1) % items.length
      : e.key === 'ArrowUp' ? (i <= 0 ? items.length - 1 : i - 1)
      : e.key === 'Home' ? 0
      : e.key === 'End' ? items.length - 1
      : -1;
    if (next < 0) return;
    e.preventDefault();
    items[next].focus();
  };
  // One labelled row: icon, title, one-line description. A row that does not apply stays
  // focusable (aria-disabled, not disabled) so the keyboard reaches it and the title says why.
  const row = (o: {
    key: string; icon: React.ReactNode; title: string; desc: string;
    run?: () => void; reason?: string | null; testId?: string; checked?: boolean;
  }) => {
    const off = !o.run || !!o.reason;
    return (
      <button
        key={o.key}
        type="button"
        role={o.checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
        aria-checked={o.checked}
        className="strip-pop-row menu-row"
        data-row={o.key}
        data-testid={o.testId}
        aria-disabled={off || undefined}
        aria-labelledby={`${menuId}-${o.key}-t`}
        aria-describedby={`${menuId}-${o.key}-d`}
        title={o.reason || undefined}
        onClick={() => { if (off) return; closeMore(true); o.run!(); }}
      >
        <span className="menu-row-ic" aria-hidden="true">{o.icon}</span>
        <span className="menu-row-txt">
          <span id={`${menuId}-${o.key}-t`} className="menu-row-t">{o.title}</span>
          <span id={`${menuId}-${o.key}-d`} className="menu-row-d">{o.desc}</span>
        </span>
      </button>
    );
  };

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
    // The mockup's status strip under the composer box: thinking, memory, plan usage, and the
    // "Add & chat" menu (the key hint moved to the send button's tooltip). The menu holds the
    // composer's attach/video pickers, project memory, summarising and the usage report; it stays
    // mounted (hidden) so nothing it holds loses state.
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
          ref={moreBtnRef}
          type="button"
          className="strip-item strip-more"
          data-guide="strip-more"
          aria-haspopup="menu"
          aria-expanded={showMore}
          aria-controls="strip-more-pop"
          onClick={() => (showMore ? closeMore(false) : setShowMore(true))}
        >
          <span className="strip-more-t">{t('strip.more')}</span>
          <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>
        </button>
        {showMore && <div className="fixed inset-0 z-40" onClick={() => closeMore(false)} />}
        {/* No mode list here: the top bar's mode chip and Settings > Onay modu set it (owner, 3 Oct 2026).
            Every row names itself and says what it does: icons alone were not understood (owner, 3 Oct 2026). */}
        <div
          id="strip-more-pop"
          ref={morePopRef}
          className="strip-pop strip-more-pop"
          role="menu"
          aria-label={t('strip.more')}
          hidden={!showMore}
          onKeyDown={onMenuKey}
        >
          <div role="group" aria-labelledby={`${menuId}-add`} className="menu-sec">
            <div id={`${menuId}-add`} className="menu-sec-h">{t('more.addSection')}</div>
            {row({ key: 'attach', icon: <Paperclip size={15} />, title: t('more.attach'), desc: t('more.attachDesc'), run: onAttachFile })}
            {row({ key: 'video', icon: <Film size={15} />, title: t('more.video'), desc: t('more.videoDesc'), run: onAddVideo })}
          </div>
          <div role="group" aria-labelledby={`${menuId}-chat`} className="menu-sec">
            <div id={`${menuId}-chat`} className="menu-sec-h">{t('more.chatSection')}</div>
            {/* Learn the project, and its memory submenu */}
            <div className="strip-pop-pair">
              {row({
                key: 'learn', icon: <Sparkles size={15} />,
                title: isAnalyzingProject ? t('memory.learning') : t('more.learn'), desc: t('more.learnDesc'),
                run: () => { void analyzeProject(); }, reason: isAnalyzingProject ? t('more.analyzing') : null,
              })}
              <button
                type="button"
                role="menuitem"
                onClick={() => { if (!memoryOff) setShowMemoryMenu(v => !v); }}
                aria-disabled={memoryOff || undefined}
                aria-expanded={showMemoryMenu}
                aria-label={t('more.memoryOptions')}
                title={memoryOff ? (isAnalyzingProject ? t('more.analyzing') : t('more.noChat')) : t('more.memoryOptions')}
                className="icon-btn"
              >
                <ChevronDown size={14} aria-hidden="true" />
              </button>
            </div>
            {showMemoryMenu && (
              <div className="strip-pop-sub">
                <button type="button" role="menuitem" className="strip-pop-row" onClick={() => { closeMore(true); void analyzeProject(); }}>
                  <span className="strip-pop-row-l"><Sparkles size={14} aria-hidden="true" />{t('memory.refresh')}</span>
                </button>
                <button type="button" role="menuitem" className="strip-pop-row" onClick={() => { closeMore(true); void exportMemory(); }}>
                  <span className="strip-pop-row-l"><Download size={14} aria-hidden="true" />{t('memory.export')}</span>
                </button>
                <button type="button" role="menuitem" className="strip-pop-row" onClick={() => { closeMore(true); void importMemory(); }}>
                  <span className="strip-pop-row-l"><Upload size={14} aria-hidden="true" />{t('memory.import')}</span>
                </button>
              </div>
            )}
            {/* Same action as a click on the Hafıza meter, which stays. */}
            {row({
              key: 'compact', icon: <Minimize2 size={15} />,
              title: isCompacting ? t('memory.compacting') : t('more.compact'), desc: t('more.compactDesc'),
              run: () => { void compactConversation(); },
              reason: !activeConvId ? t('more.noChat') : isCompacting ? t('more.compactingReason') : null,
              testId: 'compact-row',
            })}
            {/* The per-turn token/cost readout was REMOVED on 5 Sep 2026 (it summed SSE `turn_usage`
                events that half the run paths never send). The usage panel opens WITHOUT writing to
                the chat (`/usage` used to leave a message pair in the history for every look). */}
            {row({
              key: 'reports', icon: <Gauge size={15} />, title: t('more.reports'), desc: t('more.reportsDesc'),
              run: onToggleReports, reason: !activeConvId ? t('more.noChat') : null,
              testId: 'reports-toggle', checked: reportsOpen,
            })}
          </div>
        </div>
      </div>
    </div>
  );
};
