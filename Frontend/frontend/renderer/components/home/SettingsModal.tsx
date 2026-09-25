import { LogOut, Settings, Trash2, X, Gamepad2, Loader2, Globe, Key, Check, Cpu, Hand } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";

import { AIConfig, AvailableModels, GenerationMode } from "./types";
import { ModelAvatar } from "./ModelAvatar";
import { UnityMCPStatus } from "../../hooks/home/useAIConfig";
import { useLang, type Lang } from "../../lib/i18n";
import { stripBidi } from "../../lib/modelText";



// Sağlayıcı seçim ızgarası (marka avatarlı — native <select> yerine).
// brand: ModelAvatar/ModelLogo anahtarı; badge: küçük rozet.
// ⚠️ `label` marka adı (çevrilmez), `labelKey`/`badge` ise SÖZLÜK ANAHTARIDIR.
// Bu liste modül düzeyinde sabit olduğu için `t()` burada çağrılamaz — anahtar
// saklanıp render anında çevriliyor. Eskiden burada düz Türkçe metin duruyordu
// ("ücretsiz", "yerel", "Abonelik (CLI)") ve dil EN yapılsa bile Türkçe kalıyordu.
const PROVIDER_TILES: { value: string; label: string; labelKey?: string; brand: string; badge?: string }[] = [
  { value: "anthropic",    label: "Claude",     brand: "anthropic" },
  { value: "openai",       label: "OpenAI",     brand: "openai" },
  { value: "google",       label: "Gemini",     brand: "google" },
  { value: "deepseek",     label: "DeepSeek",   brand: "deepseek" },
  { value: "moonshot",     label: "Kimi",       brand: "moonshot" },
  { value: "z-ai",         label: "GLM",        brand: "z-ai" },
  { value: "nvidia",       label: "NVIDIA",     brand: "nvidia", badge: "provider.badge.free" },
  { value: "groq",         label: "Groq",       brand: "groq" },
  { value: "openrouter",   label: "OpenRouter", brand: "openrouter" },
  { value: "ollama",       label: "Ollama",     brand: "ollama", badge: "provider.badge.local" },
  { value: "subscription", label: "Subscription (CLI)", labelKey: "provider.subscriptionCli", brand: "subscription" },
];




interface SettingsModalProps {
  open: boolean;
  aiConfig: AIConfig;
  availableModels?: AvailableModels;
  providersWithKeys: string[];
  onChange: (nextConfig: AIConfig) => void;
  onClose: () => void;
  onSave: () => Promise<void>;
  onLogout: () => void;
  onDeleteKey: (provider: string) => Promise<void>;
  unityMcpStatus: UnityMCPStatus;
  unityMcpToggling: boolean;
  onToggleUnityMcp: () => void;
  lang: Lang;
  onLangChange: (l: Lang) => void;
  approvalMode?: GenerationMode;
  onApprovalModeChange?: (mode: GenerationMode) => void;
}

type SettingsTab = 'general' | 'mode';


