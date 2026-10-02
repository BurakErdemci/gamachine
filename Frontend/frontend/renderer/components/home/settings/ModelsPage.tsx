import React, { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';

import { useLang, type TKey } from '../../../lib/i18n';
import { stripBidi, shortModelId } from '../../../lib/modelText';
import { GROUP_USAGE_FAMILY, familyFor, type UsageLimits } from '../../../lib/usageLimits';
import { useCliDoctor } from '../../../hooks/home/useCliDoctor';
import { useUsageLimits } from '../../../hooks/home/useUsageLimits';
import type { AvailableModelsState } from '../../../hooks/home/useAIConfig';
import { ModelLogo } from '../../ui/ModelLogos';
import { confirmDialog } from '../../ui/ConfirmDialog';
import { UseBlock } from '../UsageMeters';
import type { AIConfig } from '../types';
import {
  API_KEY_PROVIDERS, CLI_GROUPS, CLOUD_PROVIDER_META, chipModelName, type CliGroupDef, type ModelItem,
} from '../providerGroups';
import { Chev, Lamp, SetCard, SetGroup, SetPageHead, SetRow } from './controls';

type Toast = (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;

export interface ModelsPageProps {
  aiConfig: AIConfig;
  availableModels?: AvailableModelsState | { local: ModelItem[]; cloud: ModelItem[]; subscription: ModelItem[] };
  providersWithKeys: string[];
  /** The default for a new chat; null until read (the chat on screen's pair is shown meanwhile). */
  defaultModel?: { provider_type: string; model_name: string } | null;
  onSaveDefaultModel?: (provider: string, model: string) => Promise<boolean>;
  onSaveApiKey?: (provider: string, key: string) => Promise<boolean>;
  onDeleteKey: (provider: string) => Promise<boolean | void>;
  onUseCustomModel?: (model: string) => Promise<boolean>;
  usage?: UsageLimits | null;
  API?: string;
  http?: { get: (...a: any[]) => Promise<any>; post: (...a: any[]) => Promise<any> };
  token?: string | null;
  showToast?: Toast;
  saved: () => void;
}

// The groups that always show; the rest fold under "N more" unless signed in.
const MAIN_GROUPS = new Set(['claude', 'codex', 'gemini']);
const SEP = '\u0000';
const noHttp = { get: async () => ({ data: null }), post: async () => ({ data: null }) };

export const ModelsPage = ({
  aiConfig, availableModels, providersWithKeys, defaultModel, onSaveDefaultModel, onSaveApiKey,
  onDeleteKey, onUseCustomModel, usage = null, API = '', http, token, showToast, saved,
}: ModelsPageProps) => {
  const { t } = useLang();
  const goster = (s?: string) => stripBidi(s || '');
  const toast: Toast = showToast ?? (() => {});
  const cli = useCliDoctor({ API, http: http ?? noHttp, token, showToast: toast, t });
  const pageUsage = useUsageLimits({ api: API, token, http, enabled: !!API, menuOpen: true });
  const models = availableModels ?? { local: [], cloud: [], subscription: [] };

  // Dynamic CLI lists (Codex, Copilot, Cursor, OpenCode) for the default-model list. Asked only
  // for a CLI that is installed and signed in: listing spawns the CLI.
  const [dyn, setDyn] = useState<Record<string, ModelItem[]>>({});
  useEffect(() => { if (API && http) void cli.fetchDoctor(); }, [API, http]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!API || !http || !cli.doctor) return;
    for (const g of CLI_GROUPS) {
      if (!g.dynamic || dyn[g.dynamic] || cli.isLoggedIn(g) !== true) continue;
      const name = g.dynamic;
      http.get(`${API}/cli-models/${name}`, { headers: { 'X-Session-Token': token ?? '' } })
        .then((res: any) => setDyn(prev => ({ ...prev, [name]: Array.isArray(res?.data?.models) ? res.data.models : [] })))
        .catch(() => setDyn(prev => ({ ...prev, [name]: [] })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API, http, cli.doctor]);

  const groupModels = (g: CliGroupDef): ModelItem[] =>
    g.dynamic ? (dyn[g.dynamic] || []) : (models.subscription || []).filter(m => g.matches(m.id));

  const cloudGroups = useMemo(() => {
    const order: string[] = [];
    const map: Record<string, ModelItem[]> = {};
    for (const m of models.cloud || []) {
      const p = m.provider || 'other';
      if (!map[p]) { map[p] = []; order.push(p); }
      map[p].push(m);
    }
    return order.map(p => ({ provider: p, label: CLOUD_PROVIDER_META[p]?.label || goster(p), models: map[p] }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [models.cloud]);

  // ── default model ──────────────────────────────────────────────
  const def = defaultModel ?? { provider_type: aiConfig.provider_type, model_name: aiConfig.model_name };
  const defValue = `${def.provider_type}${SEP}${def.model_name}`;
  const options: { group: string; items: { value: string; label: string }[] }[] = [
    ...CLI_GROUPS.map(g => ({
      group: g.label,
      // The group label already names the CLI: "(CLI)" / "Codex (…)" would repeat it.
      items: groupModels(g).filter(m => !m.disabled).map(m => ({ value: `subscription${SEP}${m.id}`, label: chipModelName(goster(m.name)) })),
    })),
    ...cloudGroups.map(c => ({
      group: c.label,
      items: c.models.map(m => ({ value: `${m.provider}${SEP}${m.id}`, label: goster(m.name) })),
    })),
    { group: 'Ollama', items: (models.local || []).map(m => ({ value: `ollama${SEP}${m.id}`, label: goster(m.name) })) },
  ].filter(o => o.items.length > 0);
  const known = options.some(o => o.items.some(i => i.value === defValue));
  const defGroup = CLI_GROUPS.find(g => def.provider_type === 'subscription' && g.matches(def.model_name || ''));
  const defBrand = defGroup ? defGroup.brand : def.provider_type;

  const pickDefault = async (value: string) => {
    const [provider, ...rest] = value.split(SEP);
    const model = rest.join(SEP);
    if (!provider || !model || !onSaveDefaultModel) return;
    if (cloudGroups.some(g => g.provider === provider) && !providersWithKeys.includes(provider)) {
      toast(`${goster(provider)} ${t('models.apiKeyNeeded')}`, 'warning');
      startEdit(provider);
      return;
    }
    if (await onSaveDefaultModel(provider, model)) saved();
  };

  // ── subscriptions ──────────────────────────────────────────────
  const subRow = (g: CliGroupDef) => {
    const loggedIn = cli.isLoggedIn(g);
    const installed = cli.isInstalled(g);
    const needsLogin = cli.needsLogin(g);
    const limits = pageUsage.data ?? usage;
    const fam = familyFor(limits, GROUP_USAGE_FAMILY[g.key]);
    const count = groupModels(g).length;
    const parts: string[] = [];
    let tone: 'ok' | 'warn' | undefined;
    if (!cli.doctor) parts.push(t('set.cli.checking'));
    else if (!installed) parts.push(t('set.cli.notInstalled'));
    else if (needsLogin) { parts.push(t('set.cli.notLoggedIn')); tone = 'warn'; }
    else if (loggedIn) {
      tone = 'ok';
      parts.push(t('set.cli.loggedIn'));
      if (fam?.plan) parts.push(t('set.cli.plan', { plan: goster(fam.plan) }));
      if (count > 0) parts.push(t('set.cli.models', { sayi: count }));
    }
    const control = !cli.doctor ? null
      : !installed ? (
        <button type="button" className="btn btn-ghost btn-sm" data-testid={`cli-install-${g.key}`}
          title={t('models.installHint', { cli: g.cliLabel })} disabled={cli.busyCli === g.key}
          onClick={() => cli.installCli(g)}>
          {cli.busyCli === g.key && <Loader2 size={13} className="animate-spin" />}{t('set.cli.install')}
        </button>
      ) : needsLogin ? (
        <button type="button" className="btn btn-ghost btn-sm" data-testid={`cli-login-${g.key}`}
          title={t('models.loginHint', { cli: g.cliLabel })} disabled={cli.busyCli === g.key}
          onClick={() => cli.loginCli(g)}>
          {cli.busyCli === g.key && <Loader2 size={13} className="animate-spin" />}{t('set.cli.login')}
        </button>
      ) : <UseBlock fam={fam} nowIso={limits?.now} />;
    return (
      <SetRow
        key={g.key}
        testId={`sub-row-${g.key}`}
        lead={<ModelLogo provider={g.brand} size={24} className="plogo plogo-lg" />}
        name={g.label}
        nameLang="en"
        hint={<><Lamp tone={tone} />{parts.join(' · ')}</>}
        control={control}
      />
    );
  };
  const mainSubs = CLI_GROUPS.filter(g => MAIN_GROUPS.has(g.key) || cli.isLoggedIn(g) === true || cli.needsLogin(g));
  const moreSubs = CLI_GROUPS.filter(g => !mainSubs.includes(g));

  // ── API keys ───────────────────────────────────────────────────
  const [editing, setEditing] = useState<string | null>(null);
  const [keyInput, setKeyInput] = useState('');
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const startEdit = (p: string) => { setEditing(p); setKeyInput(''); };
  const cancelEdit = () => { setEditing(null); setKeyInput(''); };
  const commitKey = async (p: string) => {
    if (!onSaveApiKey || !keyInput.trim()) return;
    setBusyKey(p);
    const ok = await onSaveApiKey(p, keyInput);
    setBusyKey(null);
    if (ok) { setEditing(null); setKeyInput(''); saved(); }
  };
  const deleteKey = async (p: string) => {
    const label = API_KEY_PROVIDERS.find(provider => provider.value === p)?.label || goster(p);
    if (!(await confirmDialog(t('set.key.deleteConfirm', { saglayici: label }), t('set.key.delete'), t('confirm.cancel')))) return;
    setBusyKey(p);
    try { if (await onDeleteKey(p) === true) saved(); } finally { setBusyKey(null); }
  };
  const keyRow = (p: { value: string; label: string; badge?: string }) => {
    const has = providersWithKeys.includes(p.value);
    const isEditing = editing === p.value;
    const inputLabel = t('set.key.inputLabel', { saglayici: p.label });
    const hint = isEditing ? undefined : (
      <>
        <Lamp tone={has ? 'ok' : undefined} />
        {has ? t('set.key.saved') : t('set.key.none')}
        {p.badge && !has ? ` · ${t(p.badge as TKey)}` : ''}
      </>
    );
    const field = isEditing ? (
      <form className="set-field" onSubmit={e => { e.preventDefault(); void commitKey(p.value); }}>
        <input
          className="set-input"
          type="password"
          autoFocus
          autoComplete="off"
          value={keyInput}
          onChange={e => setKeyInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Escape') { e.preventDefault(); e.currentTarget.blur(); cancelEdit(); }
          }}
          placeholder={has ? t('settings.savedKeyPlaceholder') : t('settings.apiKeyPlaceholder')}
          aria-label={inputLabel}
          data-testid={`key-input-${p.value}`}
        />
        <button type="submit" className="btn btn-primary btn-sm" data-testid={`key-save-${p.value}`}
          disabled={!keyInput.trim() || busyKey === p.value}>{t('set.key.save')}</button>
        <button type="button" className="set-link" onClick={cancelEdit}>{t('set.key.cancel')}</button>
      </form>
    ) : undefined;
    const control = isEditing ? undefined : has ? (
      <span className="set-acts">
        <button type="button" className="set-link" data-testid={`key-change-${p.value}`} onClick={() => startEdit(p.value)}>{t('set.key.change')}</button>
        <button type="button" className="set-link set-link-danger" data-testid={`key-delete-${p.value}`}
          disabled={busyKey === p.value} onClick={() => deleteKey(p.value)}>{t('set.key.delete')}</button>
      </span>
    ) : (
      <button type="button" className="btn btn-ghost btn-sm" data-testid={`key-add-${p.value}`} onClick={() => startEdit(p.value)}>
        <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><circle cx="7" cy="12" r="3.2" /><path d="M9.3 9.7L16 3M13.5 5.5l2 2" /></svg>
        {t('set.key.add')}
      </button>
    );
    return (
      <SetRow
        key={p.value}
        testId={`key-row-${p.value}`}
        className={isEditing ? 'set-prov-edit' : ''}
        lead={<ModelLogo provider={p.value} size={24} className="plogo plogo-lg" />}
        name={p.label}
        nameLang="en"
        hint={hint}
        extra={field}
        control={control}
      />
    );
  };
  const mainKeys = API_KEY_PROVIDERS.filter(p =>
    providersWithKeys.includes(p.value) || p.value === aiConfig.provider_type || p.value === editing);
  const moreKeys = API_KEY_PROVIDERS.filter(p => !mainKeys.includes(p));

  // ── custom model id ────────────────────────────────────────────
  const [custom, setCustom] = useState('');
  const [customBusy, setCustomBusy] = useState(false);
  const useCustom = async () => {
    if (!onUseCustomModel || !custom.trim()) return;
    setCustomBusy(true);
    const ok = await onUseCustomModel(custom);
    setCustomBusy(false);
    if (ok) { setCustom(''); saved(); }
  };
  const localCount = (models.local || []).length;

  return (
    <>
      <SetPageHead title={t('set.nav.modeller')} lede={t('set.lede.modeller')} />

      <SetGroup title={t('set.group.newChat')}>
        <SetCard>
          <SetRow
            name={t('set.defaultModel')}
            hint={t('set.defaultModelHint')}
            control={(
              <span className="set-select set-model">
                <ModelLogo provider={defBrand} size={18} className="plogo" />
                <select
                  value={defValue}
                  aria-label={t('set.defaultModel')}
                  data-testid="default-model-select"
                  disabled={!onSaveDefaultModel}
                  onChange={e => { void pickDefault(e.target.value); }}
                >
                  {!known && <option value={defValue}>{def.model_name ? shortModelId(goster(def.model_name)) : t('models.select')}</option>}
                  {options.map(o => (
                    <optgroup key={o.group} label={o.group}>
                      {o.items.map(i => <option key={i.value} value={i.value}>{i.label}</option>)}
                    </optgroup>
                  ))}
                </select>
                <Chev />
              </span>
            )}
          />
        </SetCard>
      </SetGroup>

      <SetGroup title={t('set.group.subs')} note={t('set.group.subsNote')} testId="set-subs">
        <SetCard>
          {mainSubs.map(subRow)}
          {moreSubs.length > 0 && (
            <details className="set-more">
              <summary className="set-row set-more-sum">
                <span className="set-more-t">
                  {t('set.moreSubs', { sayi: moreSubs.length })}
                  <span className="set-more-names" lang="en">{moreSubs.map(g => g.label).join(' · ')}</span>
                </span>
                <Chev />
              </summary>
              {moreSubs.map(subRow)}
            </details>
          )}
        </SetCard>
      </SetGroup>

      <SetGroup title={t('set.group.keys')} note={t('set.group.keysNote')} testId="set-keys">
        <SetCard>
          {mainKeys.map(keyRow)}
          {moreKeys.length > 0 && (
            <details className="set-more">
              <summary className="set-row set-more-sum">
                <span className="set-more-t">
                  {t('set.moreProviders', { sayi: moreKeys.length })}
                  <span className="set-more-names" lang="en">{moreKeys.map(p => p.label).join(' · ')}</span>
                </span>
                <Chev />
              </summary>
              {moreKeys.map(keyRow)}
            </details>
          )}
        </SetCard>
      </SetGroup>

      <SetGroup title={t('set.group.local')}>
        <SetCard>
          <SetRow
            testId="set-ollama"
            lead={<ModelLogo provider="ollama" size={24} className="plogo plogo-lg" />}
            name="Ollama"
            nameLang="en"
            hint={<><Lamp tone={localCount > 0 ? 'ok' : undefined} />{localCount > 0 ? t('set.ollama.models', { sayi: localCount }) : t('models.noLocal')}</>}
            control={<span className="use-note">{t('use.unlimited')}</span>}
          />
        </SetCard>
      </SetGroup>

      <details className="set-group set-adv">
        <summary className="set-gk set-adv-sum">{t('set.advanced')} <Chev /></summary>
        <SetCard>
          <SetRow
            name={t('set.customModel')}
            hint={t('set.customModelHint', { saglayici: goster(aiConfig.provider_type) })}
            control={(
              <form className="set-field" onSubmit={e => { e.preventDefault(); void useCustom(); }}>
                <input
                  className="set-input set-input-mono"
                  type="text"
                  value={custom}
                  onChange={e => setCustom(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Escape') { e.preventDefault(); setCustom(''); e.currentTarget.blur(); }
                  }}
                  placeholder={goster(aiConfig.model_name) || t('settings.modelPlaceholder')}
                  aria-label={t('set.customModel')}
                  data-testid="custom-model-input"
                />
                <button type="submit" className="btn btn-ghost btn-sm" data-testid="custom-model-use"
                  disabled={!custom.trim() || customBusy || !onUseCustomModel}>{t('set.customModelUse')}</button>
              </form>
            )}
          />
        </SetCard>
      </details>
    </>
  );
};
