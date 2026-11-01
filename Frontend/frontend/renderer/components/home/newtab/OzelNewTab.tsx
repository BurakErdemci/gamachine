import React from 'react';

import { useLang } from '../../../lib/i18n';
import { MascotHead } from '../BrandLogo';
import { Name, SiteButton, SiteIcon, type NewTabLayoutProps } from './parts';

/**
 * Özel: made only of the user's colours and font (the --u-* / --oz-* variables). A large question
 * in the user's own font, the mascot drawn in their colours beside it, and the sites set as one run
 * of large words to pick from, each underlined in the user's accent when reached.
 */
export const OzelNewTab = ({ sites, onPick }: NewTabLayoutProps) => {
  const { t } = useLang();
  return (
    <div className="nt" data-nt="ozel">
      <header className="nt-oz-head">
        <h2 className="nt-oz-title">{t('nt.ozel.title')}</h2>
        <MascotHead className="mascot nt-oz-mascot" />
      </header>
      {sites.length === 0
        ? <p className="nt-oz-empty">{t('bp.suggestedEmpty')}</p>
        : (
          <ul className="nt-oz-words">
            {sites.map(site => (
              <li key={site.label}>
                <SiteButton site={site} onPick={onPick} className="nt-oz-word">
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
