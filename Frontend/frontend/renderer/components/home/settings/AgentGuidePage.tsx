import React, { useEffect, useRef, useState } from 'react';

import { useLang, type Lang, type TKey } from '../../../lib/i18n';
import { SetCard, SetGroup, SetPageHead } from './controls';

type GuideWhen = 'always' | 'project_open' | 'connected' | 'off' | 'not_responding';
type GuideSection = { id: string; when: string; text: string };
const WHEN_KEYS: Record<GuideWhen, TKey> = {
  always: 'set.ajan.when.always',
  project_open: 'set.ajan.when.project_open',
  connected: 'set.ajan.when.connected',
  off: 'set.ajan.when.off',
  not_responding: 'set.ajan.when.not_responding',
};

// The addendum belongs to the app session, so navigation and language changes keep it.
let cachedDraft: string | null = null;
let pendingSave: Promise<string> | null = null;

export const __resetAgentGuideCacheForTests = () => {
  cachedDraft = null;
  pendingSave = null;
};

const isGuideSection = (value: unknown): value is GuideSection => {
  if (!value || typeof value !== 'object') return false;
  const section = value as Partial<GuideSection>;
  return typeof section.id === 'string' && typeof section.when === 'string' && typeof section.text === 'string';
};

export interface AgentGuidePageProps {
  lang: Lang;
  API?: string;
  http?: { get: (...a: any[]) => Promise<any>; put?: (...a: any[]) => Promise<any> };
  token?: string | null;
  saved: () => void;
}

export const AgentGuidePage = ({ lang, API, http, token, saved }: AgentGuidePageProps) => {
  const { t } = useLang();
  const [sections, setSections] = useState<GuideSection[]>([]);
  const [draft, setDraft] = useState('');
  const [savedValue, setSavedValue] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [retry, setRetry] = useState(0);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!API || !http) return;
    let active = true;
    setLoading(true);
    setLoadError(false);
    setSaveError(false);
    const load = async () => {
      try {
        if (pendingSave) await pendingSave.catch(() => {});
        if (!active) return;
        const res = await http.get(`${API}/agent-guide?lang=${lang}`, {
          headers: { 'X-Session-Token': token ?? '' },
        });
        if (!active) return;
        if (!Array.isArray(res?.data?.sections) || !res.data.sections.every(isGuideSection)
          || typeof res.data.addendum !== 'string') throw new Error('Invalid agent guide response');
        setSections(res.data.sections);
        setDraft(cachedDraft ?? res.data.addendum);
        setSavedValue(res.data.addendum);
      } catch {
        if (active) setLoadError(true);
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => { active = false; };
  }, [API, http, lang, token, retry]);

  const save = async () => {
    if (!API || !http || saving || draft.trim() === savedValue) return;
    setSaveError(false);
    if (!http.put) { setSaveError(true); return; }
    setSaving(true);
    let request: Promise<string> | null = null;
    try {
      request = http.put(`${API}/agent-guide/addendum`, { text: draft }, {
        headers: { 'X-Session-Token': token ?? '' },
      }).then(res => {
        if (typeof res?.data?.addendum !== 'string') throw new Error('Invalid addendum response');
        cachedDraft = null;
        return res.data.addendum;
      });
      pendingSave = request;
      const addendum = await request;
      if (!mounted.current) return;
      setDraft(addendum);
      setSavedValue(addendum);
      saved();
    } catch {
      if (mounted.current) setSaveError(true);
    } finally {
      if (pendingSave === request) pendingSave = null;
      if (mounted.current) setSaving(false);
    }
  };

  const head = <SetPageHead title={t('set.nav.ajan')} lede={t('set.lede.ajan')} />;
  if (!API || !http) return <>{head}<p className="set-hint">{t('set.ajan.unavailable')}</p></>;
  if (loading) return <>{head}<p className="set-hint" role="status">{t('set.ajan.loading')}</p></>;
  if (loadError) return <>{head}
    <p className="set-msg set-msg-err">{t('set.ajan.loadError')}</p>
    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setRetry(n => n + 1)}>{t('set.ajan.retry')}</button>
  </>;

  return <>{head}
    <SetGroup title={t('set.ajan.base')} note={t('set.ajan.baseNote')}>
      <SetCard>
        {sections.map(section => (
          <div key={section.id} className="set-agent-section" data-testid={`agent-guide-section-${section.id}`}>
            <p className="set-agent-label">{Object.prototype.hasOwnProperty.call(WHEN_KEYS, section.when)
              ? t(WHEN_KEYS[section.when as GuideWhen]) : section.when}</p>
            <div className="set-agent-text">{section.text}</div>
          </div>
        ))}
      </SetCard>
    </SetGroup>
    <SetGroup title={t('set.ajan.addendum')} note={t('set.ajan.addendumNote')}>
      <textarea className="set-input set-agent-addendum" data-testid="agent-guide-addendum"
        aria-label={t('set.ajan.addendum')} rows={8} maxLength={4000}
        value={draft} placeholder={t('set.ajan.placeholder')} disabled={saving}
        onChange={e => { cachedDraft = e.target.value; setDraft(e.target.value); setSaveError(false); }} />
      <div className="set-agent-actions">
        <span className="num" data-testid="agent-guide-count">{draft.length} / 4000</span>
        <button type="button" className="btn btn-primary btn-sm" data-testid="agent-guide-save"
          disabled={saving || draft.trim() === savedValue} onClick={() => void save()}>{t('set.ajan.save')}</button>
      </div>
      {saveError && <p className="set-msg set-msg-err" role="alert">{t('set.ajan.saveError')}</p>}
    </SetGroup>
  </>;
};
