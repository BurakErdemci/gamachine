import React, { useEffect, useMemo, useRef } from 'react';

import { useLang } from '../../lib/i18n';
import { GROUPS, markHits, matches, searchWords, tx } from '../../lib/guide/registry';
import type { GuideTopic } from '../../lib/guide/types';
import { MascotHead } from './BrandLogo';

export interface GuideScreenProps {
  open: boolean;
  topics: GuideTopic[];
  query: string;
  onQuery: (q: string) => void;
  isSeen: (t: GuideTopic) => boolean;
  isNew: (t: GuideTopic) => boolean;
  coreSteps: number;
  onPlay: (id: string) => void;
  onTour: () => void;
  onClose: () => void;
  /** After a topic: its card gets the focus. '' = the search box; null = nothing to restore. */
  focusTopic: string | null;
  onFocused: () => void;
}

const Ic = ({ children, className = 'ic' }: { children: React.ReactNode; className?: string }) => (
  <svg className={className} viewBox="0 0 20 20" aria-hidden="true">{children}</svg>
);

/**
 * The guide (mockup round 12b screen 6, `main.guide`): topics on demand, in the main area beside
 * the kept sidebar and top bar. Listed = available in this build (registry rule 2); a group with
 * nothing listed is not drawn. Picking a topic plays only its steps over the real UI.
 */
export const GuideScreen: React.FC<GuideScreenProps> = ({
  open, topics, query, onQuery, isSeen, isNew, coreSteps, onPlay, onTour, onClose, focusTopic, onFocused,
}) => {
  const { t, lang } = useLang();
  const rootRef = useRef<HTMLElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const words = useMemo(() => searchWords(query, lang), [query, lang]);

  // Opening puts the cursor in the search box; coming back from a topic puts it on that topic.
  // `onFocused` clears focusTopic, which re-runs this effect; that second run (open unchanged,
  // topic just handled) must not fall through to the search box and steal the card's focus
  // (guide audit, 2 Oct 2026).
  const handled = useRef<{ open: boolean; topic: string | null }>({ open: false, topic: null });
  useEffect(() => {
    const before = handled.current;
    handled.current = { open, topic: focusTopic };
    if (!open) return;
    if (focusTopic == null && before.open && before.topic != null) return;
    const id = requestAnimationFrame(() => {
      const tile = focusTopic ? rootRef.current?.querySelector<HTMLElement>(`.gd-topic[data-topic="${focusTopic}"]`) : null;
      if (tile) tile.focus();
      else {
        if (rootRef.current && focusTopic == null) rootRef.current.scrollTop = 0;
        searchRef.current?.focus({ preventScroll: true });
      }
      if (focusTopic != null) onFocused();
    });
    return () => cancelAnimationFrame(id);
  }, [open, focusTopic]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null;

  const hit = (text: string) => markHits(text, words, lang).map((p, i) => (p.hit ? <mark key={i} className="gd-hit">{p.text}</mark> : <React.Fragment key={i}>{p.text}</React.Fragment>));
  const tile = (topic: GuideTopic) => {
    const seen = isSeen(topic);
    const n = topic.steps.length;
    return (
      <li key={topic.id}>
        <button type="button" className={`gd-topic${seen ? ' is-seen' : ''}`} data-topic={topic.id} onClick={() => onPlay(topic.id)}>
          <span className="gd-t-title">{hit(tx(topic.title, lang))}</span>
          <span className="gd-t-sum">{hit(tx(topic.summary, lang))}</span>
          <span className="gd-t-meta">
            <span className="gd-t-n">{t(n === 1 ? 'guide.stepOne' : 'guide.steps', { n })}</span>
            {seen && <span className="gd-seen"><Ic><path d="M4.5 10.5l3.5 3.5 7.5-8" /></Ic>{t('guide.seen')}</span>}
            <span className="gd-t-play" aria-hidden="true">{t('guide.play')}<Ic className="ic ic-sm"><path d="M8 5l5 5-5 5" /></Ic></span>
          </span>
          {isNew(topic) && <span className="gd-new">{t('guide.new')}</span>}
        </button>
      </li>
    );
  };
  const section = (id: string, title: string, sub: string, list: GuideTopic[]) => (
    <section key={id} className="gd-group" data-group={id}>
      <header className="gd-group-head">
        <h2 className="gd-gk">{title}</h2>
        <span className="gd-gn num">{list.length}</span>
        <p className="gd-gsub">{sub}</p>
      </header>
      <ul className="gd-list">{list.map(tile)}</ul>
    </section>
  );

  const groups: React.ReactNode[] = [];
  let shown = 0;
  if (!words.length) {
    const news = topics.filter(isNew);
    if (news.length) groups.push(section('new', t('guide.newGroup'), t('guide.newSub'), news));
  }
  for (const g of GROUPS) {
    const list = topics.filter(tp => tp.group === g.id && matches(tp, words, lang));
    if (!list.length) continue;
    shown += list.length;
    groups.push(section(g.id, tx(g.title, lang), tx(g.sub, lang), list));
  }
  const stat = words.length
    ? t(shown === 1 ? 'guide.foundOne' : 'guide.found', { n: shown })
    : t('guide.count', { n: topics.length, s: topics.filter(isSeen).length });

  return (
    <main ref={rootRef} className="guide paper" aria-labelledby="gd-title" data-testid="guide-screen" tabIndex={-1}>
      <div className="gd-wrap">
        <header className="gd-head">
          <MascotHead className="mascot gd-mascot" />
          <div className="gd-id">
            <p className="gd-kicker lex">
              <span className="lex-d">{t('guide.kicker')}</span>
              <span className="lex-q">{t('guide.kickerQuest')}</span>
              <span className="lex-p">{t('guide.kickerNote')}</span>
              <span className="lex-w">{t('guide.kickerShop')}</span>
            </p>
            <h1 className="gd-title" id="gd-title">{t('guide.title')}</h1>
            <p className="gd-lede">{t('guide.lede')}</p>
          </div>
          <button type="button" className="gd-back" onClick={onClose} data-testid="guide-back">
            <Ic><path d="M11.5 5.5L7 10l4.5 4.5M7.5 10H16" /></Ic>{t('guide.back')}<kbd>Esc</kbd>
          </button>
        </header>

        <div className="gd-tools">
          <label className="gd-search">
            <Ic className="ic ic-sm"><circle cx="9" cy="9" r="5" /><path d="M13 13l3.5 3.5" /></Ic>
            <input
              ref={searchRef}
              type="search"
              data-guide-search=""
              data-testid="guide-search"
              value={query}
              onChange={e => onQuery(e.target.value)}
              placeholder={t('guide.search')}
              aria-label={t('guide.searchLabel')}
              autoComplete="off"
            />
          </label>
          <p className="gd-stat" aria-live="polite" data-testid="guide-stat">{stat}</p>
        </div>

        <section className="gd-core" aria-label={t('guide.core')}>
          <Ic className="ic gd-core-ic"><path d="M4 10a6 6 0 1 0 1.8-4.3" /><path d="M4 4v3.5h3.5" /></Ic>
          <span className="gd-core-t"><b>{t('guide.core')}</b><span>{t('guide.coreText', { n: coreSteps })}</span></span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onTour} data-testid="guide-core-replay">{t('guide.coreReplay')}</button>
        </section>

        <div className="gd-groups">{groups}</div>
        {!shown && <p className="gd-none" data-testid="guide-none">{t('guide.none', { q: query.trim() })}</p>}
        <p className="gd-foot">{t('guide.foot')}</p>
      </div>
    </main>
  );
};
