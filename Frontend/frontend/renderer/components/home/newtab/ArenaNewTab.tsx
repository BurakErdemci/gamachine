import React from 'react';

import { useLang } from '../../../lib/i18n';
import { MascotHead } from '../BrandLogo';
import { Name, NT_SLOTS, Path, rank, SiteButton, SiteIcon, type NewTabLayoutProps } from './parts';

const PLAY = <svg className="nt-ar-play" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 1.5v9l7.5-4.5z" /></svg>;

/**
 * Arena: a game's stage-select screen. The most visited site is the highlighted stage with a Start
 * plate; the rest are chamfered stage tiles with their rank. A slot meter on the HUD bar shows how
 * many of the eight stages are open. The mascot only appears when no stage is open yet.
 */
export const ArenaNewTab = ({ sites, onPick }: NewTabLayoutProps) => {
  const { t } = useLang();
  const [first, ...rest] = sites;
  return (
    <div className="nt" data-nt="arena">
      <header className="nt-ar-hud">
        <h2 className="nt-ar-title">{t('nt.arena.title')}</h2>
        <span className="nt-ar-meter" title={t('nt.arena.slots', { n: sites.length, max: NT_SLOTS })}>
          <span className="nt-ar-pips" aria-hidden="true">
            {Array.from({ length: NT_SLOTS }, (_, i) => <i key={i} className={i < sites.length ? 'is-on' : undefined} />)}
          </span>
          <span className="nt-ar-count num">{sites.length}/{NT_SLOTS}</span>
        </span>
      </header>
      {!first ? (
        <div className="nt-ar-empty">
          <span className="nt-ar-empty-slot" aria-hidden="true"><MascotHead className="mascot nt-ar-mascot" /></span>
          <p className="nt-ar-empty-t">{t('nt.arena.empty')}</p>
          <p className="nt-ar-empty-h">{t('nt.arena.emptyHint')}</p>
        </div>
      ) : (
        <ul className="nt-ar-stages">
          <li className="nt-ar-feature">
            <SiteButton site={first} onPick={onPick} className="nt-ar-hero">
              <span className="nt-ar-rank num" aria-hidden="true">{rank(0)}</span>
              <SiteIcon favicon={first.favicon} label={first.label} />
              <span className="nt-ar-hero-text">
                <Name site={first} className="nt-ar-hero-name" />
                <Path site={first} />
              </span>
              <span className="nt-ar-start" aria-hidden="true">{PLAY}{t('nt.arena.start')}</span>
            </SiteButton>
          </li>
          {rest.map((site, i) => (
            <li key={site.label}>
              <SiteButton site={site} onPick={onPick} className="nt-ar-stage">
                <span className="nt-ar-rank num" aria-hidden="true">{rank(i + 1)}</span>
                <SiteIcon favicon={site.favicon} label={site.label} />
                <Name site={site} />
              </SiteButton>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};
