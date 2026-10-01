import React, { useState, useEffect, useMemo, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ChevronDown,
  ChevronRight,
  Sparkles,
  Cpu,
  Key,
  Search,
  Check,
  AlertTriangle,
  Loader2,
  Download,
  LogIn,
  RefreshCw
} from 'lucide-react';
import { ModelAvatar } from './ModelAvatar';
import { AIConfig, UserData } from './types';
import { useLang } from '../../lib/i18n';
import { apiHataMesaji } from '../../lib/apiError';
import { stripBidi } from '../../lib/modelText';
import type { AvailableModelsState } from '../../hooks/home/useAIConfig';

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
}

interface CliGroupDef {
  key: string;
  label: string;
  brand: string;               // ModelAvatar/ModelLogo marka anahtarı
  availKey: string;            // /cli-availability yanıtındaki anahtar
  cliLabel: string;            // "kurulu değil" uyarısında insan-okur ad
  matches: (id: string) => boolean;
  dynamic?: 'cursor' | 'opencode' | 'copilot' | 'codex'; // /cli-models/{cli} ile liste
  accent: string;              // aktif model rengi (tailwind text sınıfı)
  dot: string;                 // aktif nokta rengi (tailwind bg sınıfı)
  badge?: string;              // grup başlığı yanındaki küçük rozet
}

export const CLI_GROUPS: CliGroupDef[] = [
  {
    key: 'claude', label: 'Claude Code', brand: 'claude', availKey: 'claude', cliLabel: 'Claude Code',
    matches: id => id.startsWith('claude-'),
    accent: 'text-orange-400', dot: 'bg-orange-400',
  },
  {
    key: 'codex', label: 'Codex', brand: 'openai', availKey: 'codex', cliLabel: 'Codex',
    matches: id => id.startsWith('gpt-'),
    dynamic: 'codex',
    accent: 'text-emerald-400', dot: 'bg-emerald-400',
  },
  {
    key: 'gemini', label: 'Antigravity', brand: 'gemini', availKey: 'agy', cliLabel: 'Antigravity (agy)',
    matches: id => id.startsWith('gemini') || id.startsWith('agy-'),
    accent: 'text-blue-400', dot: 'bg-blue-400',
  },
  {
    key: 'copilot', label: 'GitHub Copilot', brand: 'copilot', availKey: 'copilot', cliLabel: 'GitHub Copilot CLI',
    matches: id => id.startsWith('copilot-'),
    dynamic: 'copilot',
    accent: 'text-violet-300', dot: 'bg-violet-300',
  },
  {
    key: 'cursor', label: 'Cursor', brand: 'cursor', availKey: 'cursor', cliLabel: 'Cursor CLI (agent)',
    matches: id => id.startsWith('cursor-'),
    dynamic: 'cursor',
    accent: 'text-slate-100', dot: 'bg-slate-100',
  },
  {
    key: 'opencode', label: 'OpenCode', brand: 'opencode', availKey: 'opencode', cliLabel: 'OpenCode',
    matches: id => id.startsWith('opencode:'),
    dynamic: 'opencode',
    accent: 'text-teal-300', dot: 'bg-teal-300',
    badge: 'FREE + GO',
  },
  {
    key: 'kimi', label: 'Kimi Code', brand: 'moonshot', availKey: 'kimi', cliLabel: 'Kimi Code CLI',
    matches: id => id.startsWith('kimi-'),
    accent: 'text-fuchsia-300', dot: 'bg-fuchsia-300',
  },
];

type ModelItem = { id: string; name: string; provider?: string; openrouter_id?: string; disabled?: boolean; disabled_reason?: string; available?: boolean; verified?: boolean; source?: 'live' | 'openrouter'; context_length?: number };
type CliDoctor = Record<string, { installed: boolean; loggedIn: boolean | null }>;

