import { LogOut, Settings, Trash2, X, Gamepad2, Loader2, Globe, Key, Check, Cpu, Hand, ShieldCheck, Type, Mic } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";

import { AIConfig, AvailableModels, GenerationMode } from "./types";
import { ModelAvatar } from "./ModelAvatar";
import { UnityMCPStatus } from "../../hooks/home/useAIConfig";
import { useLang, type Lang } from "../../lib/i18n";
import { stripBidi } from "../../lib/modelText";
import { RemoteControlSection } from "./RemoteControlSection";
import type { RemoteStatus } from "../../lib/remoteControl";
import { CODE_FONTS, FONT_STACKS, READING_FONTS, TEXT_SIZES, THEMES, useAppearance, type CodeFont, type ReadingFont } from "../../lib/appearance";

const FONT_NAMES = {
  inter: 'Inter', geist: 'Geist', 'plex-sans': 'IBM Plex Sans', figtree: 'Figtree', atkinson: 'Atkinson Hyperlegible',
  jetbrains: 'JetBrains Mono', 'geist-mono': 'Geist Mono', 'plex-mono': 'IBM Plex Mono', fira: 'Fira Code', cascadia: 'Cascadia Code',
} as const;



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
  autoTitles?: boolean;
  autoTitlesSaving?: boolean;
  onToggleAutoTitles?: () => void;
  dictationAutoLang?: boolean;
  dictationAutoLangSaving?: boolean;
  onToggleDictationAutoLang?: () => void;
  onRemoteStatus?: (status: RemoteStatus) => void;
}

