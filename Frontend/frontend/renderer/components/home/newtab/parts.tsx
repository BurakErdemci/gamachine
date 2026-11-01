import React from 'react';

import type { Site } from '../../../lib/browserSuggest';
import { SiteIcon } from '../BrowserTabs';

export { SiteIcon };

/** What every empty-tab layout gets: the most visited sites (suggestedSites) and what a pick does. */
export interface NewTabLayoutProps {
  sites: Site[];
  onPick: (url: string) => void;
}

/** suggestedSites' default cap: the Arena slot meter and the Pafta zones are drawn for this many. */
export const NT_SLOTS = 8;

/** The page under the host, for the small second line ("/game-assets"); empty for a site's root. */
export function pathOf(url: string): string {
  try {
    const { pathname, search } = new URL(url);
    return (pathname === '/' ? '' : pathname.replace(/\/$/, '')) + search;
  } catch {
    return '';
  }
}

/** Two-digit rank: the sites come most visited first, so the number is a real order. */
export const rank = (i: number) => String(i + 1).padStart(2, '0');

/**
 * The one control every layout uses for a site: a real button (keyboard reachable), the URL as its
 * title, the host in `.nt-name` so tests and screen readers read the same word in every theme.
 */
export function SiteButton({ site, onPick, className, children, style }: {
  site: Site;
  onPick: (url: string) => void;
  className: string;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <button type="button" className={`nt-site ${className}`} title={site.url} style={style} onClick={() => onPick(site.url)}>
      {children}
    </button>
  );
}

export const Name = ({ site, className = '' }: { site: Site; className?: string }) => (
  <span className={`nt-name ${className}`.trim()}>{site.label}</span>
);

export const Path = ({ site, className = '' }: { site: Site; className?: string }) => {
  const path = pathOf(site.url);
  return path ? <span className={`nt-path ${className}`.trim()}>{path}</span> : null;
};