// Bulut API sağlayıcı grupları (abonelik CLI grupları gibi kategorize görünüm)
export const CLOUD_PROVIDER_META: Record<string, { label: string; badge?: string }> = {
  anthropic:  { label: 'Anthropic' },
  openai:     { label: 'OpenAI' },
  google:     { label: 'Google' },
  deepseek:   { label: 'DeepSeek' },
  groq:       { label: 'Groq' },
  moonshot:   { label: 'Moonshot' },
  'z-ai':     { label: 'Z.ai' },
  nvidia:     { label: 'NVIDIA NIM', badge: 'provider.badge.free' },
};

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
}) => {
  const { t } = useLang();
  const activeGroupKey = CLI_GROUPS.find(g => g.matches(aiConfig.model_name || ''))?.key ?? null;
  // Aktif bulut sağlayıcısının grubu da başlangıçta açık gelsin (cloud:<provider>)
  const activeCloudKey = CLOUD_PROVIDER_META[aiConfig.provider_type] ? `cloud:${aiConfig.provider_type}` : null;
  const [expandedGroup, setExpandedGroup] = useState<string | null>(activeGroupKey ?? activeCloudKey);
  const [query, setQuery] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);

  // CLI doktoru: kurulu mu + giriş yapılmış mı (Kur/Giriş butonlarını sürer).
  const [doctor, setDoctor] = useState<CliDoctor | null>(null);
  const [doctorRefreshing, setDoctorRefreshing] = useState(false);
  const fetchDoctor = async (refresh = false) => {
    if (refresh) setDoctorRefreshing(true);
    try {
      const res = await axios.get(`${API}/cli-doctor${refresh ? '?refresh=true' : ''}`, { headers: { 'X-Session-Token': user?.sessionToken ?? '' } });
      setDoctor(res.data || {});
      if (refresh) showToast(t('models.doctorRefreshed'), 'success');
    } catch {
      if (refresh) showToast(t('models.doctorFailed'), 'error');
    } finally {
      if (refresh) setDoctorRefreshing(false);
    }
  };
  useEffect(() => {
    if (!isModelDropdownOpen || !API) return;
    fetchDoctor();
    setQuery('');
    // Plan kilitleri TUR SIRASINDA öğrenilebilir (mesaj plan-blok yiyince backend
    // blocklist'e yazar) → dropdown her açılışta yüklü dinamik listeleri arka planda
    // tazele; yoksa kilit ancak uygulama yeniden başlayınca görünüyordu.
    (['cursor', 'opencode', 'copilot', 'codex'] as const).forEach(cli => {
      if (dynModels[cli]) fetchDynModels(cli, true);
    });
    setTimeout(() => searchRef.current?.focus(), 60);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isModelDropdownOpen, API]);

  const [busyCli, setBusyCli] = useState<string | null>(null);
  const installCli = async (g: CliGroupDef) => {
    setBusyCli(g.key);
    try {
      await axios.post(`${API}/cli-install/${g.availKey}`, null, { headers: { 'X-Session-Token': user?.sessionToken ?? '' } });
      showToast(t('models.installStarted'), 'info');
    } catch (e: any) {
      showToast(apiHataMesaji(e, t('models.installFailed')), 'error');
    } finally { setBusyCli(null); }
  };
  const loginCli = async (g: CliGroupDef) => {
    setBusyCli(g.key);
    try {
      await axios.post(`${API}/cli-login/${g.availKey}`, null, { headers: { 'X-Session-Token': user?.sessionToken ?? '' } });
      showToast(t('models.loginStarted'), 'info');
    } catch (e: any) {
      showToast(apiHataMesaji(e, t('models.loginFailed')), 'error');
    } finally { setBusyCli(null); }
  };

  // Cursor/OpenCode: hesaba/kuruluma göre CANLI model listesi (grup ilk açılınca çekilir).
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

  const isGroupInstalled = (g: CliGroupDef): boolean => {
    if (!doctor) return true; // henüz yüklenmedi → uyarı gösterme
    return doctor[g.availKey]?.installed !== false;
  };
  const groupNeedsLogin = (g: CliGroupDef): boolean =>
    !!doctor && doctor[g.availKey]?.installed === true && doctor[g.availKey]?.loggedIn === false;

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
      setShowSettings(true);
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

  const sectionLabel = (icon: React.ReactNode, text: string) => (
    <div className="px-4 pt-3 pb-1.5 text-[9.5px] font-bold tracking-[0.14em] text-slate-500 uppercase flex items-center gap-1.5 select-none">
      {icon} {text}
    </div>
  );

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

  const cliModelRow = (g: CliGroupDef, m: ModelItem, indent = true) => {
    const active = isActive(m.id);
    if (m.disabled) {
      // Plan bu modeli desteklemiyor → soluk + kilitli (tıklanınca açıklayıcı toast)
      return (
        <button
          key={m.id}
          onClick={() => selectCliModel(g, m)}
          title={t('models.planLockedTitle')}
          className={`w-full text-left ${indent ? 'pl-[46px]' : 'pl-4'} pr-3 py-[7px] text-[12px] flex items-center justify-between rounded-lg hover:bg-white/[0.03] transition-colors`}
        >
          <span className="truncate font-medium text-slate-600 line-through decoration-slate-700">{goster(m.name)}</span>
          <span className="text-[9px] text-slate-600 shrink-0 ml-2">{t('models.notInPlan')}</span>
        </button>
      );
    }
    return (
      <button
        key={m.id}
        onClick={() => selectCliModel(g, m)}
        className={`w-full text-left ${indent ? 'pl-[46px]' : 'pl-4'} pr-3 py-[7px] text-[12px] flex items-center justify-between rounded-lg transition-colors hover:bg-white/[0.05] group/row`}
      >
        <span className={`truncate font-medium ${active ? g.accent : 'text-slate-300'}`}>{goster(m.name)}</span>
        {active && <Check size={13} className={`${g.accent} shrink-0 ml-2`} />}
      </button>
    );
  };

  const cloudModelRow = (m: ModelItem) => {
    const orToggle = modelOrToggles[m.id] ?? false;
    const effectiveModelId = (orToggle && m.openrouter_id) ? m.openrouter_id : m.id;
    const cloudProvider = (orToggle && m.openrouter_id) ? 'openrouter' : (m.provider || '');
    const hasKey = providersWithKeys.includes(cloudProvider);
    const active = isActive(effectiveModelId);

    return (
      <div key={m.id} className={`flex items-center gap-1 rounded-lg transition-colors hover:bg-white/[0.05] ${active ? 'bg-blue-500/[0.08]' : ''}`}>
        <button
          onClick={() => selectCloudModel(m, orToggle)}
          className={`flex-1 min-w-0 text-left pl-3 pr-1 py-[7px] flex items-center gap-2.5 ${!hasKey ? 'opacity-60' : ''}`}
        >
          <ModelAvatar provider={orToggle ? 'openrouter' : m.provider} size={11} containerSize="h-5 w-5" />
          <span className="flex flex-col min-w-0">
            <span className={`text-[12px] font-medium truncate flex items-center gap-1.5 ${active ? 'text-blue-400' : 'text-slate-300'}`}>
              {goster(m.name)}
              {hasKey
                ? <Key size={9} className="text-blue-400/70 shrink-0" />
                : <span className="text-[8px] text-amber-400/90 bg-amber-500/10 border border-amber-500/30 rounded px-1 leading-tight shrink-0">{t('models.noKey')}</span>}
              {/* Doğrulanmamış = OpenRouter'ın açık kataloğundan geliyor: "böyle
                  bir model var" ama "senin hesabında var" DEĞİL. Bunu sessizce
                  doğrulanmış gibi göstermek, kullanıcıyı çalışmayacak bir modele
                  yollamak olurdu. */}
              {m.verified === false && (
                <span data-testid="model-unverified" title={t('models.unverifiedTitle')}
                  className="text-[8px] text-slate-400/90 bg-slate-500/10 border border-slate-500/30 rounded px-1 leading-tight shrink-0">
                  {t('models.unverified')}
                </span>
              )}
            </span>
            <span className="text-[9.5px] text-slate-500 truncate">{orToggle ? 'via OpenRouter' : goster(m.provider)}</span>
          </span>
        </button>
        {active && <Check size={13} className="text-blue-400 shrink-0" />}
        {m.openrouter_id && (
          <button
            onClick={e => {
              e.stopPropagation();
              setModelOrToggles({ ...modelOrToggles, [m.id]: !orToggle });
            }}
            title={orToggle && !providersWithKeys.includes('openrouter') ? t('models.openrouterNoKey') : t('models.viaOpenrouter')}
            className={`mr-2 px-1.5 py-0.5 rounded-md text-[8px] font-semibold border transition-colors ${
              orToggle
                ? (providersWithKeys.includes('openrouter') ? 'border-purple-500/70 text-purple-300 bg-purple-500/10' : 'border-amber-500/70 text-amber-300 bg-amber-500/10')
                : 'border-slate-700 text-slate-500 hover:border-slate-500'
            }`}
          >
            OR
          </button>
        )}
      </div>
    );
  };

  return (
    <div className="model-wrap">
      {/* The mockup's model "character card": the model colour dot (--model, set on .app from
          the current pick), the name over its provider. Logic unchanged, restyle only. */}
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={isModelDropdownOpen}
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
        <span className="model-dot" aria-hidden="true" />
        <span className="model-text">
          <span className="model-name" lang="en">{goster(displayModelName)}</span>
          <span className="model-sub">
            {activeGroupKey ? CLI_GROUPS.find(g => g.key === activeGroupKey)!.label : goster(effectiveProvider)}
          </span>
        </span>
        <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>
      </button>

      <AnimatePresence>
        {isModelDropdownOpen && (
          <>
            <div
              className="fixed inset-0 z-40"
              onClick={() => setIsModelDropdownOpen(false)}
            />
            <motion.div
              initial={{ opacity: 0, y: -8, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.98 }}
              transition={{ duration: 0.16, ease: 'easeOut' }}
              className="model-menu"
            >
              {/* Arama */}
              <div className="p-2 border-b border-white/[0.06]">
                <div className="flex items-center gap-2 px-2.5 py-[7px] rounded-xl bg-white/[0.05] border border-white/[0.06] focus-within:border-white/20 transition-colors">
                  <Search size={12} className="text-slate-500 shrink-0" />
                  <input
                    ref={searchRef}
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    placeholder={t('models.search')}
                    className="w-full bg-transparent outline-none text-[12px] text-slate-200 placeholder:text-slate-600"
                  />
                  {query && (
                    <button onClick={() => setQuery('')} className="text-slate-500 hover:text-slate-300 text-[11px] leading-none">✕</button>
                  )}
                </div>
              </div>

              <div className="max-h-[62vh] overflow-y-auto custom-scrollbar pb-1">
                {searchResults ? (
                  /* ── ARAMA SONUÇLARI (düz liste) ── */
                  <div className="p-1">
                    {searchResults.cli.length === 0 && searchResults.cloud.length === 0 && searchResults.local.length === 0 && (
                      <div className="px-4 py-6 text-center text-[11.5px] text-slate-500">{t('models.noResults')}</div>
                    )}
                    {searchResults.cli.map(({ g, m }) => (
                      <div key={m.id} className="flex items-center">
                        <div className="pl-3 shrink-0"><ModelAvatar provider={g.brand} size={11} containerSize="h-5 w-5" /></div>
                        <div className="flex-1 min-w-0">{cliModelRow(g, m, false)}</div>
                      </div>
                    ))}
                    {searchResults.cloud.map(m => cloudModelRow(m))}
                    {searchResults.local.map(m => (
                      <button
                        key={m.id}
                        onClick={() => selectLocalModel(m)}
                        className={`w-full text-left px-4 py-[7px] text-[12px] rounded-lg hover:bg-white/[0.05] ${isActive(m.id) ? 'text-emerald-400' : 'text-slate-300'}`}
                      >
                        {goster(m.name)}
                      </button>
                    ))}
                  </div>
                ) : (
                  <>
                    {/* ── CLI ABONELİKLERİ ── */}
                    <div className="p-1">
                      <div className="flex items-center justify-between pr-2">
                        {sectionLabel(<Key size={9} />, t('models.subscription'))}
                        <button
                          onClick={() => { if (!doctorRefreshing) { fetchDoctor(true); setDynModels({}); } }}
                          disabled={doctorRefreshing}
                          title={t('models.refreshTitle')}
                          className={`p-1 rounded-md transition-colors ${doctorRefreshing
                            ? 'text-blue-400 bg-blue-500/10'
                            : 'text-slate-500 hover:text-slate-300 hover:bg-white/[0.06] active:scale-90'}`}
                        >
                          <RefreshCw size={11} className={doctorRefreshing ? 'animate-spin' : ''} />
                        </button>
                      </div>
                      {CLI_GROUPS.map(g => {
                        const models = groupModels(g);
                        const isOpen = expandedGroup === g.key;
                        const installed = isGroupInstalled(g);
                        const isGroupActive = g.key === activeGroupKey;
                        const loading = g.dynamic ? dynLoading[g.dynamic] : false;
                        return (
                          <div key={g.key} className={`rounded-xl transition-colors ${isOpen ? 'bg-white/[0.03]' : ''}`}>
                            <button
                              onClick={() => {
                                const next = isOpen ? null : g.key;
                                setExpandedGroup(next);
                                if (next && g.dynamic) fetchDynModels(g.dynamic);
                              }}
                              className="w-full text-left px-3 py-2 flex items-center gap-2.5 rounded-xl hover:bg-white/[0.04] transition-colors"
                            >
                              <ModelAvatar provider={g.brand} size={12} containerSize="h-6 w-6" />
                              <span className={`flex-1 min-w-0 text-[12.5px] font-semibold truncate ${isGroupActive ? g.accent : 'text-slate-200'}`}>
                                {g.label}
                              </span>
                              {isGroupActive && <span className={`h-1.5 w-1.5 rounded-full ${g.dot} shrink-0`} />}
                              {g.badge && installed && (
                                <span className="text-[8px] font-semibold text-emerald-300 bg-emerald-500/10 border border-emerald-500/30 rounded-md px-1.5 py-0.5 uppercase tracking-wide shrink-0">
                                  {t(g.badge as any)}
                                </span>
                              )}
                              {!installed && (
                                <span
                                  onClick={e => { e.stopPropagation(); if (busyCli !== g.key) installCli(g); }}
                                  title={t('models.installHint', { cli: g.cliLabel })}
                                  className="flex items-center gap-1 text-amber-200 bg-amber-500/15 border border-amber-500/40 rounded-md px-1.5 py-0.5 text-[8.5px] font-semibold uppercase tracking-wide shrink-0 hover:bg-amber-500/30 transition-colors cursor-pointer"
                                >
                                  {busyCli === g.key ? <Loader2 size={10} className="animate-spin" /> : <Download size={10} />} {t('models.install')}
                                </span>
                              )}
                              {groupNeedsLogin(g) && (
                                <span
                                  onClick={e => { e.stopPropagation(); if (busyCli !== g.key) loginCli(g); }}
                                  title={t('models.loginHint', { cli: g.cliLabel })}
                                  className="flex items-center gap-1 text-sky-200 bg-sky-500/15 border border-sky-500/40 rounded-md px-1.5 py-0.5 text-[8.5px] font-semibold uppercase tracking-wide shrink-0 hover:bg-sky-500/30 transition-colors cursor-pointer"
                                >
                                  {busyCli === g.key ? <Loader2 size={10} className="animate-spin" /> : <LogIn size={10} />} {t('models.login')}
                                </span>
                              )}
                              <ChevronDown size={12} className={`text-slate-500 transition-transform duration-200 shrink-0 ${isOpen ? 'rotate-180' : ''}`} />
                            </button>
                            <AnimatePresence initial={false}>
                              {isOpen && (
                                <motion.div
                                  initial={{ height: 0, opacity: 0 }}
                                  animate={{ height: 'auto', opacity: 1 }}
                                  exit={{ height: 0, opacity: 0 }}
                                  transition={{ duration: 0.15 }}
                                  className="overflow-hidden pb-1"
                                >
                                  {loading && (
                                    <div className="pl-[46px] py-2 flex items-center gap-2 text-[11px] text-slate-500">
                                      <Loader2 size={11} className="animate-spin" /> {t('models.loading')}
                                    </div>
                                  )}
                                  {!loading && models.length === 0 && (
                                    <div className="pl-[46px] pr-3 py-2 text-[10.5px] text-slate-500 leading-snug">
                                      {installed ? t('models.emptyGroup') : `${g.cliLabel} ${t('models.installFirst')}`}
                                    </div>
                                  )}
                                  {!loading && models.map(m => cliModelRow(g, m))}
                                </motion.div>
                              )}
                            </AnimatePresence>
                          </div>
                        );
                      })}
                    </div>

                    {/* Katalog ÇEKİLEMEDİĞİNDE bunu söyle.
                        Bölüm `cloudGroups.length > 0` ile gizlendiği için arıza
                        "bütün bulut modelleri kayboldu" gibi görünüyordu; boş
                        liste ile alınamamış liste aynı şey değil. Yeniden dene
                        düğmesi şart: kullanıcıya bir hâl gösterip çıkış yolu
                        vermemek, arızayı yalnız daha görünür yapardı. */}
                    {availableModels.catalog_error && (
                      <div
                        data-testid="cloud-catalog-error"
                        className="p-3 border-t border-white/[0.06] flex items-center gap-2 text-[11px] text-amber-400/90"
                      >
                        <AlertTriangle size={12} className="shrink-0" />
                        <span className="flex-1 leading-snug">{t('models.catalogFailed')}</span>
                        <button
                          data-testid="cloud-catalog-retry"
                          onClick={() => fetchAvailableModels()}
                          className="shrink-0 px-2 py-1 rounded-md border border-amber-500/40 bg-amber-500/10 text-amber-200 text-[10px] font-semibold hover:bg-amber-500/20 transition-colors"
                        >
                          {t('models.catalogRetry')}
                        </button>
                      </div>
                    )}

                    {/* ── BULUT API (sağlayıcıya göre gruplu) ── */}
                    {cloudGroups.length > 0 && (
                      <div className="p-1 border-t border-white/[0.06]">
                        {sectionLabel(<Sparkles size={9} />, t('models.cloud'))}
                        {cloudGroups.map(({ provider, meta, models }) => {
                          const gKey = `cloud:${provider}`;
                          const isOpen = expandedGroup === gKey;
                          const hasKey = providersWithKeys.includes(provider);
                          const isGroupActive = aiConfig.provider_type === provider ||
                            models.some(m => isActive(m.id) || (m.openrouter_id && aiConfig.model_name === m.openrouter_id));
                          return (
                            <div key={gKey} className={`rounded-xl transition-colors ${isOpen ? 'bg-white/[0.03]' : ''}`}>
                              <button
                                onClick={() => setExpandedGroup(isOpen ? null : gKey)}
                                className="w-full text-left px-3 py-2 flex items-center gap-2.5 rounded-xl hover:bg-white/[0.04] transition-colors"
                              >
                                <ModelAvatar provider={provider} size={12} containerSize="h-6 w-6" />
                                <span className={`flex-1 min-w-0 text-[12.5px] font-semibold truncate ${isGroupActive ? 'text-blue-400' : 'text-slate-200'}`}>
                                  {meta.label}
                                </span>
                                {isGroupActive && <span className="h-1.5 w-1.5 rounded-full bg-blue-400 shrink-0" />}
                                {meta.badge && (
                                  <span className="text-[8px] font-semibold text-emerald-300 bg-emerald-500/10 border border-emerald-500/30 rounded-md px-1.5 py-0.5 uppercase tracking-wide shrink-0">
                                    {t(meta.badge as any)}
                                  </span>
                                )}
                                {hasKey ? (
                                  <Key size={10} className="text-blue-400/70 shrink-0" />
                                ) : (
                                  <span className="text-[8px] text-amber-400/90 bg-amber-500/10 border border-amber-500/30 rounded px-1 leading-tight shrink-0">{t('models.noKey')}</span>
                                )}
                                <ChevronDown size={12} className={`text-slate-500 transition-transform duration-200 shrink-0 ${isOpen ? 'rotate-180' : ''}`} />
                              </button>
                              <AnimatePresence initial={false}>
                                {isOpen && (
                                  <motion.div
                                    initial={{ height: 0, opacity: 0 }}
                                    animate={{ height: 'auto', opacity: 1 }}
                                    exit={{ height: 0, opacity: 0 }}
                                    transition={{ duration: 0.15 }}
                                    className="overflow-hidden pb-1 pl-6"
                                  >
                                    {/* Listenin NEREDEN geldiğini söyle. Sessiz
                                        kalırsak elle yazılı bir katalog canlı
                                        sanılır ve eksikliği fark edilmez —
                                        kaymış bir liste hiç liste olmamasından
                                        kötü, çünkü güncel sanılıp okunuyor. */}
                                    {availableModels.cloud_sources?.[provider] === 'unknown' && (
                                      <div data-testid="cloud-source-unknown" className="px-3 pb-1.5 text-[10px] text-amber-400/80">
                                        {t('models.listUnverified')}
                                      </div>
                                    )}
                                    {models.map(m => cloudModelRow(m))}
                                  </motion.div>
                                )}
                              </AnimatePresence>
                            </div>
                          );
                        })}
                      </div>
                    )}

                    {/* ── YEREL ── */}
                    <div className="p-1 border-t border-white/[0.06]">
                      {sectionLabel(<Cpu size={9} />, t('models.local'))}
                      {availableModels.local.length === 0 && (
                        <div className="px-4 pb-2 text-[10.5px] text-slate-600">{t('models.noLocal')}</div>
                      )}
                      {availableModels.local.map(m => (
                        <button
                          key={m.id}
                          onClick={() => selectLocalModel(m)}
                          className={`w-full text-left px-4 py-[7px] text-[12px] rounded-lg hover:bg-white/[0.05] flex items-center justify-between ${isActive(m.id) ? 'text-emerald-400' : 'text-slate-300'}`}
                        >
                          <span className="truncate">{goster(m.name)}</span>
                          {isActive(m.id) && <Check size={13} className="text-emerald-400 shrink-0 ml-2" />}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>

              <button
                onClick={() => { setIsModelDropdownOpen(false); setShowSettings(true); }}
                className="w-full text-left px-4 py-3 text-[11px] text-slate-400 border-t border-white/[0.06] hover:bg-white/[0.04] transition-colors flex items-center justify-between group"
              >
                {t('models.settings')}
                <ChevronRight size={12} className="opacity-0 group-hover:opacity-100 transition-opacity" />
              </button>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
};
