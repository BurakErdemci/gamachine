import React, { useState, useEffect, useMemo, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Search, Check, AlertTriangle, Loader2, Download, LogIn, RefreshCw, LayoutGrid } from 'lucide-react';

import { ModelLogo } from '../ui/ModelLogos';
import { AIConfig, UserData } from './types';
import { useLang } from '../../lib/i18n';
import { apiHataMesaji } from '../../lib/apiError';
import { stripBidi } from '../../lib/modelText';
import type { AvailableModelsState } from '../../hooks/home/useAIConfig';
import { GROUP_USAGE_FAMILY, familyFor, minutesSince, type UsageLimits } from '../../lib/usageLimits';
import { ChipUse, UseBlock, UsePair } from './UsageMeters';
import {
  CLI_GROUPS, CLOUD_PROVIDER_META, activeProviderKey, chipModelName,
  type CliGroupDef, type ModelItem,
} from './providerGroups';
import type { SettingsPage } from './settings/pages';
import type { ThinkingLevel } from './ControlPanel';
import { useCliDoctor } from '../../hooks/home/useCliDoctor';

// Kept as exports of this module: messageAgent.ts and older callers import them from here.
export { CLI_GROUPS, CLOUD_PROVIDER_META };

interface ModelSelectorProps {
  aiConfig: AIConfig;
  setAiConfig: (cfg: AIConfig) => void;
  availableModels: AvailableModelsState;
  providersWithKeys: string[];
  effectiveProvider: string;
  displayModelName: string;
  isModelDropdownOpen: boolean;
  setIsModelDropdownOpen: (open: boolean) => void;
  modelOrToggles: Record<string, boolean>;
  setModelOrToggles: (toggles: any) => void;
  user: UserData | null;
  fetchAvailableModels: () => void;
  setShowSettings: (show: boolean) => void;
  API: string;
  axios: any;
  showToast: (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;
  // The chat on screen: a pick sets its model as well as the default for new chats.
  conversationId?: number | null;
  /** Usage windows (`useUsageLimits`); absent = no meters anywhere. */
  usage?: UsageLimits | null;
  /** The composer's thinking level: the menu's switch reads and writes the same value. */
  thinkingLevel?: ThinkingLevel;
  effortLevels?: string[] | null;
  onThinkingChange?: (level: ThinkingLevel) => void;
  /** Opens one page of the settings screen (falls back to `setShowSettings(true)`). */
  openSettings?: (page: SettingsPage) => void;
}

const LOCAL_KEY = 'local';

export const ModelSelector: React.FC<ModelSelectorProps> = ({
  aiConfig,
  setAiConfig,
  availableModels,
  providersWithKeys,
  effectiveProvider,
  displayModelName,
  isModelDropdownOpen,
  setIsModelDropdownOpen,
  modelOrToggles,
  setModelOrToggles,
  user,
  fetchAvailableModels,
  setShowSettings,
  API,
  axios,
  showToast,
  conversationId = null,
  usage = null,
  thinkingLevel,
  effortLevels,
  onThinkingChange,
  openSettings,
}) => {
  const { t } = useLang();
  const activeKey = activeProviderKey(aiConfig.provider_type, aiConfig.model_name || '');
  const activeGroup = CLI_GROUPS.find(g => g.key === activeKey) ?? null;
  const [selProv, setSelProv] = useState<string | null>(activeKey);
  const [query, setQuery] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);

  const goSettings = (page: SettingsPage) => {
    if (openSettings) openSettings(page); else setShowSettings(true);
  };

