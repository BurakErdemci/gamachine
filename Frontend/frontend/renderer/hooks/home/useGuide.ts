import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  CORE_TOUR, isNew as isNewTopic, isSeen as isSeenTopic, listedTopics, registryWarnings, stateAt, stepsOf,
  topicById, type GuideContext, type SeqItem,
} from '../../lib/guide/registry';
import { runPrepare, type PrepareHandlers } from '../../lib/guide/prepare';
import { installedFrom, loadSeen, markTourDone, saveSeen, tourDone, type Seen } from '../../lib/guide/storage';
import type { GuideTopic } from '../../lib/guide/types';

/**
 * What the page lends the guide: the prepare actions (prepare.ts) plus the few things only the
 * page can do. Snapshot/restore is how leaving a topic undoes what its steps opened (REHBER
 * rule 5): the page records its panel, drawer and chat before the first step and puts them back.
 */
export interface GuideHost extends PrepareHandlers {
  /** The guide screen takes the main area: close settings, the profile and open menus. */
  showGuideScreen: () => void;
  snapshot: () => unknown;
  /** `mode` decides the chat: a topic returns to the chat it started on, the core tour stays on the new chat. */
  restore: (snap: unknown, mode: TourMode) => void;
  /** Between steps: close what the previous step opened that this step's state does not ask for. */
  unprepare: (prep: readonly string[]) => void;
  /** After the core tour (not from the guide): the composer gets the focus. */
  focusComposer: () => void;
}

export type TourMode = 'core' | 'topic';

export interface TourState {
  mode: TourMode;
  seq: SeqItem[];
  /** 1-based step. */
  cur: number;
  /** The topic being played (topic mode). */
  topicId: string | null;
  /** Return to the guide when the tour ends (a topic, or the core tour started from the guide). */
  toGuide: boolean;
  /** The name typed in the name step, saved when the step is left forward. */
  nameDraft: string;
}

export interface UseGuideArgs {
  ctx: GuideContext;
  host: GuideHost;
  userName: string;
  saveName: (name: string) => Promise<boolean> | void;
  appVersion: string;
  /** The app frame (chat, workspace) is on screen: the core tour may open by itself. */
  frameReady: boolean;
}

const clean = (s: string) => s.replace(/\s+/g, ' ').trim().slice(0, 24);

