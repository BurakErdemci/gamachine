import React, { useEffect, useRef, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { displayAddress } from '../../lib/browserAddress';
import { shortcutLabel, type Site, type Suggestion } from '../../lib/browserSuggest';

export interface BrowserTab {
  id: number;
  url: string;
  title: string;
  loading: boolean;
  favicon: string;
  /** The one tab the agent's tools reach. */
  agent: boolean;
}

const AGENT_ICON = (
  <svg className="bp-agent-ic" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1l1.2 3.3L10.5 5.5 7.2 6.7 6 10 4.8 6.7 1.5 5.5 4.8 4.3z" /></svg>
);

/** The site's own icon when it loads; the first letter of its name until then or when it does not. */
export function SiteIcon({ favicon, label }: { favicon: string; label: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [favicon]);
  return (
    <span className="bp-ico" aria-hidden="true">
      {favicon.startsWith('data:image/') && !failed
        ? <img src={favicon} alt="" width={14} height={14} referrerPolicy="no-referrer" onError={() => setFailed(true)} />
        : <span className="bp-ico-ch">{(label.trim()[0] || '·').toUpperCase()}</span>}
    </span>
  );
}

export function BrowserTabs({ tabs, activeId, mac, onSelect, onClose, onNew }: {
  tabs: BrowserTab[];
  activeId: number | null;
  mac: boolean;
  onSelect: (id: number) => void;
  onClose: (id: number) => void;
  onNew: () => void;
}) {
  const { t } = useLang();
  const activeRef = useRef<HTMLDivElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  // Keeps the selected tab inside the strip, whose scrollbar is hidden.
  useEffect(() => {
    const strip = stripRef.current;
    const tab = activeRef.current;
    if (!strip || !tab) return;
    if (tab.offsetLeft < strip.scrollLeft) strip.scrollLeft = tab.offsetLeft;
    else if (tab.offsetLeft + tab.offsetWidth > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = tab.offsetLeft + tab.offsetWidth - strip.clientWidth;
  }, [activeId, tabs.length]);
  return (
    <div className="bp-tabs" role="tablist" aria-label={t('bp.tabs')} ref={stripRef}>
      {tabs.map(tab => {
        const label = tab.title || (tab.url && tab.url !== 'about:blank' ? displayAddress(tab.url) : t('bp.newTab'));
        const active = tab.id === activeId;
        return (
          <div key={tab.id} ref={active ? activeRef : undefined} className="bp-tab" data-active={active ? 'true' : 'false'}
            data-agent={tab.agent ? 'true' : 'false'} data-loading={tab.loading ? 'true' : 'false'}>
            <button type="button" role="tab" className="bp-tab-main" aria-selected={active} title={tab.agent ? `${label} · ${t('bp.agentTab')}` : label}
              onClick={() => onSelect(tab.id)}>
              <SiteIcon favicon={tab.favicon} label={label} />
              <span className="bp-tab-t">{label}</span>
              {tab.agent && <span className="bp-tab-agent" aria-label={t('bp.agentTab')}>{AGENT_ICON}<span>{t('bp.agentBadge')}</span></span>}
            </button>
            <button type="button" className="bp-tab-x" aria-label={`${t('bp.closeTab')}: ${label}`}
              title={`${t('bp.closeTab')} (${shortcutLabel('W', mac)})`} onClick={() => onClose(tab.id)}>×</button>
          </div>
        );
      })}
      <button type="button" className="bp-tab-new" aria-label={t('bp.newTab')} title={`${t('bp.newTab')} (${shortcutLabel('T', mac)})`}
        onClick={onNew}>+</button>
    </div>
  );
}

/** The rows under the address bar. Mouse down picks (before the input loses focus). */
export function SuggestionList({ id, items, active, onPick }: {
  id: string;
  items: Suggestion[];
  active: number;
  onPick: (url: string) => void;
}) {
  const { t } = useLang();
  return (
    <ul className="bp-suggest" id={id} role="listbox" aria-label={t('bp.suggestions')}>
      {items.map((item, i) => (
        <li key={`${item.kind}:${item.url}`} id={`${id}-${i}`} role="option" aria-selected={i === active} data-kind={item.kind}
          className="bp-suggest-row" data-active={i === active ? 'true' : 'false'}
          onMouseDown={e => { e.preventDefault(); onPick(item.url); }}>
          {item.kind === 'history'
            ? <SiteIcon favicon={item.favicon} label={item.text} />
            : <span className="bp-ico bp-ico-search" aria-hidden="true">{item.kind === 'search' ? '⌕' : '↗'}</span>}
          <span className="bp-suggest-t">
            {item.kind === 'history' ? item.text : t(item.kind === 'search' ? 'bp.suggestSearch' : 'bp.suggestOpen', { q: item.text })}
          </span>
          {item.kind === 'history' && <span className="bp-suggest-u">{displayAddress(item.url)}</span>}
        </li>
      ))}
    </ul>
  );
}

/** "Önerilen": tiles of the sites visited most, on a tab that has no page yet. */
export function NewTabGrid({ sites, onPick }: { sites: Site[]; onPick: (url: string) => void }) {
  const { t } = useLang();
  return (
    <div className="bp-grid-wrap">
      <p className="bp-grid-title">{t('bp.suggested')}</p>
      {sites.length === 0
        ? <p className="bp-grid-empty">{t('bp.suggestedEmpty')}</p>
        : (
          <ul className="bp-grid">
            {sites.map(site => (
              <li key={site.label}>
                <button type="button" className="bp-tile" title={site.url} onClick={() => onPick(site.url)}>
                  <SiteIcon favicon={site.favicon} label={site.label} />
                  <span className="bp-tile-t">{site.label}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
    </div>
  );
}