  // CLI doktoru: kurulu mu + giriş yapılmış mı (Kur/Giriş butonlarını sürer). Shared with the
  // settings screen's "Modeller ve hesaplar" page.
  const cli = useCliDoctor({ API, http: axios, token: user?.sessionToken, showToast, t });
  const { doctor, fetchDoctor, busyCli, installCli, loginCli } = cli;
  const doctorRefreshing = cli.refreshing;
  useEffect(() => {
    if (!isModelDropdownOpen) return;
    // Every opening starts on the provider of the model on screen.
    setSelProv(activeKey);
    setQuery('');
    if (!API) return;
    fetchDoctor();
    // Plan kilitleri TUR SIRASINDA öğrenilebilir (mesaj plan-blok yiyince backend
    // blocklist'e yazar) → menü her açılışta yüklü dinamik listeleri arka planda
    // tazele; yoksa kilit ancak uygulama yeniden başlayınca görünüyordu.
    (['cursor', 'opencode', 'copilot', 'codex'] as const).forEach(cli => {
      if (dynModels[cli]) fetchDynModels(cli, true);
    });
    if (activeGroup?.dynamic) fetchDynModels(activeGroup.dynamic);
    setTimeout(() => searchRef.current?.focus(), 60);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isModelDropdownOpen, API]);

