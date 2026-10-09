import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { useLang } from '../../lib/i18n';
import { findAnchor } from '../../lib/guide/anchors';
import { place, type PlaceOutput } from '../../lib/guide/engine';
import { tx } from '../../lib/guide/registry';
import type { TourState } from '../../hooks/home/useGuide';
import type { GenerationMode } from './types';
import { MascotHead } from './BrandLogo';
import { GamachineFigure } from './GamachineMascot';

export interface GuideTourProps {
  tour: TourState;
  approvalMode: GenerationMode;
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
  onNameDraft: (v: string) => void;
}

const MODES: { id: GenerationMode; label: 'mode.step' | 'mode.balanced' | 'mode.auto'; sub: 'tour.modeStep' | 'tour.modeBalanced' | 'tour.modeAuto' }[] = [
  { id: 'step', label: 'mode.step', sub: 'tour.modeStep' },
  { id: 'balanced', label: 'mode.balanced', sub: 'tour.modeBalanced' },
  { id: 'auto', label: 'mode.auto', sub: 'tour.modeAuto' },
];

const reducedMotion = () =>
  document.documentElement.getAttribute('data-motion') === 'reduced'
  || !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

function cssNum(el: Element, prop: string, fallback: number): number {
  const v = parseFloat(getComputedStyle(el).getPropertyValue(prop));
  return Number.isNaN(v) ? fallback : v;
}

/** Replays a one-shot CSS animation class (the mockup's `replay`). */
function replay(el: Element | null, cls: string, ms: number) {
  if (!el) return;
  el.classList.remove(cls);
  void (el as HTMLElement).offsetWidth;
  el.classList.add(cls);
  setTimeout(() => el.classList.remove(cls), ms);
}

/** Brings a target hidden in a scrolled pane (a settings page, the profile) into its pane's view. */
function revealInPane(el: HTMLElement) {
  for (let p = el.parentElement; p && !p.classList.contains('app'); p = p.parentElement) {
    const oy = getComputedStyle(p).overflowY;
    if ((oy !== 'auto' && oy !== 'scroll') || p.scrollHeight <= p.clientHeight) continue;
    const pr = p.getBoundingClientRect(), r = el.getBoundingClientRect();
    if (r.top < pr.top || r.bottom > pr.bottom) p.scrollTop += r.top - pr.top - Math.max(16, (pr.height - r.height) / 3);
    return;
  }
}

/**
 * The tour overlay (mockup `.tour`): a spotlight on the step's anchor and a card beside it, over
 * the real app, in the same markup every theme styles (guide.css). One component for the core tour
 * and for a single guide topic. The rest of the frame is `inert` while it is open.
 */