type SettingsTab = 'general' | 'mode' | 'remote';


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
  autoTitles = true,
  autoTitlesSaving = false,
  onToggleAutoTitles,
  dictationAutoLang = false,
  dictationAutoLangSaving = false,
  onToggleDictationAutoLang,
  onRemoteStatus,
}: SettingsModalProps) => {
  const { t } = useLang();
  const { appearance, setAppearance } = useAppearance();
  const [tab, setTab] = useState<SettingsTab>('general');
  const TABS: { id: SettingsTab; label: string }[] = [
    { id: 'general', label: t('settings.tabGeneral') },
    { id: 'mode', label: t('settings.tabMode') },
    { id: 'remote', label: t('settings.tabRemote') },
  ];
  // Each Unity state maps to a tone; settings.css turns the tone into colour.
  // The `blocked` entry is load-bearing: the backend has returned that value
  // since `b4065f1`, and a missing key crashed the modal on open whenever a
  // foreign server held port 8080. `unknown` is a quiet grey with no pulse:
  // the state is not known, so it must not read as "working" (finding I-2,
  // where a failed poll left the indicator on `connected` indefinitely).
  const UNITY_STATUS_CONFIG: Record<UnityMCPStatus, { label: string; tone: 'off' | 'danger' | 'busy' | 'ok' }> = {
    off:       { label: t('unity.off'),       tone: 'off' },
    blocked:   { label: t('unity.blocked'),   tone: 'danger' },
    starting:  { label: t('unity.starting'),  tone: 'busy' },
    running:   { label: t('unity.running'),   tone: 'busy' },
    connected: { label: t('unity.connected'), tone: 'ok' },
    unknown:   { label: t('unity.unknown'),   tone: 'off' },
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
      <div role="dialog" aria-modal="true" aria-label={t('settings.title')} className="gm-set-scrim">
        <motion.div
          initial={{ scale: 0.96, opacity: 0, y: 8 }}
          animate={{ scale: 1, opacity: 1, y: 0 }}
          exit={{ scale: 0.96, opacity: 0, y: 8 }}
          transition={{ duration: 0.16, ease: 'easeOut' }}
          className="gm-set"
        >
          <div className="gm-set-head">
            <h2 className="gm-set-title"><Settings size={18} className="gm-set-title-ic" />{t('settings.title')}</h2>
            <button type="button" onClick={onClose} aria-label={t('settings.close')} className="gm-set-close">
              <X size={18} />
            </button>
          </div>
          <div role="tablist" className="gm-set-tabs">
            {TABS.map(item => (
              <button
                key={item.id}
                role="tab"
                type="button"
                aria-selected={tab === item.id}
                onClick={() => setTab(item.id)}
                className="gm-set-tab"
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="gm-set-body custom-scrollbar">
            {tab === 'general' && (<>
            <div className="gm-set-group">
              <label className="gm-set-k">{t('settings.provider')}</label>
              <div className="gm-set-grid">
                {PROVIDER_TILES.map(tile => {
                  const selected = aiConfig.provider_type === tile.value;
                  const hasKey = providersWithKeys.includes(tile.value);
                  return (
                    <button
                      key={tile.value}
                      type="button"
                      aria-pressed={selected}
                      onClick={() => onChange({ ...aiConfig, provider_type: tile.value, api_key: '', model_name: '' })}
                      className={`gm-set-tile ${tile.value === 'subscription' ? 'gm-set-tile-wide' : ''}`}
                    >
                      <ModelAvatar provider={tile.brand} size={11} containerSize="h-5 w-5" />
                      <span className="gm-set-tile-text">
                        <span className="gm-set-tile-name">{tile.labelKey ? t(tile.labelKey as any) : tile.label}</span>
                        {/* The badge is a sub line under the name: as a corner sticker it
                            had to shrink to 7px to fit, far below the 12px floor. */}
                        {tile.badge && <span className="gm-set-tile-sub">{t(tile.badge as any)}</span>}
                      </span>
                      {!tile.badge && hasKey && tile.value !== 'subscription' && tile.value !== 'ollama' && (
                        <Key size={10} className="gm-set-tile-key" />
                      )}
                      {selected && <Check size={12} className="gm-set-tile-check" />}
                    </button>
                  );
                })}
              </div>
            </div>
            {aiConfig.provider_type !== 'ollama' && aiConfig.provider_type !== 'subscription' && (
              <div className="gm-set-group">
                <label className="gm-set-k" htmlFor="settings-api-key">
                  {t('settings.apiKey')}
                  {providersWithKeys.includes(aiConfig.provider_type) && !aiConfig.api_key && (
                    <span className="gm-set-k-note">{t('settings.savedKey')}</span>
                  )}
                </label>
                <input
                  id="settings-api-key"
                  type="password"
                  value={aiConfig.api_key}
                  onChange={e => onChange({ ...aiConfig, api_key: e.target.value })}
                  className="gm-set-input"
                  placeholder={providersWithKeys.includes(aiConfig.provider_type) ? t('settings.savedKeyPlaceholder') : t('settings.apiKeyPlaceholder')}
                />
                {providersWithKeys.includes(aiConfig.provider_type) && !aiConfig.api_key && (
                  <button
                    type="button"
                    onClick={() => onDeleteKey(aiConfig.provider_type)}
                    data-tone="danger"
                    className="gm-set-link self-start"
                  >
                    <Trash2 size={12} /> {t('settings.deleteKey')}
                  </button>
                )}
              </div>
            )}
            {aiConfig.provider_type === 'subscription' && (
              <div className="gm-set-card">
                <p className="gm-set-name">{t('settings.subscriptionActive')}</p>
                <p className="gm-set-hint">{t('settings.subscriptionDesc')}</p>
              </div>
            )}
            <div className="gm-set-group">
              <label className="gm-set-k" htmlFor="settings-model-name">{t('settings.modelName')}</label>
              <input
                id="settings-model-name"
                value={aiConfig.model_name}
                onChange={e => onChange({ ...aiConfig, model_name: e.target.value })}
                className="gm-set-input"
                placeholder={varsayilanModel || t('settings.modelPlaceholder')}
              />
              {MODEL_HINTS.length > 0 && (
                <div className="gm-set-chips">
                  {MODEL_HINTS.map(hint => (
                    <button
                      key={hint.value}
                      type="button"
                      aria-pressed={aiConfig.model_name === hint.value}
                      onClick={() => onChange({ ...aiConfig, model_name: hint.value })}
                      className="gm-set-chip"
                    >
                      {hint.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {/* Unity MCP toggle. The row carries the status as a tone: `blocked`
                must not look like the grey "off", because it hides a foreign server. */}
            <div
              data-testid="unity-mcp-row"
              data-status={unityMcpStatus}
              data-tone={UNITY_STATUS_CONFIG[unityMcpStatus].tone}
              className="gm-set-card gm-set-row"
            >
              <div className="gm-set-row-l">
                <Gamepad2 size={15} className="gm-set-ic" />
                <div>
                  <p className="gm-set-name">Unity MCP</p>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <span className="gm-set-lamp" data-tone={UNITY_STATUS_CONFIG[unityMcpStatus].tone} />
                    <span className="gm-set-meta">{UNITY_STATUS_CONFIG[unityMcpStatus].label}</span>
                  </div>
                </div>
              </div>
              {/* Kept a plain button (no role="switch") so its accessible role does not
                  change; data-state says whether it reads as on. */}
              <button
                type="button"
                aria-label="Unity MCP"
                data-testid="unity-mcp-toggle"
                data-state={unityMcpAcik ? 'on' : 'off'}
                onClick={onToggleUnityMcp}
                disabled={unityMcpToggling || unityMcpStatus === 'starting'}
                className="gm-set-switch"
              >
                <span className="gm-set-knob">
                  {unityMcpToggling && <Loader2 size={10} className="animate-spin" />}
                </span>
              </button>
            </div>

            <div role="group" aria-label={t('settings.appearance.title')} className="gm-set-group">
              <p className="gm-set-k">{t('settings.appearance.title')}</p>
              <div className="gm-set-card gm-set-row">
                <p className="gm-set-name">{t('settings.appearance.theme')}</p>
                <div role="group" aria-label={t('settings.appearance.theme')} className="gm-set-seg">
                  {THEMES.map(theme => (
                    <button key={theme} type="button" aria-pressed={appearance.theme === theme}
                      onClick={() => setAppearance({ theme })}
                      className="gm-set-seg-btn"
                    >{t(`settings.appearance.${theme}`)}</button>
                  ))}
                </div>
              </div>
              <div className="gm-set-card gm-set-row">
                <label htmlFor="appearance-reading-font" className="gm-set-name">{t('settings.appearance.readingFont')}</label>
                <select id="appearance-reading-font" value={appearance.readingFont}
                  onChange={event => setAppearance({ readingFont: event.target.value as ReadingFont })}
                  className="gm-set-select"
                >
                  {READING_FONTS.map(font => (
                    <option key={font} value={font} style={font === 'theme' ? undefined : { fontFamily: FONT_STACKS[font] }}>
                      {font === 'theme' ? t('settings.appearance.themeDefault') : FONT_NAMES[font]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="gm-set-card gm-set-row">
                <label htmlFor="appearance-code-font" className="gm-set-name">{t('settings.appearance.codeFont')}</label>
                <select id="appearance-code-font" value={appearance.codeFont}
                  onChange={event => setAppearance({ codeFont: event.target.value as CodeFont })}
                  className="gm-set-select"
                >
                  {CODE_FONTS.map(font => (
                    <option key={font} value={font} style={font === 'theme' ? undefined : { fontFamily: FONT_STACKS[font] }}>
                      {font === 'theme' ? t('settings.appearance.themeDefault') : FONT_NAMES[font]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="gm-set-card gm-set-row">
                <p className="gm-set-name">{t('settings.appearance.textSize')}</p>
                <div role="group" aria-label={t('settings.appearance.textSize')} className="gm-set-seg">
                  {TEXT_SIZES.map(textSize => (
                    <button key={textSize} type="button" aria-pressed={appearance.textSize === textSize}
                      onClick={() => setAppearance({ textSize })}
                      className="gm-set-seg-btn"
                    >{t(`settings.appearance.${textSize}`)}</button>
                  ))}
                </div>
              </div>
              <div className="gm-set-card gm-set-row">
                <p className="gm-set-name">{t('settings.appearance.intro')}</p>
                <button type="button" role="switch" aria-checked={appearance.intro} aria-label={t('settings.appearance.intro')}
                  onClick={() => setAppearance({ intro: !appearance.intro })}
                  className="gm-set-switch"
                >
                  <span className="gm-set-knob" />
                </button>
              </div>
            </div>

            {/* Language */}
            <div className="gm-set-card gm-set-row">
              <div className="gm-set-row-l">
                <Globe size={15} className="gm-set-ic" />
                <p className="gm-set-name">{t('settings.language')}</p>
              </div>
              <div role="group" aria-label={t('settings.language')} className="gm-set-seg">
                {(['tr', 'en'] as Lang[]).map(l => (
                  <button
                    key={l}
                    type="button"
                    aria-pressed={lang === l}
                    onClick={() => onLangChange(l)}
                    className="gm-set-seg-btn"
                  >
                    {l === 'tr' ? '🇹🇷 TR' : '🇬🇧 EN'}
                  </button>
                ))}
              </div>
            </div>

            {/* Auto chat titles */}
            {onToggleAutoTitles && (
              <div className="gm-set-card gm-set-row">
                <div className="gm-set-row-l">
                  <Type size={15} className="gm-set-ic" />
                  <div className="min-w-0">
                    <p className="gm-set-name">{t('settings.autoTitles')}</p>
                    <p className="gm-set-hint">{t('settings.autoTitlesHint')}</p>
                  </div>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={autoTitles}
                  aria-label={t('settings.autoTitles')}
                  data-testid="auto-titles-toggle"
                  onClick={onToggleAutoTitles}
                  disabled={autoTitlesSaving}
                  className="gm-set-switch"
                >
                  <span className="gm-set-knob" />
                </button>
              </div>
            )}

            {/* Dictation: detect the spoken language on a CPU-only machine too */}
            {onToggleDictationAutoLang && (
              <div className="gm-set-card gm-set-row">
                <div className="gm-set-row-l">
                  <Mic size={15} className="gm-set-ic" />
                  <div className="min-w-0">
                    <p className="gm-set-name">{t('settings.dictationAutoLang')}</p>
                    <p className="gm-set-hint">{t('settings.dictationAutoLangHint')}</p>
                  </div>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={dictationAutoLang}
                  aria-label={t('settings.dictationAutoLang')}
                  data-testid="dictation-auto-lang-toggle"
                  onClick={onToggleDictationAutoLang}
                  disabled={dictationAutoLangSaving}
                  className="gm-set-switch"
                >
                  <span className="gm-set-knob" />
                </button>
              </div>
            )}
            </>)}

            {tab === 'mode' && (
              <div className="gm-set-group">
                <label className="gm-set-k">{t('settings.tabMode')}</label>
                {([
                  { id: 'auto' as GenerationMode, icon: <Cpu size={14} />, label: t('settings.modeAutoTitle'), explain: t('settings.modeAutoExplain'), warn: true, recommended: false },
                  { id: 'balanced' as GenerationMode, icon: <ShieldCheck size={14} />, label: t('settings.modeBalancedTitle'), explain: t('settings.modeBalancedExplain'), warn: false, recommended: true },
                  { id: 'step' as GenerationMode, icon: <Hand size={14} />, label: t('settings.modeStepTitle'), explain: t('settings.modeStepExplain'), warn: false, recommended: false },
                ]).map(option => {
                  const selected = approvalMode === option.id;
                  // The auto card keeps its warning whether or not it is selected:
                  // the warning is about the mode itself, not about the current choice.
                  return (
                    <button
                      key={option.id}
                      type="button"
                      disabled={!onApprovalModeChange}
                      onClick={() => onApprovalModeChange?.(option.id)}
                      aria-pressed={selected}
                      data-warn={option.warn ? 'true' : undefined}
                      className="gm-set-mode"
                    >
                      <span className="gm-set-mode-ic">{option.icon}</span>
                      <span className="flex-1 min-w-0">
                        <span className="flex flex-wrap items-center gap-1.5">
                          <span className="gm-set-mode-title" data-warn={option.warn ? 'true' : undefined}>{option.label}</span>
                          {option.recommended && (
                            <span data-testid="mode-recommended-badge" className="gm-set-badge">
                              {t('mode.recommended')}
                            </span>
                          )}
                        </span>
                        <span className="gm-set-hint block leading-relaxed">{option.explain}</span>
                      </span>
                      {selected && <Check size={12} className="gm-set-mode-ic" />}
                    </button>
                  );
                })}
              </div>
            )}

            {tab === 'remote' && <RemoteControlSection onStatus={onRemoteStatus} />}

            <div className="gm-set-foot">
              <button
                type="button"
                onClick={onSave}
                className="gm-set-btn gm-set-btn-lg gm-set-btn-primary"
              >
                {t('settings.save')}
              </button>
              <button
                type="button"
                onClick={onLogout}
                data-tone="danger"
                className="gm-set-btn gm-set-btn-lg"
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
