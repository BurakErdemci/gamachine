import React from 'react';

import { useLang } from '../../../lib/i18n';
import { Name, Path, rank, SiteButton, SiteIcon, type NewTabLayoutProps } from './parts';

// Hung by hand: each ticket leans a little, never two the same way in a row.
const TILTS = [-1.4, 0.9, -0.5, 1.3, -1, 0.6, -0.8, 1.1];

/**
 * Atölye: work orders on a workshop pegboard. A brass rail names the board; every site is a paper
 * ticket hung on a peg, with a perforated stub holding its order number. A picked-up ticket
 * straightens. With nothing to do, one blank ticket hangs on the rail.
 */
export const AtolyeNewTab = ({ sites, onPick }: NewTabLayoutProps) => {
  const { t } = useLang();
  return (
    <div className="nt" data-nt="atolye">
      <div className="nt-at-rail"><h2 className="nt-at-title">{t('nt.atolye.title')}</h2></div>
      {sites.length === 0
        ? (
          <div className="nt-at-empty">
            <div className="nt-at-ticket is-blank" aria-hidden="true">
              <span className="nt-at-peg" />
              <span className="nt-at-stub" />
              <span className="nt-at-lines" />
            </div>
            <p className="nt-at-empty-t">{t('nt.atolye.empty')}</p>
            <p className="nt-at-empty-h">{t('bp.suggestedEmpty')}</p>
          </div>
        )
        : (
          <ul className="nt-at-board">
            {sites.map((site, i) => (
              <li key={site.label}>
                <SiteButton site={site} onPick={onPick} className="nt-at-ticket"
                  style={{ ['--tilt' as string]: `${TILTS[i % TILTS.length]}deg` }}>
                  <span className="nt-at-peg" aria-hidden="true" />
                  <span className="nt-at-stub num" aria-hidden="true">{t('nt.atolye.no', { no: rank(i) })}</span>
                  <span className="nt-at-body">
                    <span className="nt-at-head"><SiteIcon favicon={site.favicon} label={site.label} /><Name site={site} /></span>
                    <Path site={site} />
                  </span>
                </SiteButton>
              </li>
            ))}
          </ul>
        )}
    </div>
  );
};
