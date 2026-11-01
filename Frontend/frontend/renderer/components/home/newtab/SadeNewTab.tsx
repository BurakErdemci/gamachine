import React from 'react';

import { useLang } from '../../../lib/i18n';
import { Name, Path, SiteButton, SiteIcon, type NewTabLayoutProps } from './parts';

/**
 * Sade: an editor's welcome page. One left-aligned column: a heading with the accent rule under it,
 * a quiet hint, then the sites as hairline-separated rows (host, then its page in the code font).
 * No game ornaments and no mascot (Sade hides them).
 */
export const SadeNewTab = ({ sites, onPick }: NewTabLayoutProps) => {
  const { t } = useLang();
  return (
    <div className="nt" data-nt="sade">
      <div className="nt-sd-col">
        <h2 className="nt-sd-title">{t('bp.suggested')}</h2>
        <p className="nt-sd-hint">{t('nt.sade.hint')}</p>
        {sites.length === 0
          ? <p className="nt-sd-empty">{t('bp.suggestedEmpty')}</p>
          : (
            <ul className="nt-sd-list">
              {sites.map(site => (
                <li key={site.label}>
                  <SiteButton site={site} onPick={onPick} className="nt-sd-row">
                    <SiteIcon favicon={site.favicon} label={site.label} />
                    <Name site={site} />
                    <Path site={site} />
                  </SiteButton>
                </li>
              ))}
            </ul>
          )}
      </div>
    </div>
  );
};