  // Esc closes the menu (the settings screen has its own Esc).
  useEffect(() => {
    if (!isModelDropdownOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setIsModelDropdownOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isModelDropdownOpen, setIsModelDropdownOpen]);

  // Cursor/OpenCode/Copilot/Codex: hesaba/kuruluma göre CANLI model listesi (sağlayıcı ilk seçilince çekilir).
  const [dynModels, setDynModels] = useState<Record<string, ModelItem[]>>({});
  const [dynLoading, setDynLoading] = useState<Record<string, boolean>>({});
  const fetchDynModels = async (cli: 'cursor' | 'opencode' | 'copilot' | 'codex', force = false) => {
    if (dynLoading[cli]) return;
    if (!force && dynModels[cli]) return;
    setDynLoading(prev => ({ ...prev, [cli]: true }));
    try {
      const res = await axios.get(`${API}/cli-models/${cli}`, { headers: { 'X-Session-Token': user?.sessionToken ?? '' } });
      setDynModels(prev => ({ ...prev, [cli]: res.data?.models || [] }));
    } catch {
      // force-tazelemede eldeki listeyi SİLME (geçici ağ hatası kilitli/kilitsiz
      // bilgisini kaybettirmesin); ilk yüklemede boş liste göster.
      setDynModels(prev => ({ ...prev, [cli]: force && prev[cli] ? prev[cli] : [] }));
    } finally {
      setDynLoading(prev => ({ ...prev, [cli]: false }));
    }
  };

  const isGroupInstalled = cli.isInstalled;
  const groupNeedsLogin = cli.needsLogin;

  const groupModels = (g: CliGroupDef): ModelItem[] => {
    if (g.dynamic) return dynModels[g.dynamic] || [];
    return (availableModels.subscription || []).filter(m => g.matches(m.id));
  };

  // Bulut modelleri sağlayıcıya göre grupla (liste sırası korunur)
  const cloudGroups = useMemo(() => {
    const order: string[] = [];
    const map: Record<string, ModelItem[]> = {};
    for (const m of availableModels.cloud) {
      const p = m.provider || 'other';
      if (!map[p]) { map[p] = []; order.push(p); }
      map[p].push(m);
    }
    return order.map(p => ({
      provider: p,
      meta: CLOUD_PROVIDER_META[p] || { label: stripBidi(p) },
      models: map[p],
    }));
  }, [availableModels.cloud]);

  // The chat is read at click time: a pick lands on the chat on screen even
  // if its own model is still loading.
  const savePick = async (newCfg: AIConfig): Promise<boolean> => {
    if (!user) return true;
    try {
      await axios.post(`${API}/save-ai-config`, {
        ...newCfg, user_id: user.id,
        ...(conversationId != null ? { conversation_id: conversationId } : {}),
      });
      return true;
    } catch (e: any) {
      showToast(apiHataMesaji(e, t('settings.saveFailed')), 'error');
      return false;
    }
  };

  const selectCliModel = async (g: CliGroupDef, m: ModelItem) => {
    if (m.disabled) {
      showToast(t('models.planLocked', { model: goster(m.name), cli: g.label }), 'warning');
      return;
    }
    const newCfg = { ...aiConfig, provider_type: 'subscription', model_name: m.id, api_key: 'CLI_SESSION' };
    setAiConfig(newCfg);
    setIsModelDropdownOpen(false);
    if (!(await savePick(newCfg))) return;
    showToast(t('models.selected', { model: goster(m.name) }), 'info');
    if (doctor && doctor[g.availKey]?.installed === false) {
      showToast(t('models.cliNotFound', { cli: g.cliLabel }), 'warning');
    }
  };

  const selectCloudModel = async (m: ModelItem, orToggle: boolean) => {
    const effectiveModelId = (orToggle && m.openrouter_id) ? m.openrouter_id : m.id;
    const cloudProvider = (orToggle && m.openrouter_id) ? 'openrouter' : (m.provider || '');
    const hasKey = providersWithKeys.includes(cloudProvider);
    // Optimistic: tıklama HER ZAMAN modele geçer; key yoksa Ayarlar açılır.
    // api_key BİLEREK boşaltılır: state'te bayat 'CLI_SESSION' (veya başka
    // provider'ın key'i) kalmış olabilir — save-ai-config'e sızarsa kullanıcının
    // kayıtlı gerçek key'ini ezer (nvidia 401 bug'ı).
    const newCfg = { ...aiConfig, provider_type: cloudProvider, model_name: effectiveModelId, api_key: '' };
    setAiConfig(newCfg);
    setIsModelDropdownOpen(false);
    if (!(await savePick(newCfg))) return;
    if (!hasKey) {
      goSettings('modeller');
      showToast(`${orToggle ? 'OpenRouter' : goster(m.provider)} ${t('models.apiKeyNeeded')}`, 'warning');
    }
  };

  const selectLocalModel = async (m: ModelItem) => {
    const newCfg = { ...aiConfig, provider_type: 'ollama', model_name: m.id, api_key: '' };
    setAiConfig(newCfg);
    setIsModelDropdownOpen(false);
    await savePick(newCfg);
  };

  // ── Arama: tüm kaynaklarda düz filtre ──────────────────────────
  const q = query.trim().toLowerCase();
  const searchResults = useMemo(() => {
    if (!q) return null;
    const hit = (s?: string) => (s || '').toLowerCase().includes(q);
    const cli = CLI_GROUPS.flatMap(g =>
      groupModels(g).filter(m => hit(m.name) || hit(m.id) || hit(g.label))
        .map(m => ({ g, m }))
    );
    const cloud = availableModels.cloud.filter(m => hit(m.name) || hit(m.id) || hit(m.provider));
    const local = availableModels.local.filter(m => hit(m.name) || hit(m.id));
    return { cli, cloud, local };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, availableModels, dynModels]);

  // Arama açıkken dinamik listeleri de getir (sonuç tam olsun)
  useEffect(() => {
    if (q) { fetchDynModels('cursor'); fetchDynModels('opencode'); fetchDynModels('copilot'); fetchDynModels('codex'); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const isActive = (id: string, orToggle?: boolean, m?: ModelItem) => {
    if (orToggle && m?.openrouter_id) return aiConfig.model_name === m.openrouter_id;
    return aiConfig.model_name === id;
  };

  /**
   * Katalog metni HER ZAMAN buradan geçerek ekrana çıkıyor.
   *
   * Model adları, sağlayıcı adları ve model kimlikleri uzak bir kataloğun
   * (sağlayıcının `/v1/models` yanıtı ya da OpenRouter) kontrolünde. React
   * markup'ı kaçırıyor ama U+202E markup değil: tarayıcı onu onurlandırıp
   * satırın kalanını ters çiziyor, yani listede seçtiğin ad ile state'e yazılan
   * kimlik farklı okunabiliyor.
   *
   * Tek bir yardımcı bilerek: bu depoda ölçülmüş arıza sınıfı "kapı bir yolda
   * var, öbür yolda yok" — satır temizlenip seçili etiket temizlenmeyince
   * korunmuş görünüp korunmayan bir yüzey kalıyordu. `stripBidi` YALNIZ
   * gösterimde; `m.id` seçim ve kayıt yollarında ham kalıyor.
   */
  const goster = (s?: string) => stripBidi(s || '');

  const check = <Check size={14} className="model-opt-check" aria-hidden="true" />;

  const cliModelRow = (g: CliGroupDef, m: ModelItem) => {
    const active = isActive(m.id);
    if (m.disabled) {
      // Plan bu modeli desteklemiyor → soluk + kilitli (tıklanınca açıklayıcı toast)
      return (
        <button
          key={m.id}
          type="button"
          role="option"
          aria-selected={false}
          onClick={() => selectCliModel(g, m)}
          title={t('models.planLockedTitle')}
          className="model-opt is-locked"
        >
          <span className="model-opt-text">
            <span className="model-opt-name" lang="en">{goster(m.name)}</span>
            <span className="model-opt-sub">{t('models.notInPlan')}</span>
          </span>
        </button>
      );
    }
    return (
      <button key={m.id} type="button" role="option" aria-selected={active} onClick={() => selectCliModel(g, m)} className="model-opt">
        <span className="model-opt-text"><span className="model-opt-name" lang="en">{goster(m.name)}</span></span>
        {check}
      </button>
    );
  };

  const cloudModelRow = (m: ModelItem, withMark = false) => {
    const orToggle = modelOrToggles[m.id] ?? false;
    const effectiveModelId = (orToggle && m.openrouter_id) ? m.openrouter_id : m.id;
    const cloudProvider = (orToggle && m.openrouter_id) ? 'openrouter' : (m.provider || '');
    const hasKey = providersWithKeys.includes(cloudProvider);
    const active = isActive(effectiveModelId);

    return (
      <div key={m.id} className="mm-cloud-row">
        <button
          type="button"
          role="option"
          aria-selected={active}
          onClick={() => selectCloudModel(m, orToggle)}
          className={`model-opt${!hasKey ? ' is-keyless' : ''}`}
        >
          {withMark && <ModelLogo provider={orToggle ? 'openrouter' : m.provider} size={16} className="plogo" />}
          <span className="model-opt-text">
            <span className="model-opt-name" lang="en">{goster(m.name)}</span>
            <span className="model-opt-sub">
              {orToggle ? 'via OpenRouter' : goster(m.provider)}
              {!hasKey && <span className="mm-tag">{t('models.noKey')}</span>}
              {/* Doğrulanmamış = OpenRouter'ın açık kataloğundan geliyor: "böyle
                  bir model var" ama "senin hesabında var" DEĞİL. Bunu sessizce
                  doğrulanmış gibi göstermek, kullanıcıyı çalışmayacak bir modele
                  yollamak olurdu. */}
              {m.verified === false && (
                <span data-testid="model-unverified" title={t('models.unverifiedTitle')} className="mm-tag">
                  {t('models.unverified')}
                </span>
              )}
            </span>
          </span>
          {check}
        </button>
        {m.openrouter_id && (
          <button
            type="button"
            onClick={e => {
              e.stopPropagation();
              setModelOrToggles({ ...modelOrToggles, [m.id]: !orToggle });
            }}
            title={orToggle && !providersWithKeys.includes('openrouter') ? t('models.openrouterNoKey') : t('models.viaOpenrouter')}
            aria-pressed={orToggle}
            className="mm-or"
          >
            OR
          </button>
        )}
      </div>
    );
  };

  const localModelRow = (m: ModelItem) => (
    <button key={m.id} type="button" role="option" aria-selected={isActive(m.id)} onClick={() => selectLocalModel(m)} className="model-opt">
      <span className="model-opt-text"><span className="model-opt-name" lang="en">{goster(m.name)}</span></span>
      {check}
    </button>
  );

  // ── the chip ───────────────────────────────────────────────────
  const chipBrand = activeGroup ? activeGroup.brand
    : aiConfig.provider_type === 'ollama' ? 'ollama'
      : (aiConfig.provider_type || effectiveProvider || '');
  const markIsClaude = activeKey === 'claude' || activeKey === 'cloud:anthropic';
  const chipName = chipModelName(goster(displayModelName), markIsClaude);
  const activeFamily = activeGroup ? GROUP_USAGE_FAMILY[activeGroup.key] ?? null : null;
  const providerName = activeGroup ? activeGroup.label
    : aiConfig.provider_type === 'ollama' ? 'Ollama'
      : (CLOUD_PROVIDER_META[aiConfig.provider_type]?.label || goster(aiConfig.provider_type));

  // ── provider column ────────────────────────────────────────────
  type ProvRow = { key: string; name: string; brand: string; sub: string; off: boolean; right: React.ReactNode };
  const provRows: ProvRow[] = [
    ...CLI_GROUPS.map(g => {
      const fam = familyFor(usage, GROUP_USAGE_FAMILY[g.key]);
      const installed = isGroupInstalled(g);
      const needsLogin = groupNeedsLogin(g);
      const sub = !installed ? t('mm.sub.notInstalled')
        : needsLogin ? t('mm.sub.needsLogin')
          : fam?.plan ? t('mm.sub.subscriptionPlan', { plan: goster(fam.plan) }) : t('mm.sub.subscription');
      const right = installed && !needsLogin
        ? <UsePair fam={fam} modelId={g.key === activeKey ? aiConfig.model_name : null} />
        : null;
      return { key: g.key, name: g.label, brand: g.brand, sub, off: !installed || needsLogin, right };
    }),
    ...cloudGroups.map(({ provider, meta }) => {
      const hasKey = providersWithKeys.includes(provider);
      return {
        key: `cloud:${provider}`, name: meta.label, brand: provider,
        sub: hasKey ? t('mm.sub.apiKey') : t('mm.sub.noKey'), off: !hasKey, right: null,
      };
    }),
    { key: LOCAL_KEY, name: 'Ollama', brand: 'ollama', sub: t('mm.sub.local'), off: false,
      right: <span className="use-note">{t('use.unlimited')}</span> },
  ];

  const selRow = provRows.find(r => r.key === selProv) ?? provRows.find(r => r.key === activeKey) ?? provRows[0];

  const pickProv = (key: string) => {
    setSelProv(key);
    const g = CLI_GROUPS.find(x => x.key === key);
    if (g?.dynamic) fetchDynModels(g.dynamic);
  };

  const catalogError = availableModels.catalog_error ? (
    <div data-testid="cloud-catalog-error" className="mm-warn" role="status">
      <AlertTriangle size={14} aria-hidden="true" />
      <span className="mm-warn-t">{t('models.catalogFailed')}</span>
      <button type="button" data-testid="cloud-catalog-retry" onClick={() => fetchAvailableModels()} className="btn btn-ghost btn-sm">
        {t('models.catalogRetry')}
      </button>
    </div>
  ) : null;

  const pane = () => {
    const key = selRow.key;
    const g = CLI_GROUPS.find(x => x.key === key);
    if (g) {
      const models = groupModels(g);
      const installed = isGroupInstalled(g);
      const needsLogin = groupNeedsLogin(g);
      const loading = g.dynamic ? dynLoading[g.dynamic] : false;
      const fam = familyFor(usage, GROUP_USAGE_FAMILY[g.key]);
      return (
        <>
          {installed && !needsLogin && <UseBlock fam={fam} className="mm-use" nowIso={usage?.now}
            modelId={g.key === activeKey ? aiConfig.model_name : null} />}
          {!installed && (
            <>
              <p className="mm-note">{t('mm.note.install', { ad: g.cliLabel })}</p>
              <button type="button" className="btn btn-ghost btn-sm mm-act" data-testid="mm-install"
                title={t('models.installHint', { cli: g.cliLabel })}
                onClick={() => { if (busyCli !== g.key) installCli(g); }}>
                {busyCli === g.key ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} {t('models.install')}
              </button>
            </>
          )}
          {needsLogin && (
            <>
              <p className="mm-note">{t('mm.note.login', { ad: g.label })}</p>
              <button type="button" className="btn btn-ghost btn-sm mm-act" data-testid="mm-login"
                title={t('models.loginHint', { cli: g.cliLabel })}
                onClick={() => { if (busyCli !== g.key) loginCli(g); }}>
                {busyCli === g.key ? <Loader2 size={14} className="animate-spin" /> : <LogIn size={14} />} {t('models.login')}
              </button>
            </>
          )}
          {loading && <p className="mm-note"><Loader2 size={13} className="animate-spin" /> {t('models.loading')}</p>}
          {!loading && models.length === 0 && installed && !needsLogin && <p className="mm-note">{t('models.emptyGroup')}</p>}
          {!loading && models.length > 0 && (
            <div className="mm-list" role="listbox" aria-label={t('mm.models', { ad: g.label })}>
              {models.map(m => cliModelRow(g, m))}
            </div>
          )}
        </>
      );
    }
    if (key === LOCAL_KEY) {
      return (
        <>
          <p className="mm-note">{t('mm.note.local')}</p>
          {availableModels.local.length === 0
            ? <p className="mm-note">{t('models.noLocal')}</p>
            : <div className="mm-list" role="listbox" aria-label={t('mm.models', { ad: 'Ollama' })}>{availableModels.local.map(localModelRow)}</div>}
        </>
      );
    }
    const provider = key.replace(/^cloud:/, '');
    const group = cloudGroups.find(c => c.provider === provider);
    return (
      <>
        <p className="mm-note">{t('mm.note.api')}</p>
        {/* Listenin NEREDEN geldiğini söyle. Sessiz kalırsak elle yazılı bir
            katalog canlı sanılır ve eksikliği fark edilmez. */}
        {availableModels.cloud_sources?.[provider] === 'unknown' && (
          <p data-testid="cloud-source-unknown" className="mm-note mm-note-warn">{t('models.listUnverified')}</p>
        )}
        {group && (
          <div className="mm-list" role="listbox" aria-label={t('mm.models', { ad: group.meta.label })}>
            {group.models.map(m => cloudModelRow(m))}
          </div>
        )}
      </>
    );
  };

  const levels = (effortLevels && effortLevels.length ? effortLevels : ['auto']);
  const effLevel = thinkingLevel && levels.includes(thinkingLevel) ? thinkingLevel : 'auto';
  const activeFam = familyFor(usage, activeFamily);
  const measuredMin = minutesSince(activeFam?.measured_at, usage?.now);

  return (
    <div className="model-wrap" data-guide="model-chip">
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={isModelDropdownOpen}
        data-testid="model-pick"
        onClick={() => {
          const opening = !isModelDropdownOpen;
          setIsModelDropdownOpen(opening);
          if (opening) {
            fetchAvailableModels();
            if (aiConfig.provider_type === 'openrouter') {
              setModelOrToggles((prev: any) => ({ ...prev, [aiConfig.model_name]: true }));
            }
          }
        }}
        className="pick model-pick"
      >
        <ModelLogo provider={chipBrand} size={18} className="plogo" />
        <span className="model-text"><span className="model-name" lang="en">{chipName}</span></span>
        <ChipUse limits={usage} family={activeFamily} modelId={aiConfig.model_name} providerName={providerName} />
        <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>
      </button>

      <AnimatePresence>
        {isModelDropdownOpen && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setIsModelDropdownOpen(false)} />
            <motion.div
              initial={{ opacity: 0, y: -8, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.98 }}
              transition={{ duration: 0.16, ease: 'easeOut' }}
              className="model-menu"
              role="dialog"
              aria-label={t('mm.title')}
              data-testid="model-menu"
              data-guide="model-menu"
            >
              <div className="mm-top">
                <span className="model-menu-k">{t('mm.title')}</span>
                <label className="mm-search">
                  <Search size={14} aria-hidden="true" />
                  <input
                    ref={searchRef}
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    placeholder={t('models.search')}
                    aria-label={t('models.search')}
                  />
                </label>
                <button
                  type="button"
                  onClick={() => { if (!doctorRefreshing) { fetchDoctor(true); setDynModels({}); } }}
                  disabled={doctorRefreshing}
                  title={t('models.refreshTitle')}
                  aria-label={t('models.refreshTitle')}
                  className="icon-btn mm-refresh"
                >
                  <RefreshCw size={14} className={doctorRefreshing ? 'animate-spin' : ''} />
                </button>
              </div>

              {searchResults ? (
                <div className="mm-results custom-scrollbar">
                  {catalogError}
                  {searchResults.cli.length === 0 && searchResults.cloud.length === 0 && searchResults.local.length === 0 && (
                    <p className="mm-note">{t('models.noResults')}</p>
                  )}
                  <div className="mm-list" role="listbox" aria-label={t('models.search')}>
                    {searchResults.cli.map(({ g, m }) => (
                      <div key={`${g.key}:${m.id}`} className="mm-result">
                        <ModelLogo provider={g.brand} size={16} className="plogo" />
                        {cliModelRow(g, m)}
                      </div>
                    ))}
                    {searchResults.cloud.map(m => cloudModelRow(m, true))}
                    {searchResults.local.map(m => (
                      <div key={`local:${m.id}`} className="mm-result">
                        <ModelLogo provider="ollama" size={16} className="plogo" />
                        {localModelRow(m)}
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="mm-body">
                  <div className="mm-provs custom-scrollbar" role="tablist" aria-label={t('mm.providers')} aria-orientation="vertical">
                    {provRows.map(r => (
                      <button
                        key={r.key}
                        type="button"
                        role="tab"
                        data-prov={r.key}
                        data-testid={`mm-prov-${r.key}`}
                        aria-selected={r.key === selRow.key}
                        className={`mm-prov${r.off ? ' is-off' : ''}${r.key === activeKey ? ' is-current' : ''}`}
                        onClick={() => pickProv(r.key)}
                      >
                        <ModelLogo provider={r.brand} size={18} className="plogo" />
                        <span className="mm-prov-t">
                          <span className="mm-prov-name" lang="en">{r.name}</span>
                          <span className="mm-prov-sub">{r.sub}</span>
                        </span>
                        {r.right}
                      </button>
                    ))}
                  </div>
                  <div className="mm-pane custom-scrollbar" role="tabpanel" data-prov={selRow.key} data-testid="mm-pane">
                    {catalogError}
                    {pane()}
                  </div>
                  {onThinkingChange && (
                    <div className="mm-effort" data-testid="mm-effort" data-guide="model-effort">
                      <span className="mm-effort-k">{t('mm.effort')}</span>
                      <span className="gm-seg gm-seg-sm" role="radiogroup" aria-label={t('mm.effortGroup')}>
                        {levels.map(id => (
                          <button
                            key={id}
                            type="button"
                            role="radio"
                            aria-checked={id === effLevel}
                            disabled={levels.length <= 1}
                            onClick={() => onThinkingChange(id as ThinkingLevel)}
                          >
                            {t(`effort.label.${id}` as any)}
                          </button>
                        ))}
                      </span>
                    </div>
                  )}
                </div>
              )}

              <div className="mm-foot">
                <button
                  type="button"
                  className="mm-link"
                  data-testid="mm-usage-link"
                  onClick={() => { setIsModelDropdownOpen(false); goSettings('modeller'); }}
                >
                  <LayoutGrid size={14} aria-hidden="true" />{t('mm.usageLink')}
                </button>
                {measuredMin != null && (
                  <span className="mm-foot-r">
                    {measuredMin < 1 ? t('use.measuredNow') : t('use.measured', { dk: measuredMin })}
                  </span>
                )}
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
};
