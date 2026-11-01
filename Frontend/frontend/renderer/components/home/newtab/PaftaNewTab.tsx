import React from 'react';

import { useLang } from '../../../lib/i18n';
import { Name, Path, SiteButton, SiteIcon, type NewTabLayoutProps } from './parts';

const COLS = ['1', '2', '3', '4'];
const ROWS = ['A', 'B', 'C'];

/**
 * Pafta: a technical drawing sheet. A framed sheet with zone marks on its edges; every site is a
 * detail drawn in thin outline with a circled callout number and its page written like a
 * dimension note; the title block in the bottom-right corner names the sheet, the count and the date.
 */
export const PaftaNewTab = ({ sites, onPick }: NewTabLayoutProps) => {
  const { t, lang } = useLang();
  const today = new Date().toLocaleDateString(lang === 'tr' ? 'tr-TR' : 'en-GB');
  return (
    <div className="nt" data-nt="pafta">
      <div className="nt-pf-sheet">
        <span className="nt-pf-zones is-top" aria-hidden="true">{COLS.map(c => <i key={c}>{c}</i>)}</span>
        <span className="nt-pf-zones is-left" aria-hidden="true">{ROWS.map(r => <i key={r}>{r}</i>)}</span>
        <div className="nt-pf-field">
          {sites.length === 0
            ? (
              <div className="nt-pf-empty">
                <span className="nt-pf-blank" aria-hidden="true" />
                <p className="nt-pf-empty-t">{t('nt.pafta.empty')}</p>
              </div>
            )
            : (
              <ul className="nt-pf-details">
                {sites.map((site, i) => (
                  <li key={site.label}>
                    <SiteButton site={site} onPick={onPick} className="nt-pf-detail">
                      <span className="nt-pf-callout num" aria-hidden="true">{i + 1}</span>
                      <SiteIcon favicon={site.favicon} label={site.label} />
                      <Name site={site} />
                      <Path site={site} />
                    </SiteButton>
                  </li>
                ))}
              </ul>
            )}
        </div>
        <dl className="nt-pf-tb">
          <div className="nt-pf-cell is-name"><dt>{t('nt.pafta.title')}</dt><dd>{t('nt.pafta.sheet')}</dd></div>
          <div className="nt-pf-cell"><dt>{t('nt.pafta.qty')}</dt><dd className="num">{sites.length}</dd></div>
          <div className="nt-pf-cell"><dt>{t('nt.pafta.date')}</dt><dd className="num">{today}</dd></div>
        </dl>
      </div>
    </div>
  );
};