export function useGuide({ ctx, host, userName, saveName, appVersion, frameReady }: UseGuideArgs) {
  const [guideOpen, setGuideOpen] = useState(false);
  const [query, setQuery] = useState('');
  /** The topic card that gets the focus when the guide comes back after a topic. */
  const [focusTopic, setFocusTopic] = useState<string | null>(null);
  const [tour, setTour] = useState<TourState | null>(null);
  // SSR and the first client render agree on empty values; storage is read after mounting.
  const [seen, setSeen] = useState<Seen>({});
  const [from, setFrom] = useState(appVersion);
  useEffect(() => {
    setSeen(loadSeen());
    setFrom(installedFrom(appVersion));
  }, [appVersion]);

  // The latest values, for callbacks that must not go stale between renders.
  const live = useRef({ tour, host, userName, saveName, ctx, guideOpen });
  live.current = { tour, host, userName, saveName, ctx, guideOpen };
  const snap = useRef<unknown>(null);

  const warned = useRef(false);
  useEffect(() => {
    if (warned.current || process.env.NODE_ENV === 'production') return;
    warned.current = true;
    for (const w of registryWarnings(ctx)) console.info(w);
  }, [ctx]);

  const markSeen = useCallback((t: GuideTopic) => {
    if (t.core_only) return;
    setSeen(prev => {
      if (isSeenTopic(t, prev)) return prev;
      const next = { ...prev, [t.id]: t.rev ?? 1 };
      saveSeen(next);
      return next;
    });
  }, []);

  const commitName = useCallback((st: TourState | null) => {
    if (!st || st.seq[st.cur - 1]?.step.ui !== 'name') return;
    const v = clean(st.nameDraft);
    if (v !== clean(live.current.userName)) void live.current.saveName(v);
  }, []);

  /** Shows step `i` of `st`: its accumulated UI state, then the "watched" bookkeeping. */
  const show = useCallback((st: TourState, i: number): TourState => {
    const cur = Math.max(1, Math.min(st.seq.length, i));
    const prep = stateAt(st.seq, cur);
    const { host: h } = live.current;
    h.unprepare(prep);
    runPrepare(prep, h);
    const it = st.seq[cur - 1];
    // The core tour marks each one-step topic as it is shown; a topic counts once its last step is.
    if (st.mode === 'core' || cur === st.seq.length) markSeen(it.topic);
    return { ...st, cur };
  }, [markSeen]);

  const finish = useCallback((opts: { silent?: boolean; skipped?: boolean } = {}) => {
    const st = live.current.tour;
    if (!st) return;
    // Continuing past the name step saves it; skipping it does not (the name stays as it was,
    // empty on a first launch).
    if (!opts.skipped) commitName(st);
    live.current.host.restore(snap.current, st.mode);
    snap.current = null;
    setTour(null);
    if (st.mode === 'core') markTourDone();
    if (opts.silent) return;
    if (st.toGuide) {
      live.current.host.showGuideScreen();
      setGuideOpen(true);
      setFocusTopic(st.topicId ?? '');
    } else {
      live.current.host.focusComposer();
    }
  }, [commitName]);

  const play = useCallback((mode: TourMode, ids: readonly string[], step = 1) => {
    const seq = stepsOf(ids, live.current.ctx);
    if (!seq.length) return;
    if (live.current.tour) finish({ silent: true });
    const toGuide = mode === 'topic' || live.current.guideOpen;
    snap.current = live.current.host.snapshot();
    // A topic plays over the real app, not over the guide behind it.
    setGuideOpen(false);
    const st: TourState = { mode, seq, cur: 1, topicId: mode === 'topic' ? ids[0] : null, toGuide, nameDraft: clean(live.current.userName) };
    setTour(show(st, step));
  }, [finish, show]);

  const go = useCallback((i: number) => {
    const st = live.current.tour;
    if (!st) return;
    if (i !== st.cur) commitName(st);
    setTour(show(st, i));
  }, [commitName, show]);

  const next = useCallback(() => {
    const st = live.current.tour;
    if (!st) return;
    if (st.cur >= st.seq.length) finish(); else go(st.cur + 1);
  }, [finish, go]);
  const back = useCallback(() => {
    const st = live.current.tour;
    if (st && st.cur > 1) go(st.cur - 1);
  }, [go]);
  const skip = useCallback(() => finish({ skipped: true }), [finish]);
  const setNameDraft = useCallback((v: string) => setTour(st => (st ? { ...st, nameDraft: v } : st)), []);

  const openTour = useCallback((step = 1) => play('core', CORE_TOUR, step), [play]);
  const playTopic = useCallback((id: string, step = 1) => play('topic', [id], step), [play]);

  const openGuide = useCallback((q?: string) => {
    if (live.current.tour) finish({ silent: true });
    live.current.host.showGuideScreen();
    if (q != null) setQuery(q);
    setFocusTopic(null);
    setGuideOpen(true);
  }, [finish]);
  const closeGuide = useCallback(() => setGuideOpen(false), []);

  // Keys. While a tour runs: Esc skips (capture, before the app's own Esc handling: settings,
  // menus), arrows step unless the name field is being typed in. Otherwise F1 opens the guide
  // anywhere, and Esc on the guide clears its search first, then goes back to the chat.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const st = live.current.tour;
      if (st) {
        if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); skip(); return; }
        const typing = e.target instanceof HTMLElement && e.target.matches('input, textarea');
        if (typing) return;
        if (e.key === 'ArrowRight') { e.preventDefault(); e.stopImmediatePropagation(); if (st.cur < st.seq.length) go(st.cur + 1); }
        if (e.key === 'ArrowLeft') { e.preventDefault(); e.stopImmediatePropagation(); if (st.cur > 1) go(st.cur - 1); }
        return;
      }
      if (e.key === 'F1') { e.preventDefault(); openGuide(); return; }
      if (e.key === 'Escape' && live.current.guideOpen && !e.defaultPrevented) {
        if (document.querySelector('[data-confirm-dialog], [role="alertdialog"]')) return;
        const t = e.target;
        if (t instanceof HTMLInputElement && t.dataset.guideSearch !== undefined && t.value) {
          e.preventDefault();
          setQuery('');
          return;
        }
        e.preventDefault();
        setGuideOpen(false);
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [go, openGuide, skip]);

  // First launch: the core tour opens by itself once, after the opening animation (mockup timing:
  // 0.4 s after the intro ends, 0.45 s after it is skipped; with no intro, 3.2 s after load or
  // 0.42 s after a project opens later). Never again once it closed (tour.done).
  const autoTried = useRef(false);
  useEffect(() => {
    if (!frameReady || autoTried.current || tourDone()) return;
    autoTried.current = true;
    const root = document.documentElement;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let watch: MutationObserver | undefined;
    let fired = false;
    const fire = () => { fired = true; watch?.disconnect(); if (!live.current.tour && !tourDone()) openTour(1); };
    const onIntroEnd = (e: Event) => {
      const skipped = !!(e as CustomEvent<{ skip?: boolean }>).detail?.skip;
      clearTimeout(timer);
      timer = setTimeout(fire, skipped ? 450 : 400);
    };
    const check = () => {
      if (!root.hasAttribute('data-intro')) { fire(); return; }
      window.addEventListener('gm-intro-end', onIntroEnd, { once: true });
      // The end event may already have gone (the attribute outlives it by the 1 s handoff):
      // the attribute leaving is the fallback signal.
      watch = new MutationObserver(() => { if (!root.hasAttribute('data-intro')) { clearTimeout(timer); fire(); } });
      watch.observe(root, { attributes: true, attributeFilter: ['data-intro'] });
    };
    const since = typeof performance !== 'undefined' ? performance.now() : 0;
    timer = setTimeout(check, Math.max(420, 3200 - since));
    return () => {
      clearTimeout(timer); watch?.disconnect(); window.removeEventListener('gm-intro-end', onIntroEnd);
      // Not opened yet (frame gone, or StrictMode's replay of the effect): try again next time.
      if (!fired) autoTried.current = false;
    };
  }, [frameReady, openTour]);

  const topics = useMemo(() => listedTopics(ctx), [ctx]);
  const isSeen = useCallback((t: GuideTopic) => isSeenTopic(t, seen), [seen]);
  const isNew = useCallback((t: GuideTopic) => isNewTopic(t, seen, from, appVersion), [seen, from, appVersion]);
  const coreSteps = useMemo(() => stepsOf(CORE_TOUR, ctx).length, [ctx]);

  return {
    guideOpen, openGuide, closeGuide, query, setQuery, focusTopic, clearFocusTopic: () => setFocusTopic(null),
    topics, isSeen, isNew, coreSteps,
    tour, openTour, playTopic, next, back, skip, go, setNameDraft,
    topic: tour?.topicId ? topicById(tour.topicId) ?? null : null,
  };
}

export type GuideApi = ReturnType<typeof useGuide>;