export const GuideTour: React.FC<GuideTourProps> = ({ tour, approvalMode, onNext, onBack, onSkip, onNameDraft }) => {
  const { t, lang } = useLang();
  const rootRef = useRef<HTMLDivElement>(null);
  const holeRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const figRef = useRef<HTMLDivElement>(null);
  const leadRef = useRef<SVGPathElement>(null);
  const dotRef = useRef<SVGCircleElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const targetRef = useRef<Element | null>(null);
  const [fig, setFig] = useState<{ pose: string; flip: boolean }>({ pose: 'wave', flip: false });

  const { seq, cur, mode } = tour;
  const it = seq[cur - 1];
  const step = it.step;
  const last = cur === seq.length;
  const onName = step.ui === 'name';
  const name = tour.nameDraft.replace(/\s+/g, ' ').trim();

  const measure = useCallback(() => {
    const root = rootRef.current, card = cardRef.current, box = boxRef.current, hole = holeRef.current;
    if (!root || !card || !box || !hole) return;
    let target = step.anchor ? findAnchor(step.anchor) : null;
    if (step.anchor && !target) console.info('[guide] anchor not on screen, card centred:', step.anchor);
    if (target) { revealInPane(target); target = findAnchor(step.anchor!); }
    targetRef.current = target;
    const F = root.getBoundingClientRect();
    const r = target?.getBoundingClientRect();
    const out: PlaceOutput = place({
      frame: { width: F.width, height: F.height },
      target: r ? { left: r.left - F.left, top: r.top - F.top, width: r.width, height: r.height } : null,
      card: { width: card.offsetWidth, height: card.offsetHeight },
      box: { width: box.offsetWidth, height: box.offsetHeight },
      figWidth: figRef.current && getComputedStyle(figRef.current).display !== 'none' ? figRef.current.offsetWidth : 0,
      side: step.side, align: step.align, pose: step.pose,
      css: {
        pad: cssNum(root, '--tour-pad', 6), gap: cssNum(root, '--tour-gap', 18),
        figToward: cssNum(root, '--tour-fig-toward', 1) === 1,
        leadInset: cssNum(root, '--tour-lead-inset', 0), leadSag: cssNum(root, '--tour-lead-sag', 0),
      },
    });
    root.dataset.side = out.side;
    card.dataset.fig = out.fig;
    card.style.left = `${out.card.left}px`;
    card.style.top = `${out.card.top}px`;
    const h = out.hole;
    hole.style.cssText = `left:${h.left}px;top:${h.top}px;width:${h.width}px;height:${h.height}px`;
    if (out.caret != null) box.style.setProperty('--caret', `${out.caret}px`);
    if (out.lead && leadRef.current && dotRef.current) {
      leadRef.current.setAttribute('d', out.lead.d);
      dotRef.current.setAttribute('cx', String(out.lead.x));
      dotRef.current.setAttribute('cy', String(out.lead.y));
    }
    setFig(prev => (prev.pose === out.pose && prev.flip === out.flip ? prev : { pose: out.pose, flip: out.flip }));
  }, [step]);

  // Each step: measure once the prepare actions have rendered, again after menus and drawers
  // finish their short entrance, and on resize.
  useLayoutEffect(() => {
    const reduced = reducedMotion();
    const app = rootRef.current?.closest('.app');
    app?.classList.add('is-init');
    measure();
    const raf = requestAnimationFrame(() => requestAnimationFrame(() => app?.classList.remove('is-init')));
    const again = setTimeout(measure, reduced ? 0 : 240);
    if (!reduced) { replay(boxRef.current, 'is-entering', 260); replay(holeRef.current, 'is-locking', 260); }
    const focus = setTimeout(() => (onName ? nameRef.current : nextRef.current)?.focus({ preventScroll: true }), 0);
    window.addEventListener('resize', measure);
    // The spotlighted element can vanish mid-step (a pending approval card gets resolved): measure
    // again so the anchor is re-resolved or the card falls back to the centre instead of leaving
    // the hole on the old spot (guide audit, 2 Oct 2026). An anchor missing at both measures (a
    // pane or list still loading) is looked for again once per frame while the DOM changes, so it
    // is spotlighted when it renders instead of the card staying centred for the whole step.
    let late = 0;
    const gone = new MutationObserver(() => {
      const t = targetRef.current;
      if (t) { if (!t.isConnected) measure(); return; }
      if (!step.anchor || late) return;
      late = requestAnimationFrame(() => {
        late = 0;
        if (!targetRef.current && findAnchor(step.anchor!)) measure();
      });
    });
    gone.observe(document.body, { childList: true, subtree: true });
    return () => {
      gone.disconnect();
      cancelAnimationFrame(late);
      cancelAnimationFrame(raf); clearTimeout(again); clearTimeout(focus);
      window.removeEventListener('resize', measure);
      app?.classList.remove('is-init');
    };
  }, [cur, seq, measure, onName]);

  // Open: the rest of the frame goes inert (no focus, no clicks); closing gives it back and puts
  // the focus where the page decides (useGuide's finish).
  useEffect(() => {
    const root = rootRef.current;
    const app = root?.parentElement;
    if (!root || !app) return;
    // Elements already inert stay so afterwards; screens a step mounts (the profile) join in.
    const touched = new Set<Element>();
    const quiet = () => {
      for (const el of Array.from(app.children)) {
        if (el === root || touched.has(el) || el.hasAttribute('inert')) continue;
        el.setAttribute('inert', '');
        touched.add(el);
      }
    };
    quiet();
    const watch = new MutationObserver(quiet);
    watch.observe(app, { childList: true });
    if (!reducedMotion()) replay(root, 'is-opening', 320);
    return () => { watch.disconnect(); touched.forEach(el => el.removeAttribute('inert')); };
  }, []);

  // Function form: a plain string would read `$&`, `$'` in a typed name as replacement patterns
  // (guide audit, 2 Oct 2026).
  const text = name && step.text_named ? tx(step.text_named, lang).replace('{name}', () => name) : tx(step.text, lang);
  const nextLabel = last
    ? (mode === 'core' && !tour.toGuide ? t('tour.last') : t('tour.toGuide'))
    : onName ? t('tour.cont') : t('tour.next');

  return (
    <div
      ref={rootRef}
      className="tour"
      role="dialog"
      aria-modal="true"
      aria-labelledby="tour-title"
      aria-describedby="tour-text"
      data-testid="guide-tour"
      data-step={cur}
      data-id={step.ui || it.topic.id}
      data-mode={mode}
    >
      <div ref={holeRef} className="tour-hole" aria-hidden="true" />
      <svg className="tour-lead" aria-hidden="true"><path ref={leadRef} className="tour-lead-p" /><circle ref={dotRef} className="tour-lead-dot" r="3.5" /></svg>
      {/* Clicks inside the card must not reach the app's "click outside closes it" handlers. */}
      <div ref={cardRef} className="tour-card" tabIndex={-1} onMouseDown={e => e.stopPropagation()} onClick={e => e.stopPropagation()}>
        <div ref={figRef} className={`tour-figure${fig.flip ? ' is-flip' : ''}`} aria-hidden="true">
          <GamachineFigure pose={fig.pose} tag={onName ? name : ''} />
        </div>
        <div ref={boxRef} className="tour-box">
          <span className="tour-pin" aria-hidden="true" />
          <header className="tour-head-row">
            <MascotHead className="mascot tour-avatar" />
            <span className="tour-kicker lex">
              <span className="lex-d">{t(mode === 'core' ? 'tour.kickCore' : 'tour.kickTopic')}</span>
              <span className="lex-q" lang="en">{t('tour.kickQuest')}</span>
              <span className="lex-p">{t('tour.kickNote')}</span>
              <span className="lex-w">{t('tour.kickTag')}</span>
            </span>
            {mode === 'topic' && <span className="tour-topic">{tx(it.topic.title, lang)}</span>}
            {seq.length > 1 && <span className="tour-count num"><span className="tour-n">{cur}</span> / <span className="tour-of">{seq.length}</span></span>}
            <button type="button" className="tour-skip" onClick={onSkip} data-testid="tour-skip">{t('tour.skip')}<kbd>Esc</kbd></button>
          </header>
          <h2 className="tour-title" id="tour-title">{tx(step.title, lang)}</h2>
          <p className="tour-text" id="tour-text">{text}</p>
          {onName && (
            <form className="tour-name" autoComplete="off" onSubmit={e => { e.preventDefault(); onNext(); }}>
              <label className="tour-name-k" htmlFor="tour-name-in">{t('tour.nameLabel')} <span className="tour-opt">{t('tour.nameOpt')}</span></label>
              <input
                ref={nameRef}
                className="tour-name-in"
                id="tour-name-in"
                data-testid="tour-name"
                type="text"
                maxLength={24}
                placeholder={t('tour.namePh')}
                value={tour.nameDraft}
                onChange={e => onNameDraft(e.target.value)}
              />
            </form>
          )}
          {step.ui === 'mode' && (
            <div className="tour-modes" aria-hidden="true">
              <div className="tour-sample">
                <span className="tour-sample-k">{t('tour.sampleK')}</span>
                <span className="tour-sample-t">{t('tour.sampleT')}</span>
                <span className="tour-sample-b"><i>{t('tour.sampleOk')}</i><i>{t('tour.sampleNo')}</i></span>
              </div>
              <ul className="tour-mode-list">
                {MODES.map(m => (
                  <li key={m.id} data-mode={m.id} className={m.id === approvalMode ? 'is-on' : undefined}>
                    <b>{t(m.label)}</b><span>{t(m.sub)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <footer className="tour-foot">
            <span className="tour-dots" aria-hidden="true" style={seq.length < 2 ? { visibility: 'hidden' } : undefined}>
              {seq.map((_, i) => <i key={i} className={i + 1 < cur ? 'is-done' : i + 1 === cur ? 'is-cur' : undefined} />)}
            </span>
            {cur > 1 && <button type="button" className="btn btn-ghost btn-sm tour-back" onClick={onBack} data-testid="tour-back">{t('tour.back')}</button>}
            <button ref={nextRef} type="button" className="btn btn-primary btn-sm tour-next" onClick={onNext} data-testid="tour-next">{nextLabel}</button>
          </footer>
        </div>
      </div>
    </div>
  );
};