export const SettingsModal = ({
  open,
  aiConfig,
  availableModels,
  providersWithKeys,
  onChange,
  onClose,
  onSave,
  onLogout,
  onDeleteKey,
  unityMcpStatus,
  unityMcpToggling,
  onToggleUnityMcp,
  lang,
  onLangChange,
  approvalMode,
  onApprovalModeChange,
}: SettingsModalProps) => {
  const { t } = useLang();
  const [tab, setTab] = useState<SettingsTab>('general');
  const TABS: { id: SettingsTab; label: string }[] = [
    { id: 'general', label: t('settings.tabGeneral') },
    { id: 'mode', label: t('settings.tabMode') },
  ];
  const UNITY_STATUS_CONFIG: Record<UnityMCPStatus, { label: string; dot: string; bg: string; border: string }> = {
    off:       { label: t('unity.off'),       dot: "bg-slate-600",                bg: "bg-slate-900/50",   border: "border-slate-700/50" },
    // `blocked` satırının yokluğu ÇALIŞMA ANINDA çökme üretiyordu: aşağıda
    // `UNITY_STATUS_CONFIG[unityMcpStatus].border` okunuyor ve backend bu değeri
    // `b4065f1`'den beri döndürüyor, yani yabancı bir sunucu 8080'i tutarken
    // Ayarlar'ı açmak `undefined.border` demekti.
    blocked:   { label: t('unity.blocked'),   dot: "bg-red-500",                  bg: "bg-red-500/5",      border: "border-red-500/30" },
    starting:  { label: t('unity.starting'),  dot: "bg-yellow-400 animate-pulse", bg: "bg-yellow-500/5",   border: "border-yellow-500/20" },
    running:   { label: t('unity.running'),   dot: "bg-yellow-400 animate-pulse", bg: "bg-yellow-500/5",   border: "border-yellow-500/20" },
    connected: { label: t('unity.connected'), dot: "bg-emerald-400",              bg: "bg-emerald-500/5",  border: "border-emerald-500/20" },
    // Gri ve nabızsız: durum bilinmiyor, "çalışıyor" da denmiyor. Yeşil
    // bırakmak bulgu I-2'nin ta kendisiydi — yoklama başarısızken gösterge
    // süresiz `connected` kalıyordu.
    unknown:   { label: t('unity.unknown'),   dot: "bg-slate-500",                bg: "bg-slate-900/50",   border: "border-slate-700/50" },
  };
  // Anahtarın AÇIK görünmesi için sunucunun BİZİM olması gerekiyor. Eski koşul
  // `!== 'off'` idi ve `blocked`'ı açık sayıyordu — oysa o durumda 8080'de duran
  // sunucu bizim değil, yani kapalıdan daha kötü bir hal. Durumları tek tek
  // saymak bilinçli: yeni bir durum eklendiğinde varsayılan "açık" olmasın.
  const unityMcpAcik =
    unityMcpStatus === 'starting' || unityMcpStatus === 'running' || unityMcpStatus === 'connected';
  // Model onerileri CANLI listeden turetiliyor; elle yazili katalog YOK.
  //
  // 30 Agu 2026'da bulut katalogu backend'den silindi ama BU dosyadaki ikinci
  // kopya kalmisti ve olu bir modeli oneriyordu (Groq `llama-3.3-70b-versatile`,
  // 16 Agu'da kapatildi). Ayni kuralin iki yazili kopyasi sessizce ayrisiyor —
  // biri silinip digeri birakilinca ayrisma daha da gorunmez oluyor, cunku
  // "duzeltildi" sanilan bir yer var.
  //
  // Ilk sekiz: canli liste yuzlerce model dondurebiliyor ve bu bir cip serisi,
  // katalog degil. Tamami model seciciden gorulebiliyor.
  const saglayiciModelleri = (
    aiConfig.provider_type === 'ollama' ? availableModels?.local
      : aiConfig.provider_type === 'subscription' ? availableModels?.subscription
        : (availableModels?.cloud || []).filter(m => m.provider === aiConfig.provider_type)
  ) || [];
  // `label` GÖSTERİM, `value` ise seçildiğinde `model_name`e yazılan gerçek
  // kimlik — bu yüzden yalnız `label` temizleniyor. Aynı katalog model
  // seçicide de çiziliyor ve orada temizlenip burada bırakmak, bu depoda adı
  // konmuş arıza olurdu: kapı bir yolda var, öbür yolda yok.
  const MODEL_HINTS = saglayiciModelleri
    .slice(0, 8)
    .map(m => ({ label: stripBidi(m.name || ''), value: m.id }));
  const varsayilanModel = stripBidi(saglayiciModelleri[0]?.id || '');

  return (
  <AnimatePresence>
    {open && (
      <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-[100]">
        <motion.div
          initial={{ scale: 0.96, opacity: 0, y: 8 }}
          animate={{ scale: 1, opacity: 1, y: 0 }}
          exit={{ scale: 0.96, opacity: 0, y: 8 }}
          transition={{ duration: 0.16, ease: 'easeOut' }}
          className="bg-[#0B0D12]/95 backdrop-blur-xl border border-white/10 rounded-2xl max-w-lg w-full shadow-[0_24px_64px_-16px_rgba(0,0,0,0.9)] flex flex-col max-h-[88vh] overflow-hidden"
        >
          <div className="flex items-center justify-between px-5 pt-5 pb-4 border-b border-white/[0.06] shrink-0">
            <div className="flex items-center gap-2.5">
              <div className="p-1.5 bg-blue-500/10 rounded-lg text-blue-500"><Settings size={18} /></div>
              <h2 className="text-base font-bold text-white">{t('settings.title')}</h2>
            </div>
            <button onClick={onClose} className="p-1.5 hover:bg-white/[0.06] rounded-lg transition-colors text-slate-400">
              <X size={18} />
            </button>
          </div>
          <div role="tablist" className="flex gap-1 px-5 pt-3 shrink-0">
            {TABS.map(item => (
              <button
                key={item.id}
                role="tab"
                type="button"
                aria-selected={tab === item.id}
                onClick={() => setTab(item.id)}
                className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-colors border ${
                  tab === item.id
                    ? 'bg-blue-600/20 border-blue-500/40 text-blue-300'
                    : 'bg-transparent border-transparent text-slate-500 hover:text-slate-300 hover:bg-white/[0.04]'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="space-y-4 px-5 py-4 overflow-y-auto custom-scrollbar">
            {tab === 'general' && (<>
            <div>
              <label className="block text-[9.5px] font-bold text-slate-500 uppercase tracking-[0.14em] mb-2">{t('settings.provider')}</label>
              <div className="grid grid-cols-3 gap-1.5">
                {PROVIDER_TILES.map(tile => {
                  const selected = aiConfig.provider_type === tile.value;
                  const hasKey = providersWithKeys.includes(tile.value);
                  return (
                    <button
                      key={tile.value}
                      type="button"
                      onClick={() => onChange({ ...aiConfig, provider_type: tile.value, api_key: '', model_name: '' })}
                      className={`relative flex items-center gap-2 px-2 py-2 rounded-xl border text-left transition-all ${
                        selected
                          ? 'border-blue-500/60 bg-blue-500/10 shadow-[0_0_0_1px_rgba(59,130,246,0.25)]'
                          : 'border-white/[0.07] bg-white/[0.03] hover:bg-white/[0.06] hover:border-white/15'
                      } ${tile.value === 'subscription' ? 'col-span-2' : ''}`}
                    >
                      <ModelAvatar provider={tile.brand} size={11} containerSize="h-5 w-5" />
                      <span className={`text-[11px] font-semibold truncate ${selected ? 'text-blue-300' : 'text-slate-300'}`}>
                        {tile.labelKey ? t(tile.labelKey as any) : tile.label}
                      </span>
                      {tile.badge && (
                        <span className="absolute -top-1.5 -right-1 text-[7px] font-bold text-emerald-300 bg-emerald-500/15 border border-emerald-500/40 rounded px-1 uppercase tracking-wide">
                          {t(tile.badge as any)}
                        </span>
                      )}
                      {!tile.badge && hasKey && tile.value !== 'subscription' && tile.value !== 'ollama' && (
                        <Key size={8} className="absolute top-1.5 right-1.5 text-blue-400/60" />
                      )}
                      {selected && <Check size={11} className="ml-auto text-blue-400 shrink-0" />}
                    </button>
                  );
                })}
              </div>
            </div>
            {aiConfig.provider_type !== 'ollama' && aiConfig.provider_type !== 'subscription' && (
              <div>
                <label className="block text-[9.5px] font-bold text-slate-500 uppercase tracking-[0.14em] mb-1.5">
                  {t('settings.apiKey')}
                  {providersWithKeys.includes(aiConfig.provider_type) && !aiConfig.api_key && (
                    <span className="ml-2 text-emerald-400 normal-case tracking-normal">{t('settings.savedKey')}</span>
                  )}
                </label>
                <input
                  type="password"
                  value={aiConfig.api_key}
                  onChange={e => onChange({ ...aiConfig, api_key: e.target.value })}
                  className="w-full bg-white/[0.04] border border-white/[0.08] rounded-xl p-3 text-white text-sm outline-none focus:border-blue-500/60 focus:bg-white/[0.06] transition-colors placeholder:text-slate-600"
                  placeholder={providersWithKeys.includes(aiConfig.provider_type) ? t('settings.savedKeyPlaceholder') : t('settings.apiKeyPlaceholder')}
                />
                {providersWithKeys.includes(aiConfig.provider_type) && !aiConfig.api_key && (
                  <button
                    onClick={() => onDeleteKey(aiConfig.provider_type)}
                    className="mt-2 flex items-center gap-1.5 text-[11px] text-red-500/70 hover:text-red-400 transition-colors"
                  >
                    <Trash2 size={12} /> {t('settings.deleteKey')}
                  </button>
                )}
              </div>
            )}
            {aiConfig.provider_type === 'subscription' && (
              <div className="p-3 rounded-xl border border-purple-500/30 bg-purple-500/5">
                <p className="text-[10px] text-purple-300 font-medium">{t('settings.subscriptionActive')}</p>
                <p className="text-[9px] text-purple-400/70 mt-0.5">
                  {t('settings.subscriptionDesc')}
                </p>
              </div>
            )}
            <div>
              <label className="block text-[9.5px] font-bold text-slate-500 uppercase tracking-[0.14em] mb-1.5">{t('settings.modelName')}</label>
              <input
                value={aiConfig.model_name}
                onChange={e => onChange({ ...aiConfig, model_name: e.target.value })}
                className="w-full bg-white/[0.04] border border-white/[0.08] rounded-xl p-3 text-white text-sm outline-none focus:border-blue-500/60 focus:bg-white/[0.06] transition-colors placeholder:text-slate-600"
                placeholder={varsayilanModel || t('settings.modelPlaceholder')}
              />
              {MODEL_HINTS.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {MODEL_HINTS.map(hint => (
                    <button
                      key={hint.value}
                      type="button"
                      onClick={() => onChange({ ...aiConfig, model_name: hint.value })}
                      className={`px-2 py-0.5 rounded-md text-[10px] transition-colors border ${
                        aiConfig.model_name === hint.value
                          ? 'bg-blue-500/20 border-blue-500/40 text-blue-300'
                          : 'bg-white/[0.03] border-white/[0.08] text-slate-500 hover:text-slate-300 hover:border-white/20'
                      }`}
                    >
                      {hint.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {/* Unity MCP Toggle */}
            <div className={`flex items-center justify-between p-3 rounded-xl border ${UNITY_STATUS_CONFIG[unityMcpStatus].border} ${UNITY_STATUS_CONFIG[unityMcpStatus].bg}`}>
              <div className="flex items-center gap-2.5">
                <Gamepad2 size={15} className="text-purple-400 shrink-0" />
                <div>
                  <p className="text-xs font-semibold text-slate-200">Unity MCP</p>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${UNITY_STATUS_CONFIG[unityMcpStatus].dot}`} />
                    <span className="text-[10px] text-slate-400">{UNITY_STATUS_CONFIG[unityMcpStatus].label}</span>
                  </div>
                </div>
              </div>
              <button
                onClick={onToggleUnityMcp}
                disabled={unityMcpToggling || unityMcpStatus === 'starting'}
                className={`relative w-10 h-5 rounded-full transition-colors duration-200 focus:outline-none disabled:opacity-50 ${
                  unityMcpAcik ? 'bg-purple-600' : 'bg-slate-700'
                }`}
              >
                <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200 flex items-center justify-center ${
                  unityMcpAcik ? 'translate-x-5' : 'translate-x-0'
                }`}>
                  {unityMcpToggling && <Loader2 size={10} className="text-purple-600 animate-spin" />}
                </span>
              </button>
            </div>

            {/* Language */}
            <div className="flex items-center justify-between p-3 rounded-xl border border-white/[0.07] bg-white/[0.03]">
              <div className="flex items-center gap-2.5">
                <Globe size={15} className="text-slate-400 shrink-0" />
                <p className="text-xs font-semibold text-slate-200">{t('settings.language')}</p>
              </div>
              <div className="flex gap-1">
                {(['tr', 'en'] as Lang[]).map(l => (
                  <button
                    key={l}
                    onClick={() => onLangChange(l)}
                    className={`px-3 py-1 rounded-lg text-[11px] font-bold transition-all border ${
                      lang === l
                        ? 'bg-blue-600/20 border-blue-500/40 text-blue-300'
                        : 'bg-white/[0.04] border-white/[0.08] text-slate-500 hover:text-slate-300 hover:border-white/20'
                    }`}
                  >
                    {l === 'tr' ? '🇹🇷 TR' : '🇬🇧 EN'}
                  </button>
                ))}
              </div>
            </div>
            </>)}

            {tab === 'mode' && (
              <div className="space-y-2">
                <label className="block text-[9.5px] font-bold text-slate-500 uppercase tracking-[0.14em] mb-1">{t('settings.tabMode')}</label>
                {([
                  { id: 'auto' as GenerationMode, icon: <Cpu size={14} />, label: t('settings.modeAutoTitle'), explain: t('settings.modeAutoExplain'), warn: true },
                  { id: 'step' as GenerationMode, icon: <Hand size={14} />, label: t('settings.modeStepTitle'), explain: t('settings.modeStepExplain'), warn: false },
                ]).map(option => {
                  const selected = approvalMode === option.id;
                  // The auto card stays red whether or not it is selected: the
                  // warning is about the mode itself, not about the current choice.
                  return (
                    <button
                      key={option.id}
                      type="button"
                      disabled={!onApprovalModeChange}
                      onClick={() => onApprovalModeChange?.(option.id)}
                      data-warn={option.warn ? 'true' : undefined}
                      className={`w-full flex items-start gap-3 p-3 rounded-xl border text-left transition-all disabled:opacity-50 ${
                        selected
                          ? 'border-blue-500/60 bg-blue-500/10'
                          : 'border-white/[0.07] bg-white/[0.03] hover:bg-white/[0.06] hover:border-white/15'
                      } ${option.warn ? 'border-l-[3px] border-l-red-500' : ''}`}
                    >
                      <span className={`mt-0.5 shrink-0 ${option.warn ? 'text-red-400' : selected ? 'text-blue-400' : 'text-slate-500'}`}>{option.icon}</span>
                      <span className="flex-1 min-w-0">
                        <span className={`block text-xs ${option.warn ? 'font-bold tracking-wide text-red-400' : selected ? 'font-semibold text-blue-300' : 'font-semibold text-slate-200'}`}>{option.label}</span>
                        <span className="block text-[10.5px] text-slate-400 mt-0.5 leading-relaxed">{option.explain}</span>
                      </span>
                      {selected && <Check size={12} className="text-blue-400 shrink-0 mt-0.5" />}
                    </button>
                  );
                })}
              </div>
            )}

            <div className="flex gap-3 pt-2 mt-2 border-t border-white/[0.06]">
              <button
                onClick={onSave}
                className="flex-1 bg-blue-600 hover:bg-blue-500 text-white p-3 rounded-xl font-bold text-xs tracking-wide transition-all"
              >
                {t('settings.save')}
              </button>
              <button
                onClick={onLogout}
                className="bg-red-500/10 hover:bg-red-500/20 text-red-500 border border-red-500/20 px-4 py-3 rounded-xl font-bold text-xs tracking-wide transition-all flex items-center justify-center gap-2"
              >
                <LogOut size={14} /> {t('settings.logout')}
              </button>
            </div>
          </div>
        </motion.div>
      </div>
    )}
  </AnimatePresence>
  );
};
