import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';
import axios from 'axios';
import { ArrowDown } from 'lucide-react';
import { LangContext, aktifDilAyarla, ceviriUygula, type Lang, type TValues } from '../lib/i18n';
import { sohbetKilitliMi } from '../lib/providerGate';
import { getUnsavedEditorContext } from '../lib/editor-context';
import { contentPane } from '../lib/contentPane';
import { displayName } from '../lib/displayName';
import { routeForFile } from '../components/model-viewer/extensions';

import { Sidebar } from '../components/home/Sidebar';
import { EditorPanel, hostOpenTarget } from '../components/home/EditorPanel';
import { CsharpProjectHint } from '../components/home/CsharpProjectHint';
import { TerminalPanel, type DrawerTab } from '../components/home/TerminalPanel';
import { ChatPanel } from '../components/home/ChatPanel';
import { SettingsScreen } from '../components/home/settings/SettingsScreen';
import { APP_VERSION } from '../components/home/settings/SettingsPages';
import type { SettingsPage } from '../components/home/settings/pages';
import { ExportModal } from '../components/home/ExportModal';
import { ModelSelector } from '../components/home/ModelSelector';
import { WorkspaceScreen } from '../components/home/WorkspaceScreen';
import { Workspace, KodPane, PreviewPane, ScenePane, ChangedFiles, toggleTerminalDrawer, type WsDiff, type ChangedFile } from '../components/home/Workspace';
import { HierarchyPanel } from '../components/home/HierarchyPanel';
import { InspectorPane } from '../components/home/InspectorPane';
import { useSceneEditorSetting } from '../lib/sceneEditor';
import { useSceneEditor } from '../hooks/home/useSceneEditor';
import { ProjectFiles } from '../components/home/FileTree';
import { ControlPanel, ThinkingLevel, EffortCaps } from '../components/home/ControlPanel';
import { SessionReportPanel } from '../components/home/SessionReportPanel';
import { UnityMcpToggle } from '../components/home/UnityMcpToggle';
import { AnimatedChatInput, type ComposerPickers } from '../components/ui/animated-ai-chat';
import { ToastContainer } from '../components/ui/Toast';

import { useAppInitialization } from '../hooks/home/useAppInitialization';
import { useAuth } from '../hooks/home/useAuth';
import { useDisplayName } from '../hooks/home/useDisplayName';
import { useFileSystem } from '../hooks/home/useFileSystem';
import { useChat } from '../hooks/home/useChat';
import { useAutoChatTitles } from '../hooks/home/useAutoChatTitles';
import { useDictationSettings } from '../hooks/home/useDictationSettings';
import { useUsageLimits } from '../hooks/home/useUsageLimits';
import { GROUP_USAGE_FAMILY } from '../lib/usageLimits';
import { CLI_GROUPS, activeProviderKey } from '../components/home/providerGroups';
import { useSideChat, sideQuote } from '../hooks/home/useSideChat';
import { useAIConfig } from '../hooks/home/useAIConfig';
import { useMCPApproval } from '../hooks/home/useMCPApproval';
import { useChatNotifications } from '../hooks/home/useChatNotifications';
import { useAutoScroll } from '../hooks/home/useAutoScroll';
import { useLiveDiagnostics } from '../hooks/home/useLiveDiagnostics';
import { McpApprovalCards } from '../components/home/McpApprovalCards';
import { McpUnknownTray } from '../components/home/McpUnknownTray';
import { ChatTabs, BranchButton, hasBranches } from '../components/home/ChatTabs';
import { SideChatPanel, SideQuestionButton } from '../components/home/SideChatPanel';
import { SidebarToggle } from '../components/home/AwaitingBadge';
import { useRemoteStatus } from '../components/home/RemoteBadge';
import { ModeChip } from '../components/home/ModeChip';
import { modelFamily } from '../lib/modelFamily';
import { useRemoteEffort, useRemoteUi } from '../lib/remoteControl';
import { useAppearance } from '../lib/appearance';
import { awaitingElsewhere } from '../lib/convFamily';
import { useNewChatShortcut } from '../lib/newChatShortcut';
import { useTurnDone, cardOnScreen } from '../lib/turnDone';
import { useWorkspacePanel, tabForPath } from '../lib/workspacePanel';
import { usePendingChange } from '../lib/pendingChange';
import { clearSeen, isUnseen, loadSeen, saveSeen, type Seen } from '../lib/changesSeen';
import { ThreadHeader } from '../components/home/ThreadHeader';
import { EmptyChat, questDraft } from '../components/home/EmptyChat';
import { AchievementToast } from '../components/home/AchievementToast';
import { ProfileView } from '../components/home/ProfileView';
import { useProfileStats } from '../hooks/home/useProfileStats';
import { latestUnlocked } from '../lib/profileStats';
import { useAchievementQueue } from '../lib/achievementQueue';
import { isChatEmpty } from '../components/home/ChatPanel';
import { GuideScreen } from '../components/home/GuideScreen';
import { GuideTour } from '../components/home/GuideTour';
import { useGuide, type GuideHost } from '../hooks/home/useGuide';
import { capabilities } from '../lib/guide/capabilities';
import { parseGuideCommand } from '../lib/guide/command';
import { guideWorkspaceTab, wants, type PrepareHandlers } from '../lib/guide/prepare';
import type { WsTab, WsWidth } from '../lib/workspacePanel';

// The guide's prepare-action arguments (REHBER-KAYITLARI.md section 4) in this app's own ids.
const GUIDE_SETTINGS: Record<Parameters<PrepareHandlers['settings']>[0], SettingsPage> = {
  general: 'genel', models: 'modeller', appearance: 'gorunum', unity: 'unity', approval: 'onay', remote: 'uzak', account: 'hesap',
};
const GUIDE_TABS: Record<Parameters<PrepareHandlers['workspace.tab']>[0], WsTab> = { scene: 'sahne', files: 'dosyalar', code: 'kod', preview: 'onizleme' };
const GUIDE_WIDTHS: Record<Parameters<PrepareHandlers['workspace.width']>[0], WsWidth> = { narrow: 'dar', half: 'yarim', focus: 'odak' };
const GUIDE_DRAWER: Record<Parameters<PrepareHandlers['drawer']>[0], DrawerTab> = { terminal: 'terminal', console: 'konsol', problems: 'sorunlar', connections: 'baglantilar' };
/** What a guide topic changed and gives back when it ends. */
type GuideSnapshot = { open: boolean; width: WsWidth; tab: WsTab; terminal: boolean; drawerTab: DrawerTab; convId: number | null };

// Lazy island: keeps three.js out of the eager bundle, which nothing else in
// this app needs, and off the server render (it touches WebGL on mount).
const ModelPreviewPanel = dynamic(() => import('../components/model-viewer/ModelPreviewPanel'), { ssr: false });
// Second lazy island: the image panel shares the preview slot but needs none
// of three.js, so keeping them apart keeps a texture click off that bundle.
const ImagePreviewPanel = dynamic(() => import('../components/image-viewer/ImagePreviewPanel'), { ssr: false });

const ipc = typeof window !== 'undefined' ? (window as any).ipc : null;
const globalStyles = `
  .custom-scrollbar::-webkit-scrollbar { width: 6px; height: 6px; }
  .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
  .custom-scrollbar::-webkit-scrollbar-thumb { background: #334155; border-radius: 10px; }
  .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #475569; }
  .no-scrollbar::-webkit-scrollbar { display: none; }
`;

export default function Home() {
  // `toasts` ve `dismissToast` bilerek alınıyor: hook ikisini de döndürüyordu,
  // burada yalnız `showToast` alınıp diğerleri atılıyordu ve `ToastContainer`
  // hiçbir yerde mount edilmemişti. Sonuç: `showToast` bir state'e yazıyor,
  // çizen kimse yok — onay kapısı işinin bütün kullanıcı bildirimi görünmezdi
  // ve bunu 10/10 mutasyon bile göremedi (hepsi hook state'ine bakıyordu).
  const { API, backendReady, backendError, showToast, toasts, dismissToast } = useAppInitialization();
  const auth = useAuth(API, backendReady);
  const me = useDisplayName(API, !auth.isLoading);
  const fs = useFileSystem(API, auth.user, showToast as any);
  const ai = useAIConfig(API, auth.user, showToast as any, fs.workspacePath);
  const hasAutoLoadedRef = useRef(false);
  
  const chat = useChat(
    API, 
    auth.user, 
    ai.aiConfig, 
    fs.workspacePath, 
    showToast as any, 
    fs.refreshFileTree, 
    fs.suggestFilePath,
    convId => { if (auth.user) ai.showChatModel(auth.user.id, convId); }
  );
  const autoTitles = useAutoChatTitles(API, auth.user?.id, showToast as any);
  const dictation = useDictationSettings(API, auth.user?.id, showToast as any);
  const remote = useRemoteStatus(backendReady && !!auth.user);
  // A phone is paired and can decide registry cards (gates, commands, questions).
  const phonePaired = !!remote.status?.enabled && (remote.status?.devices ?? 0) > 0;

  // Read-only side question over the chat on screen; its own state, never
  // useChat's runtimes (see useSideChat).
  const side = useSideChat(API, auth.user);
  // The panel belongs to the chat it was opened on: switching away from it,
  // or that chat being deleted, discards the side chat.
  useEffect(() => {
    if (side.mainId == null) return;
    if (chat.activeConvId !== side.mainId || !chat.conversations.some(c => c.id === side.mainId)) side.close();
  }, [chat.activeConvId, chat.conversations, side.mainId, side.close]);

  // Onay kartı teslimi SAĞLAYICIDAN BAĞIMSIZ. Eskiden `effectiveProvider ===
  // 'subscription' && chat.loading` koşuluna bağlıydı; ikisi de yanlıştı, çünkü
  // kartı üreten köprü sağlayıcıyı hiç bilmiyor ve CLI sağlayıcıları sohbet
  // "idle" görünürken de araç çağırabiliyor. Kalan tek koşul token'ın kurulmuş
  // olması — o da izin değil, erişilebilirlik: yoklama `X-Session-Token`
  // istiyor ve `useAuth` onu IPC'den aldıktan sonra axios'a yazıyor.
  const mcp = useMCPApproval({
    API,
    // `!tokenError` de şart: token alınamadıysa elimizdeki `'local'` YANLIŞ ve
    // her yoklama 401 alacak. Bilinen bozuk bir kimlikle saniyede bir istek
    // atmak teşhisi zorlaştırmaktan başka bir şey yapmıyor; kullanıcı zaten
    // ayrı bir hata toast'ı görüyor (dış denetim: `invalid-auth-readiness`).
    enabled: !auth.isLoading && !auth.tokenError,
    // Kartı GİZLEMEK için değil, hangi projeden geldiğini yazmak için.
    // Karar 2026-07-29: eşleşmeyen istek gizlenirse unityMCP'yi doğrudan başka
    // bir istemciye bağlamış kullanıcı 180 sn'lik sessiz bir redde kilitlenir.
    workspacePath: fs.workspacePath,
    setPendingGenFiles: fs.setPendingGenFiles,
    setPendingDelete: fs.setPendingDelete,
    setPendingCommand: chat.setPendingCommand,
    setPendingFix: chat.setPendingFix,
    showToast: showToast as any,
    // A request owned by a chat is drawn only in that chat; the rest of the
    // time its sidebar row says it is waiting.
    screenConvId: chat.activeConvId,
    onOwnersChange: chat.setBridgeGates,
  });

  // API URL'yi window'a set et — ChatPanel ve diğer bileşenler erişebilsin
  useEffect(() => { if (API) (window as any).__API__ = API; }, [API]);

  // Oturum token'ı alınamadıysa kullanıcıya SÖYLE. Konsola yazmak yetmiyor:
  // bu durumda backend'e giden her istek 401/503 alıyor ve ürün "çalışıyor ama
  // hiçbir şey olmuyor" haline geliyor — onay kartı yolu dahil.
  useEffect(() => {
    if (auth.tokenError) showToast(`${auth.tokenError} ${t('home.tokenErrorSuffix')}`, 'error' as any);
  }, [auth.tokenError]);

  // --- Initialization ---
  useEffect(() => {
    if (auth.isLoading) return;
    if (auth.user && API) {
      ai.fetchAvailableModels();
      ai.fetchProvidersWithKeys(auth.user.id);
      fs.fetchLastWorkspace(auth.user.id);
      chat.fetchConversations(auth.user.id); // Eksik parça buydu!
    }
  }, [auth.isLoading, auth.user, API]);

  // Ayarlar açılınca model listesini tazele: oradaki öneri çipleri artık CANLI
  // listeden geliyor, ve liste kullanıcının anahtarına bağlı. Açılışta bir kez
  // çekmek yetmiyor — kullanıcı anahtar ekleyip aynı ekranda öneri bekliyor.
  useEffect(() => {
    if (!ai.showSettings) return;
    ai.fetchAvailableModels();
    // The "Varsayılan model" row shows the default for a new chat, which can differ from
    // the chat on screen; read it fresh on every opening.
    ai.fetchDefaultConfig();
  }, [ai.showSettings]); // eslint-disable-line react-hooks/exhaustive-deps

  // The settings screen (round 11) replaces the modal: one page at a time. Every entry point
  // names its page: Ayarlar -> Genel, the top-bar shield -> Onay modu, the phone icon ->
  // Uzaktan kontrol, the model menu's "Kullanım ve hesaplar" -> Modeller.
  const [settingsPage, setSettingsPage] = useState<SettingsPage>('genel');
  const { setShowSettings, setIsModelDropdownOpen } = ai;
  const openSettings = useCallback((page: SettingsPage) => {
    setSettingsPage(page);
    setIsModelDropdownOpen(false);
    setShowSettings(true);
  }, [setShowSettings, setIsModelDropdownOpen]);
  const closeSettings = useCallback(() => setShowSettings(false), [setShowSettings]);

  // Maker profile (mockup screen 2): replaces the chat stage, opened from the sidebar card and
  // from Settings > Hesap. One stats hook feeds both the profile and the sidebar card's level,
  // so the backend's one-time "new achievement" mark is not consumed by the wrong reader.
  const profileStats = useProfileStats({
    api: API, token: auth.user?.sessionToken,
    enabled: backendReady && !!auth.user && !auth.tokenError,
  });
  const [profileOpen, setProfileOpen] = useState(false);
  const { refresh: refreshProfile } = profileStats;
  const openProfile = useCallback(() => {
    setShowSettings(false);
    setIsModelDropdownOpen(false);
    setProfileOpen(true);
    void refreshProfile();
  }, [setShowSettings, setIsModelDropdownOpen, refreshProfile]);
  const closeProfile = useCallback(() => setProfileOpen(false), []);

  // 5-hour and weekly subscription usage for the model chip, the menu and the Modeller page.
  const usage = useUsageLimits({
    api: API, token: auth.user?.sessionToken, menuOpen: ai.isModelDropdownOpen,
    enabled: backendReady && !!auth.user && !auth.tokenError,
  });

  // The strip's "Kota" group follows the chat's own model, as the model chip does.
  const stripUsageKey = activeProviderKey(ai.aiConfig.provider_type, ai.aiConfig.model_name || '');
  const stripUsageFamily = stripUsageKey && CLI_GROUPS.some(g => g.key === stripUsageKey)
    ? GROUP_USAGE_FAMILY[stripUsageKey] ?? null : null;

  // Per-chat model: the selector, and everything derived from `ai.aiConfig`
  // (effort caps, slash catalog, Codex/ultracode checks, the gate below), shows
  // the chat on screen's own model; with no chat, the default for a new one.
  useEffect(() => {
    if (auth.isLoading || !auth.user || !API) return;
    ai.showChatModel(auth.user.id, chat.activeConvId);
  }, [auth.isLoading, auth.user, API, chat.activeConvId]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Sohbet kapısı: seçili sağlayıcı gerçekten kullanılabilir mi? ---
  // Model/sağlayıcı DEĞİŞTİĞİNDE de yeniden ölçülüyor: kullanıcı anahtarı olmayan
  // bir modele geçtiği anda kapı kapanmalı, bir sonraki mesajı beklemeden.
  useEffect(() => {
    if (auth.user && API) ai.fetchProviderReady(auth.user.id);
  }, [auth.user, API, ai.aiConfig.provider_type, ai.aiConfig.model_name]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Auto-Load Last Workspace ---
  useEffect(() => {
    if (fs.lastWorkspacePath && !fs.workspacePath && backendReady && !hasAutoLoadedRef.current) {
      fs.selectWorkspace(fs.lastWorkspacePath);
      hasAutoLoadedRef.current = true;
    }
  }, [fs.lastWorkspacePath, fs.workspacePath, backendReady]);

  // The workspace is persisted by `useFileSystem.selectWorkspace` and by
  // nothing else. This page used to run a second writer here, on a
  // `fs.workspacePath` effect: every ordinary selection produced TWO mappings
  // and TWO posts of the same value, and because each one awaited the bridge
  // before posting, selections A then B could land in the order B, A - the
  // database on A while the UI showed B. `setWorkspacePath` has exactly one
  // caller (`selectWorkspace`; `closeWorkspace` only clears it), so the effect
  // covered no case that writer does not.

  // --- UI State ---
  // Hydration requires a deterministic 'en' render in both SSR and the client.
  // Reading storage during render could make their translated text disagree,
  // so the stored language is loaded only after mounting.
  const [lang, setLangState] = useState<Lang>('en');
  useEffect(() => {
    try {
      const stored = localStorage.getItem('app-lang');
      setLangState(stored === 'tr' || stored === 'en' ? stored : 'en');
    } catch {
      setLangState('en');
    }
  }, []);
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const setLang = (l: Lang) => {
    setLangState(l);
    // A denied storage must not break the language switch itself.
    try { localStorage.setItem('app-lang', l); } catch { /* keep the in-memory choice */ }
  };
  const t = (key: string, degerler?: TValues) => ceviriUygula(lang, key, degerler);
  // Announce state to non-React consumers before they render translated text.
  // Storage alone cannot represent the initial English hydration state.
  // boyamadan önce bir toast üretilirse o da doğru dilde olmalı.
  aktifDilAyarla(lang);

  // Sohbet kapısı — karar `providerGate`'te, burada DEĞİL: aynı koşul composer'ın
  // `disabled`'ı ve gönderme kapısı tarafından tüketiliyor ve iki kopya ayrışır.
  const sohbetKilitli = sohbetKilitliMi(ai.aiConfig.provider_type, ai.providerReady);
  // Tek kavramsal effort skalası — GÖSTERİLEN seviyeler backend kayıtçısından gelir
  // (/effort-capabilities): provider+model neyi destekliyorsa o. 'auto' = model
  // varsayılanı, hiçbir parametre gönderilmez.
  const [thinkingLevel, setThinkingLevel] = useState<ThinkingLevel>('auto');
  // A chat woken by a note from another chat runs with what this page shows
  // now, even before the user has sent anything this session.
  const { setWakeDefaults } = chat;
  useEffect(() => {
    setWakeDefaults({
      lang, genMode: chat.generationMode, thinkingLevel,
      setPendingGenFiles: fs.setPendingGenFiles, setPendingDelete: fs.setPendingDelete,
    });
  }, [setWakeDefaults, lang, chat.generationMode, thinkingLevel, fs.setPendingGenFiles, fs.setPendingDelete]);
  const [effortCaps, setEffortCaps] = useState<EffortCaps | null>(null);
  // Claude-only kontrol (ultracode). Sadece subscription + claude-* modelde.
  const [ultracode, setUltracode] = useState(false);
  const isClaudeSub = ai.effectiveProvider === 'subscription' && (ai.aiConfig?.model_name || '').startsWith('claude-');
  useEffect(() => {
    if (!isClaudeSub) setUltracode(false);
  }, [isClaudeSub]);
  // Provider/model değişince yetenekleri çek; mevcut seçim yeni listede yoksa auto'ya kıstır.
  useEffect(() => {
    if (!API || !auth.user) return;
    const provider = ai.effectiveProvider || '';
    const model = ai.aiConfig?.model_name || '';
    axios.get(`${API}/effort-capabilities`, {
      params: { provider, model },
      headers: { 'X-Session-Token': auth.user?.sessionToken },
    }).then(r => {
      const caps = r.data as EffortCaps;
      setEffortCaps(caps);
      setThinkingLevel(prev => (caps?.levels || []).includes(prev) ? prev : 'auto');
    }).catch(() => setEffortCaps(null));
  }, [API, auth.user, ai.effectiveProvider, ai.aiConfig?.model_name]);
  // The one way to choose an effort level: a click in the effort panel and a
  // level asked for from a phone both come here, so they cannot drift apart.
  // Ultracode overrides the level, so choosing one switches it off.
  const chooseEffort = useCallback((level: ThinkingLevel) => {
    setThinkingLevel(level);
    if (isClaudeSub && ultracode) setUltracode(false);
  }, [isClaudeSub, ultracode]);
  // The phone page shows and sets this page's effort; see lib/remoteControl.ts.
  useRemoteEffort({
    api: API, token: auth.user?.sessionToken, level: thinkingLevel,
    levels: effortCaps?.levels ?? null, ultracode: isClaudeSub && ultracode,
    setLevel: chooseEffort, showToast,
  });
  useRemoteUi({ api: API, token: auth.user?.sessionToken, lang, theme: useAppearance().appearance.theme });
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  // The right column is the workspace (Sahne / Dosyalar / Kod / Önizleme + the terminal drawer)
  // since the v4 column swap; the chat is the main stage and is never hidden. Width modes
  // dar / yarim / odak replace P2's drag handle (lib/workspacePanel.ts).
  const ws = useWorkspacePanel();
  const { reveal: wsReveal, setOpen: setWsOpen, setTab: setWsTab } = ws;
  const [sceneEditorSetting] = useSceneEditorSetting();
  const editorOn = sceneEditorSetting && ai.unityMcpStatus !== 'off';
  const [hierarchyVisible, setHierarchyVisible] = useState(false);
  // The file change a card is waiting on, with that card's own Accept / Reject handlers.
  const pendingChange = usePendingChange();
  const [reportsOpen, setReportsOpen] = useState(false);
  // The composer's attach/video pickers, opened from the strip's "Add & chat" menu.
  const composerPickers = useRef<ComposerPickers>(null);
  const [isEditorFocused, setIsEditorFocused] = useState(false);
  const [isTerminalOpen, setIsTerminalOpen] = useState(false);

  useEffect(() => {
    if (!ipc) return;
    const off1 = ipc.on('menu-toggle-terminal', () => {
      toggleTerminalDrawer(ws.open, isTerminalOpen, setWsOpen, setIsTerminalOpen);
    });
    const off2 = ipc.on('menu-open-terminal', () => {
      setWsOpen(true);
      setIsTerminalOpen(true);
    });
    const off3 = ipc.on('menu-clear-terminal', () => showToast(t('home.terminalCleared'), 'info'));
    return () => {
      if (typeof off1 === 'function') off1();
      if (typeof off2 === 'function') off2();
      if (typeof off3 === 'function') off3();
    };
  }, [ipc, ws.open, isTerminalOpen, setWsOpen, showToast, t]);
  const [projectProblems, setProjectProblems] = useState<Record<string, any[]>>({});
  // Chat'te '/' autocomplete için Claude Code slash komutları + skill'ler (backend'den)
  const [slashCommands, setSlashCommands] = useState<string[]>([]);
  const [skills, setSkills] = useState<string[]>([]);
  const [commandMeta, setCommandMeta] = useState<{ name: string; description?: string; argumentHint?: string; insert?: string; displayName?: string }[]>([]);
  // Hangi provider'ın komut/skill kataloğu çekilecek? (backend buna göre kaynak seçer)
  //   gpt-* → codex (app-server skills/list), gemini/agy-* → agy (boş, headless),
  //   diğer subscription → claude (Claude Code slash + skill). subscription değilse katalog yok.
  const slashProvider = (() => {
    if (ai.effectiveProvider !== 'subscription') return 'other';
    const m = (ai.aiConfig?.model_name || '').toLowerCase();
    if (m.startsWith('gpt-')) return 'codex';
    if (m.startsWith('gemini') || m.startsWith('agy-')) return 'agy';
    return 'claude';
  })();
  useEffect(() => {
    if (!API || slashProvider === 'other') { setSlashCommands([]); setSkills([]); setCommandMeta([]); return; }
    axios.get(`${API}/slash-commands`, { params: { provider: slashProvider }, headers: { 'X-Session-Token': auth.user?.sessionToken } })
      .then(r => { setSlashCommands(r.data?.commands || []); setSkills(r.data?.skills || []); setCommandMeta(r.data?.meta || []); })
      .catch(() => {});
  }, [API, chat.loading, slashProvider]);  // mesaj bitince session dolar + provider değişince yeniden çek
  const chatScroll = useAutoScroll();
  const chatEndRef = chatScroll.endRef;
  // "There is something new below" badge. Separate from `isPinned`: the button
  // is only worth showing when content actually arrived while the user was up
  // there — scrolling up in an idle chat should not pop a call to action.
  const [hasUnreadBelow, setHasUnreadBelow] = useState(false);
  const openedFileRef = useRef<string | null>(null);

  useEffect(() => {
    openedFileRef.current = fs.openedFilePath;
  }, [fs.openedFilePath]);

  // --- Canlı diagnostics (OmniSharp sidecar) ---
  // OmniSharp sidecar durumu (starting → üst barda "C# analizi hazırlanıyor…" rozeti)
  const { lspStatus, inProject: csInProject } = useLiveDiagnostics({
    API, sessionToken: auth.user?.sessionToken, openedFilePath: fs.openedFilePath,
    workspacePath: fs.workspacePath, code: fs.code, setProjectProblems,
  });

  const flattenedProblems = useMemo(() => {
    return Object.values(projectProblems)
      .flat()
      .filter((p: any) => !p.file || !p.file.includes('Assets/Plugins/'));
  }, [projectProblems]);

  const [diffFile, setDiffFile] = useState<any>(null);

  // New chat content follows the user's reading position: it scrolls only while
  // the user is at the bottom. This used to be unconditional and yanked the view
  // down mid-read on every streamed chunk.
  //
  // Approval/question cards are deliberately NOT in this effect — see the forced
  // scroll below.
  useEffect(() => {
    if (!chatScroll.followIfPinned()) setHasUnreadBelow(true);
  }, [chat.messages, chat.loading, chatScroll.followIfPinned]);

  // Decision cards are FORCED into view, whatever the user was reading.
  //
  // `mcp.activeGate` was added to this dependency list because the card lives
  // outside the message list: when it arrived `messages`/`loading` did not change
  // and nothing scrolled to it. In a long chat the card stayed below the fold and
  // the request was rejected after 180 s without the user ever seeing it (external
  // audit: `approval-card-hidden-by-view-state`, one of its three legs).
  //
  // The other three gates are here for the same reason, one level down: they are
  // rendered inside the message list, but an unanswered card the user cannot see
  // is the same failure — a request nobody can answer. Being scrolled up is not
  // consent to miss it, so this branch ignores the pin and re-arms following.
  useEffect(() => {
    if (!mcp.activeGate && !chat.pendingCommand && !chat.pendingQuestion && !fs.pendingDelete) return;
    chatScroll.scrollToBottom();
    setHasUnreadBelow(false);
  }, [mcp.activeGate, chat.pendingCommand, chat.pendingQuestion, fs.pendingDelete, chatScroll.scrollToBottom]);

  // Reaching the bottom by hand clears the badge and re-arms following on its
  // own — the user does not have to press the button to get auto-scroll back.
  useEffect(() => {
    if (chatScroll.isPinned) setHasUnreadBelow(false);
  }, [chatScroll.isPinned]);

  // Switching conversations starts a fresh read: a pin left `false` by the
  // previous chat would open the new one scrolled to the top with a stale
  // "new messages" badge.
  useEffect(() => {
    chatScroll.repin();
    setHasUnreadBelow(false);
  }, [chat.activeConvId, chatScroll.repin]);

  // Approval cards used to live in a closable right-hand chat panel, so an effect re-opened the
  // panel whenever any of the four gates (or the unknown-source tray) arrived: a card drawn at
  // 0 px width is a request nobody can answer (30 Aug 2026 audit). Since the v4 column swap the
  // chat is the main stage and has no closed state, so every card and the tray are always on
  // screen and that effect has nothing left to open.

  // Every open request names its tab and opens the panel (wsReveal): asking again for the file
  // already open must still bring a closed panel back, which an effect on the opened path cannot
  // do because the path did not change (P2 audit). Text goes to Kod, images and models to
  // Önizleme; from the narrow width both open at half (mockup HOOKS round 7).
  const openInPanel = useCallback((path: string) => {
    wsReveal(tabForPath(path));
    void fs.openFile(path);
  }, [wsReveal, fs.openFile]);
  const previewInPanel = useCallback((path: string) => {
    wsReveal('onizleme');
    fs.openPreview(path);
  }, [wsReveal, fs.openPreview]);

  // Things that arrive without a click still have to be visible: a file opened by the sidebar's
  // "Open file" picker or a definition jump, a preview, a change a card waits on, the terminal
  // opened from the app menu. They show their tab without widening a panel the user sized.
  // Which tab is decided by contentPane's precedence: a change a card is asking about outranks an
  // open preview, so the card never asks about something off screen.
  const arrived = contentPane(fs.previewFile, pendingChange, fs.openedFilePath);
  useEffect(() => {
    if (arrived === 'hero') return;
    setWsOpen(true);
    setWsTab(arrived === 'preview' ? 'onizleme' : 'kod');
  }, [arrived, fs.previewFile, fs.openedFilePath, pendingChange?.id, setWsOpen, setWsTab]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (isTerminalOpen) setWsOpen(true); }, [isTerminalOpen, setWsOpen]);

  const [seen, setSeen] = useState<Seen | null>(null);
  useEffect(() => {
    setSeen(fs.workspacePath ? loadSeen(fs.workspacePath) : null);
  }, [fs.workspacePath]);
  const onAckChanges = useCallback(() => {
    if (!fs.workspacePath) return;
    setSeen(saveSeen(fs.workspacePath, fs.gitStatus.files, Date.now()));
  }, [fs.workspacePath, fs.gitStatus.files]);
  const onShowAllChanges = useCallback(() => {
    if (!fs.workspacePath) return;
    clearSeen(fs.workspacePath);
    setSeen(null);
  }, [fs.workspacePath]);

  // Sahne's "Changed files": the git status the file tree already polls every 20 s. Its keys are
  // lowercased absolute paths (main/background.ts git-status), so the case is taken back from the
  // tree entries read from disk where they are known; elsewhere the path keeps the workspace
  // root's case and a lowercased tail, which still opens on a case-insensitive disk. Capped so a
  // repo with thousands of untracked files cannot flood the pane.
  const changedFiles = useMemo(() => {
    const root = fs.workspacePath;
    if (!root || !fs.gitStatus?.isRepo) return { shown: [] as ChangedFile[], total: 0, seenHidden: 0 };
    const slash = (p: string) => p.replace(/\\/g, '/');
    const known = new Map<string, string>();
    for (const e of fs.fileTree || []) known.set(slash(e.path).toLowerCase(), e.path);
    for (const list of Object.values(fs.dirContents || {})) for (const e of list) known.set(slash(e.path).toLowerCase(), e.path);
    const rootN = slash(root).replace(/\/+$/, '');
    const rootL = `${rootN.toLowerCase()}/`;
    const out: ChangedFile[] = [];
    let seenHidden = 0;
    for (const [key, status] of Object.entries(fs.gitStatus.files)) {
      const k = slash(key);
      if (!k.startsWith(rootL)) continue;
      if (!isUnseen(key, status, fs.gitStatus.mtimes?.[key], seen)) {
        seenHidden++;
        continue;
      }
      const tail = k.slice(rootL.length);
      out.push({ path: known.get(k) ?? `${root.replace(/[\\/]+$/, '')}/${tail}`, rel: tail, status });
    }
    out.sort((a, b) => a.rel.localeCompare(b.rel));
    return { shown: out.slice(0, 200), total: out.length, seenHidden };
  }, [fs.gitStatus, fs.workspacePath, fs.fileTree, fs.dirContents, seen]);

  // --- Desktop notifications (background chats) ---
  const screenCardOpen = !!fs.pendingDelete || !!fs.pendingGenFiles;
  useChatNotifications({
    conversations: chat.conversations,
    activeConvId: chat.activeConvId,
    attention: chat.attention,
    trayGates: mcp.unknownGates,
    bridgeSynced: mcp.synced,
    screenCardOpen,
    onOpenConversation: (conv) => { chat.selectConversation(conv); },
  });
  // The on-screen counterpart of the "finished" notification: the task band.
  const turnDone = useTurnDone(chat.attention, chat.activeConvId, cardOnScreen({
    pendingDelete: fs.pendingDelete, pendingGenFiles: fs.pendingGenFiles,
    pendingFix: chat.pendingFix, activeGate: mcp.activeGate,
  }));
  const achievementBand = useAchievementQueue(
    turnDone, chat.conversations.find(c => c.id === chat.activeConvId)?.title,
    profileStats.gain, profileStats.unlocked,
  );
  // A finished task moves the sidebar card's XP: re-read the profile numbers.
  useEffect(() => {
    if (turnDone) void refreshProfile();
  }, [turnDone?.seq]); // eslint-disable-line react-hooks/exhaustive-deps
  // Switching chats by any route (shortcut, notification click) leaves the profile.
  useEffect(() => { setProfileOpen(false); }, [chat.activeConvId]);

  // --- Save Shortcut (Ctrl+S / Cmd+S) ---
  useEffect(() => {
    const handleKeyDown = async (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        // The tour overlay (or any modal dialog) owns the keyboard: Ctrl+S in its name field must
        // not save the file behind it (guide audit, 2 Oct 2026).
        if (document.querySelector('[data-testid="guide-tour"], [aria-modal="true"]')) return;
        await fs.saveFile();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [fs.saveFile, fs.workspacePath, auth.user]);

  // --- New chat shortcut (Ctrl+N / Cmd+N), the one the sidebar's "New chat" row names.
  // Guarded (terminal, inputs, repeat, open modal): see lib/newChatShortcut.ts. ---
  useNewChatShortcut(chat.createNewConversation);

  const handleLogout = () => { fs.closeWorkspace(); };

  const handleProblemClick = async (problem: any) => {
    if (!problem.file || !fs.workspacePath) return;
    // `problem.file` is the BACKEND's spelling. Usually relative (the manager
    // sends `os.path.relpath` output) and then mode-independent, but when the
    // backend has no workspace root it reports the absolute container path,
    // which names nothing on this side of the mount. Same rule as a definition
    // result, in one shared place, so the two return legs cannot drift.
    const hedef = await hostOpenTarget(problem.file);
    if (hedef) openInPanel(hedef);
  };

  const handleSendMessage = async (msg?: string, images?: string[], videos?: any[]) => {
    // `??`, not `||`: an image-only send passes '' and must not pick up the parent's copy, which
    // typing never updates (a card's deleted prompt went out with the image).
    const draft = msg ?? chat.chatInput;
    let input = draft.trim();
    if (!input && (!images || images.length === 0) && (!videos || videos.length === 0)) return;

    // Send-gate: aktif bulut sağlayıcının API key'i yoksa göndermeyi engelle (net uyarı +
    // Ayarlar). Optimistic model seçimiyle beraber → keyless modele geçilse bile sessizce
    // yanlış sağlayıcıya düşmek yerine kullanıcı net yönlendirilir.
    // Send-gate: bir sağlayıcı BAĞLANMADAN sohbet kullanılmıyor (ürün kararı,
    // 8 Ağu 2026 — kullanıcıya habersiz hiçbir şey kurmuyoruz, dolayısıyla
    // sağlayıcısı olmayan kullanıcı bozuk bir sohbete de düşmemeli).
    // Eski hâli yalnız BULUT anahtarını kontrol ediyordu ve `subscription`ı muaf
    // tutuyordu; sonuç ölçüldü: CLI'ı kurulu olmayan kullanıcı hiçbir uyarı
    // görmeden mesaj gönderiyor ve ilk cevap olarak ham bir Python istisnası
    // alıyordu. Kontrol artık sağlayıcı tipine göre backend'de yapılıyor.
    const _hazir = ai.providerReady;
    if (sohbetKilitliMi(ai.aiConfig.provider_type, _hazir)) {
      const _sebep = _hazir?.needs ? t(`gate.needs.${_hazir.needs}`) : t('gate.hint');
      showToast(`${_sebep} (${_hazir?.provider ?? ''})`.trim(), 'warning');
      openSettings('modeller');
      return;
    }

    const fileMatches = input.match(/\[File Attached: (.*?)\]/g);
    if (fileMatches && ipc) {
      for (const match of fileMatches) {
        const path = match.match(/\[File Attached: (.*?)\]/)?.[1];
        if (path) {
          try {
            const result = await ipc.invoke('read-file', path, fs.workspacePath);
            if (result && result.content) {
              const fileContent = `\n\n--- FILE: ${path} ---\n${result.content}\n--- END FILE ---`;
              input = input.replace(match, fileContent);
            }
          } catch (err) { input = input.replace(match, t('home.fileReadFailed', { yol: path })); }
        }
      }
    }
    // Diskteki açık dosyayı her mesajda yeniden modele basma. Ajan gerektiğinde
    // dosyayı kendisi okuyabilir; yalnız kaydedilmemiş buffer ayrıca gönderilir.
    const unsavedEditorContext = getUnsavedEditorContext(fs.code, fs.isDirty);
    chat.sendMessage(input, unsavedEditorContext, lang, chat.generationMode, thinkingLevel, fs.setPendingGenFiles, fs.setPendingDelete, images, ultracode, videos, 'user', undefined, draft);
  };

  const toggleSideChat = async (convId: number) => {
    if (side.mainId === convId) { side.close(); return; }
    if (!(await side.open(convId))) showToast(t('side.openFailed'), 'error');
  };

  const askSideQuestion = (question: string) => {
    // The main chat's answer as it stands on screen, only while it streams:
    // a finished answer is already in the transcript the backend sends.
    const lastAnswer = [...chat.messages].reverse().find(m => m.role === 'assistant');
    void side.ask(question, {
      liveContext: chat.loading ? (lastAnswer?.content || '') : '',
      lang,
      thinkingLevel,
    });
  };

  const addSideAnswerToMain = (question: string, answer: string) => {
    const quote = sideQuote(question, answer, {
      question: t('side.quoteQuestion'), answer: t('side.quoteAnswer'),
    });
    const add = (prev: string) => (prev.trim() ? `${prev}\n\n${quote}` : quote);
    if (composerPickers.current) composerPickers.current.editDraft(add); else chat.setChatInput(add);
    showToast(t('side.added'), 'success');
  };

  // --- Guide (Rehber) and the first-launch tour (round 12b) ---
  // The prepare actions run the app's own controls; leaving a topic gives back the panel, the
  // drawer and (for a topic) the chat it started on. See hooks/home/useGuide.ts.
  const [drawerTabRequest, setDrawerTabRequest] = useState<{ tab: DrawerTab } | null>(null);
  // The drawer's own active tab (reported by TerminalPanel), for the guide snapshot.
  const drawerTabRef = useRef<DrawerTab>('terminal');
  const guideSnapRef = useRef<GuideSnapshot | null>(null);
  const guidePeekRef = useRef(false);
  // A chat switch the guide makes itself must not close the guide it is about to show again.
  const guideNavRef = useRef<number | null | undefined>(undefined);
  const guideGoTo = (convId: number | null) => {
    if (chat.activeConvId === convId) return;
    guideNavRef.current = convId;
    if (convId == null) { chat.setActiveConvId(null); return; }
    const conv = chat.conversations.find(c => c.id === convId);
    if (conv) void chat.selectConversation(conv);
  };
  const guideHost: GuideHost = {
    screen: (a) => {
      if (a === 'profile') { openProfile(); return; }
      closeSettings();
      setProfileOpen(false);
      if (a === 'new_chat' && !isChatEmpty(chat.activeConvId, chat.messages.length, chat.loading, !!mcp.activeGate)) guideGoTo(null);
    },
    settings: (page) => openSettings(GUIDE_SETTINGS[page]),
    'workspace.tab': (tab) => { setWsOpen(true); setWsTab(GUIDE_TABS[guideWorkspaceTab(tab, editorOn)]); },
    'workspace.width': (w) => { setWsOpen(true); ws.setWidth(GUIDE_WIDTHS[w]); },
    'workspace.peek': () => { if (!ws.open) { guidePeekRef.current = true; setWsOpen(true); } },
    // No "most recent asset" list exists: the Preview tab shows what is open, or its empty state.
    'preview.open': () => { setWsOpen(true); setWsTab('onizleme'); },
    drawer: (tab) => { setWsOpen(true); setIsTerminalOpen(true); setDrawerTabRequest({ tab: GUIDE_DRAWER[tab] }); },
    menu: () => { ai.fetchAvailableModels(); setIsModelDropdownOpen(true); },
    showGuideScreen: () => { closeSettings(); setProfileOpen(false); setIsModelDropdownOpen(false); },
    snapshot: () => {
      const snap: GuideSnapshot = { open: ws.open, width: ws.width, tab: ws.tab, terminal: isTerminalOpen, drawerTab: drawerTabRef.current, convId: chat.activeConvId };
      guideSnapRef.current = snap;
      return snap;
    },
    unprepare: (prep) => {
      const snap = guideSnapRef.current;
      if (!wants(prep, 'menu')) setIsModelDropdownOpen(false);
      if (!wants(prep, 'drawer') && snap) setIsTerminalOpen(snap.terminal);
      if (!wants(prep, 'workspace.peek') && guidePeekRef.current) { guidePeekRef.current = false; setWsOpen(snap?.open ?? false); }
    },
    restore: (s, mode) => {
      const snap = s as GuideSnapshot | null;
      guideSnapRef.current = null;
      guidePeekRef.current = false;
      setIsModelDropdownOpen(false);
      if (!snap) return;
      setIsTerminalOpen(snap.terminal);
      // A step may have left the drawer on another tab (Problems); put the original back
      // (guide audit, 2 Oct 2026).
      setDrawerTabRequest({ tab: snap.drawerTab });
      setWsOpen(snap.open);
      ws.setWidth(snap.width);
      setWsTab(snap.tab);
      // The core tour ends on the new chat; a topic goes back to the chat it started on.
      if (mode === 'topic') guideGoTo(snap.convId);
    },
    focusComposer: () => {
      requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus({ preventScroll: true }));
    },
  };
  const isGitRepo = !!fs.gitStatus?.isRepo;
  const guideCtx = useMemo(() => ({ caps: capabilities({ isGitRepo }) }), [isGitRepo]);
  const guide = useGuide({
    ctx: guideCtx, host: guideHost, userName: me.name, saveName: me.saveName, appVersion: APP_VERSION,
    frameReady: !!fs.workspacePath && backendReady && !auth.isLoading,
  });
  const { closeGuide } = guide;
  const sceneFrameVisible = !!fs.workspacePath && !backendError && !ai.showSettings;
  const sceneEditor = useSceneEditor({ api: API, token: auth.user?.sessionToken, editorOn,
    unityStatus: ai.unityMcpStatus, hierarchyVisible: sceneFrameVisible && hierarchyVisible,
    inspectorVisible: sceneFrameVisible && !profileOpen && !guide.guideOpen && ws.open && ws.tab === 'sahne' });
  const selectSceneObject = useCallback((id: number) => {
    sceneEditor.select(id); closeProfile(); closeGuide(); wsReveal('sahne');
  }, [sceneEditor.select, closeProfile, closeGuide, wsReveal]);
  // Another screen (settings, profile) or another chat replaces the guide.
  useEffect(() => { if (ai.showSettings || profileOpen) closeGuide(); }, [ai.showSettings, profileOpen, closeGuide]);
  useEffect(() => {
    if (guideNavRef.current !== undefined && guideNavRef.current === chat.activeConvId) { guideNavRef.current = undefined; return; }
    guideNavRef.current = undefined;
    closeGuide();
  }, [chat.activeConvId]); // eslint-disable-line react-hooks/exhaustive-deps

  const langCtxValue = { lang, setLang, t };


  if (backendError) {
    return (
      <div className="h-screen bg-black flex flex-col items-center justify-center text-center p-6">
        <div className="text-red-500 text-5xl mb-4">⚠</div>
        <h2 className="text-white text-xl font-bold mb-2">{t('home.backendFailed')}</h2>
        <p className="text-slate-400 max-w-md">{t('home.backendFailedHint')}</p>
      </div>
    );
  }

  if (!fs.workspacePath) {
    return (
      <LangContext.Provider value={langCtxValue}>
        {/* `auth.user` NULL OLABİLİR ve bu dosyanın geri kalanı bunu zaten
            biliyor: 20 satır aşağıda `auth.user?.sessionToken ?? ''`, başlıkta
            `auth.user?.name || 'Giriş'`. Korumasız kalan tek okuma buydu ve
            `home.tsx`'te oturum için bir erken dönüş YOK — yani kullanıcı henüz
            yüklenmemişken bu dal çizilirse TypeError fırlar ve (hata sınırı
            olmadan) pencere komple boşalırdı. Ad yerine boş metin: yanlış bir
            ad göstermektense selamlamayı adsız bırakmak dürüst olan. */}
        <WorkspaceScreen
          api={API} user={auth.user} userName={me.name}
          onOpenFolder={fs.openFolder} onSelectWorkspace={fs.selectWorkspace}
          onLogout={handleLogout} showToast={showToast as any}
        />
        {/* The welcome screen's own messages (drop errors, Unity Hub fallback, a missing folder)
            need the toast host too; it was only mounted in the workspace branch. */}
        <ToastContainer toasts={toasts} onDismiss={dismissToast} />
        {/* Workspace seçilmemişken de onay kartı gelebilir: unityMCP köprüsü
            ürünün penceresinden bağımsız çalışıyor (kullanıcı unityMCP'yi
            doğrudan başka bir istemciye bağlamış olabilir). Bu dal olmadan kart
            state'e giriyor ama ekrana HİÇ çıkmıyordu ve köprü 180 sn sonra
            reddediyordu — ürün "güvenli" görünüp kullanılamaz hale geliyordu
            (dış denetim: `approval-card-hidden-by-view-state`).
            Editör yok, o yüzden setDiffFile/onOpenFile/setCode verilmiyor. */}
        {(mcp.activeGate || mcp.unknownGates.length > 0) && (
          <div className="tray-dock fixed inset-x-0 bottom-0 z-[250] max-h-[70vh] overflow-y-auto py-3">
            <div className="mx-auto max-w-3xl">
              <McpUnknownTray
                gates={mcp.unknownGates}
                apiBase={API}
                sessionToken={auth.user?.sessionToken ?? ''}
                showToast={showToast as any}
                phonePaired={phonePaired}
              />
              <McpApprovalCards
                gate={mcp.activeGate}
                workspaceMismatch={mcp.gateWorkspaceMismatch}
                workspaceCheckPending={mcp.gateWorkspaceCheckPending}
                openWorkspacePath={mcp.openWorkspacePath}
                onResolved={mcp.resolveActiveGate}
                apiBase={API}
                sessionToken={auth.user?.sessionToken ?? ''}
                showToast={showToast as any}
                refreshFileTree={fs.refreshFileTree}
                pendingGenFiles={fs.pendingGenFiles}
                setPendingGenFiles={fs.setPendingGenFiles}
                pendingDelete={fs.pendingDelete}
                setPendingDelete={fs.setPendingDelete}
                pendingCommand={chat.pendingCommand}
                setPendingCommand={chat.setPendingCommand}
                pendingFix={chat.pendingFix}
                setPendingFix={chat.setPendingFix}
                phonePaired={phonePaired}
              />
            </div>
          </div>
        )}
      </LangContext.Provider>
    );
  }

  // Content and decisions belong to the same published card, including when cards coexist.
  const kodDiff: WsDiff | null = pendingChange;
  // Branching copies the chat as it stands; mid-turn or with a card open there
  // is no settled "now" to copy (the backend answers 409 for the same case).
  const branchBlocked = chat.loading
    || (chat.activeConvId != null && chat.convStatus[chat.activeConvId] === 'awaiting')
    || !!chat.pendingCommand || !!chat.pendingQuestion || !!fs.pendingDelete || !!fs.pendingGenFiles;
  const familyHasBranches = hasBranches(chat.conversations, chat.activeConvId);
  // Which preview panel the open file belongs to; the two share one slot.
  const previewRoute = fs.previewFile ? routeForFile(fs.previewFile.path) : null;
  const activeConversation = chat.conversations.find(c => c.id === chat.activeConvId) ?? null;
  const projectName = fs.workspacePath?.split(/[\\/]/).filter(Boolean).pop() ?? null;
  // Same predicate ChatPanel uses for "nothing to draw": the empty new chat takes its place.
  const chatEmpty = isChatEmpty(chat.activeConvId, chat.messages.length, chat.loading, !!mcp.activeGate);
  // A request waits in this chat (the title block's approval cell).
  const cardWaiting = !!(chat.pendingCommand || chat.pendingQuestion || fs.pendingDelete || fs.pendingGenFiles || chat.pendingFix || mcp.activeGate);
  // A mission-board card fills the composer; nothing is sent until the user presses Enter. A draft
  // already in the box is kept (questDraft).
  // The composer applies it to the text it really holds: the parent's copy is not updated while
  // typing, so a card deleted by hand still looked present and a second pick did nothing.
  const pickQuest = (prompt: string) => {
    if (composerPickers.current) { composerPickers.current.editDraft(current => questDraft(current, prompt)); return; }
    chat.setChatInput(prev => questDraft(prev, prompt));
  };

  return (
    <LangContext.Provider value={langCtxValue}>
    {/* The v4 frame (shell.css `.app`): sidebar | main | right panel under one top bar.
        data-model drives the model colour (--model) the picker's dot reads. */}
    <div
      className="app"
      data-model={modelFamily(ai.aiConfig?.model_name, ai.effectiveProvider)}
      // Panel width mode (workspace.css): none = dar; the sidebar's state feeds the half / focus math.
      data-ws={ws.open && ws.width !== 'dar' ? ws.width : undefined}
      data-side={isSidebarOpen ? undefined : 'closed'}
      // While the settings screen shows, the rest of the frame is hidden (settings.css), not
      // unmounted: running chats, the terminal and the editor keep their state.
      data-screen={ai.showSettings ? 'ayarlar' : profileOpen ? 'profil' : guide.guideOpen ? 'rehber' : undefined}
    >
      <Head>
        <title>{displayName(me.name) ? `Gamachine | ${displayName(me.name)}` : 'Gamachine'}</title>
        <style>{globalStyles}</style>
      </Head>

      <SettingsScreen
        open={ai.showSettings} page={settingsPage} onPageChange={setSettingsPage}
        aiConfig={ai.aiConfig} availableModels={ai.availableModels} providersWithKeys={ai.providersWithKeys}
        providersWithKeysLoaded={ai.providersWithKeysLoaded}
        onClose={closeSettings} onLogout={handleLogout} onDeleteKey={ai.deleteApiKey}
        defaultModel={ai.defaultConfig} onSaveDefaultModel={ai.saveDefaultModel}
        onSaveApiKey={ai.saveApiKey} onUseCustomModel={ai.applyCustomModel}
        unityMcpStatus={ai.unityMcpStatus} unityMcpToggling={ai.unityMcpToggling} onToggleUnityMcp={ai.toggleUnityMcp}
        unityProjectName={projectName}
        lang={lang} onLangChange={setLang}
        approvalMode={chat.generationMode} onApprovalModeChange={(m) => chat.setGenerationMode(m, 'settings')}
        autoTitles={autoTitles.autoTitles} autoTitlesSaving={autoTitles.autoTitlesSaving}
        onToggleAutoTitles={autoTitles.toggleAutoTitles}
        dictationAutoLang={dictation.autoLanguageCpu} dictationAutoLangSaving={dictation.autoLanguageCpuSaving}
        onToggleDictationAutoLang={dictation.toggleAutoLanguageCpu}
        onRemoteStatus={remote.setStatus}
        usage={usage.data} user={auth.user} API={API} http={axios} showToast={showToast as any}
        userName={me.name} onSaveName={me.saveName}
        onOpenProfile={openProfile} onProfileReset={() => { void profileStats.afterReset(); }}
        onOpenGuide={() => guide.openGuide()} onReplayTour={() => guide.openTour(1)} tourSteps={guide.coreSteps}
      />

      {/* Kept mounted with the rest of the frame: the chat column is only hidden (profile.css). */}
      <ProfileView
        open={profileOpen} onClose={closeProfile}
        data={profileStats.data} range={profileStats.range} onRangeChange={profileStats.setRange}
        loading={profileStats.loading} failed={profileStats.failed} onRetry={() => { void profileStats.refresh(); }}
        userName={me.name}
      />

      {/* The guide takes the same place as the profile: the chat column and the panel are hidden. */}
      <GuideScreen
        open={guide.guideOpen && !ai.showSettings && !profileOpen}
        topics={guide.topics} query={guide.query} onQuery={guide.setQuery}
        isSeen={guide.isSeen} isNew={guide.isNew} coreSteps={guide.coreSteps}
        onPlay={id => guide.playTopic(id)} onTour={() => guide.openTour(1)} onClose={closeGuide}
        focusTopic={guide.focusTopic} onFocused={guide.clearFocusTopic}
      />

      <ExportModal
        exportModal={fs.exportModal} exportFileName={fs.exportFileName} workspacePath={fs.workspacePath}
        onFileNameChange={fs.setExportFileName} onClose={() => fs.setExportModal(null)}
        onChangeExportDir={fs.changeExportDir} onExportSingleFile={fs.exportSingleFile} onExportMultipleFiles={fs.exportMultipleFiles}
      />

      <Sidebar
        isSidebarOpen={isSidebarOpen}
        conversations={chat.conversations} activeConvId={chat.activeConvId} convStatus={chat.convStatus}
        // Picking a chat (even the one already open) or starting one leaves the profile.
        selectConversation={(...a: Parameters<typeof chat.selectConversation>) => { setProfileOpen(false); closeGuide(); return chat.selectConversation(...a); }}
        createNewConversation={(...a: Parameters<typeof chat.createNewConversation>) => { setProfileOpen(false); closeGuide(); return chat.createNewConversation(...a); }}
        deleteConversation={chat.deleteConversation}
        editingId={chat.editingId} setEditingId={chat.setEditingId} tempTitle={chat.tempTitle} setTempTitle={chat.setTempTitle} saveRename={chat.saveRename}
        workspacePath={fs.workspacePath} closeWorkspace={fs.closeWorkspace} isDirty={fs.isDirty}
        user={auth.user} userName={me.name} setShowSettings={(open: boolean) => (open ? openSettings('genel') : closeSettings())} handleLogout={handleLogout}
        onOpenRemote={() => openSettings('uzak')}
        remoteStatus={remote.status}
        unityStatus={ai.unityMcpStatus}
        onHierarchyVisible={setHierarchyVisible}
        hierarchy={<HierarchyPanel unityStatus={ai.unityMcpStatus} tree={sceneEditor.tree} loading={sceneEditor.loading}
          error={sceneEditor.error} stale={sceneEditor.stale} selectedId={sceneEditor.selectedId}
          onSelect={selectSceneObject} onConnect={() => { void ai.toggleUnityMcp(); }} actions={sceneEditor} />}
        profileLevel={profileStats.latest ? {
          level: profileStats.latest.level, xp: profileStats.latest.xp,
          levelXp: profileStats.latest.level_xp, levelNeed: profileStats.latest.level_need,
          xp_partial: profileStats.latest.xp_partial,
          lastAch: latestUnlocked(profileStats.latest.achievements)?.id,
        } : null}
        profileOpen={profileOpen && !ai.showSettings}
        onOpenProfile={openProfile}
        guideOpen={guide.guideOpen && !ai.showSettings && !profileOpen}
        onOpenGuide={() => guide.openGuide()}
      />

      <header className="topbar shell">
        <div className="tex tex-shell" aria-hidden="true" />
        <div className="topbar-left">
          {/* Unity connection: the app's signature control, its own bay at the far left.
              A component (not inline JSX) so every state is testable in the DOM. */}
          <UnityMcpToggle
            status={ai.unityMcpStatus}
            toggling={ai.unityMcpToggling}
            reason={ai.unityMcpReason}
            error={ai.unityMcpError}
            onToggle={ai.toggleUnityMcp}
            projectName={projectName}
          />
          {/* Not in the mockup: the sidebar toggle. The editor's controls that shared this spot (the
              terminal button, the open file's name / save / close) moved into the workspace: the
              drawer's own toggle, the Kod tab's file tab and crumb, the Önizleme close button. */}
          <div className="topbar-mid">
            <SidebarToggle
              open={isSidebarOpen}
              onToggle={() => setIsSidebarOpen(!isSidebarOpen)}
              awaiting={awaitingElsewhere(chat.convStatus, chat.activeConvId, chat.conversations)}
            />
          </div>
        </div>
        <div className="topbar-right">
          {/* C# analysis (OmniSharp) is starting */}
          {lspStatus?.state === 'starting' && (
            <span className="bar-chip">
              <span className="status status-running" aria-hidden="true" />
              <span className="bar-chip-label">{t('home.csharpAnalyzing')}</span>
            </span>
          )}
          <ModelSelector
            aiConfig={ai.aiConfig} setAiConfig={ai.setAiConfig} availableModels={ai.availableModels} providersWithKeys={ai.providersWithKeys}
            effectiveProvider={ai.effectiveProvider} displayModelName={ai.displayModelName} isModelDropdownOpen={ai.isModelDropdownOpen} setIsModelDropdownOpen={ai.setIsModelDropdownOpen}
            modelOrToggles={ai.modelOrToggles} setModelOrToggles={ai.setModelOrToggles} user={auth.user} fetchAvailableModels={ai.fetchAvailableModels} setShowSettings={ai.setShowSettings}
            API={API} axios={axios} showToast={showToast as any} conversationId={chat.activeConvId}
            usage={usage.data} openSettings={openSettings}
            thinkingLevel={thinkingLevel} effortLevels={effortCaps?.levels ?? null} onThinkingChange={chooseEffort}
          />
          <ModeChip value={chat.generationMode} onOpen={() => openSettings('onay')} />
          {/* The workspace toggle (mockup `[data-panel-toggle]`). */}
          <button
            type="button"
            className={`icon-btn${ws.open ? ' is-on' : ''}`}
            data-testid="right-panel-toggle"
            aria-pressed={ws.open}
            aria-label={ws.open ? t('home.panelHide') : t('home.panelShow')}
            title={ws.open ? t('home.panelHide') : t('home.panelShow')}
            onClick={() => setWsOpen(!ws.open)}
          >
            <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="4" width="14" height="12" rx="1.2" /><path d="M12.5 4v12" /></svg>
          </button>
        </div>
      </header>

      {/* ===== Chat stage (mockup `main.stage.paper`): the main column since the v4 swap. ===== */}
      <main
        className={`app-main stage paper${chat.loading ? ' is-working' : ''}`}
        aria-label={t('sidebar.chats')}
        data-empty={chatEmpty || undefined}
      >
        {/* #9 model light: a pool of the current model's colour at the top of the stage. */}
        <div className="lamp" aria-hidden="true" />

        {!chatEmpty && (
          <ThreadHeader
            conversation={activeConversation}
            projectName={projectName}
            awaiting={cardWaiting}
            actions={(
              <>
                <SideQuestionButton convId={chat.activeConvId} active={side.isOpen} onOpen={toggleSideChat} labelled />
                {!familyHasBranches && (
                  <BranchButton sourceId={chat.activeConvId} blocked={branchBlocked} onBranch={chat.branchConversation} labelled />
                )}
              </>
            )}
          />
        )}

        {/* Branch tabs keep their own (shell-toned) bar until they are ported. */}
        <div className="thread-tabs">
          <ChatTabs
            conversations={chat.conversations}
            activeConvId={chat.activeConvId}
            convStatus={chat.convStatus}
            branchBlocked={branchBlocked}
            onSelect={chat.selectConversation}
            onBranch={chat.branchConversation}
            onClose={chat.closeBranch}
            onRename={chat.renameConversation}
            onDelete={chat.deleteBranch}
          />
        </div>

        {/* Outside ChatPanel on purpose: these requests belong to no chat,
            so they stay here whichever chat is open. */}
        <McpUnknownTray
          gates={mcp.unknownGates}
          apiBase={API}
          sessionToken={auth.user?.sessionToken ?? ''}
          showToast={showToast as any}
          phonePaired={phonePaired}
        />

        {/* The scroll listener sits on `.thread`, the element that actually scrolls (measured
            before the swap: ChatPanel's own root never overflows, so a handler there would
            never fire). The empty new chat takes the thread's place. */}
        <div className="thread-wrap">
          {chatEmpty ? (
            <EmptyChat userName={me.name} projectName={projectName} onPick={pickQuest} approvalMode={chat.generationMode} />
          ) : (
            <div className="thread custom-scrollbar" onScroll={chatScroll.onScroll}>
              <ChatPanel
                messages={chat.messages} activeConvId={chat.activeConvId} conversations={chat.conversations} user={auth.user} loading={chat.loading} clearHistory={chat.clearHistory} lang={lang}
                thinkingLevel={thinkingLevel} workspacePath={fs.workspacePath} handleExportToUnity={fs.handleExportToUnity}
                pendingGenFiles={fs.pendingGenFiles} setPendingGenFiles={fs.setPendingGenFiles} pendingFix={chat.pendingFix} setPendingFix={chat.setPendingFix} openedFilePath={fs.openedFilePath}
                setCode={fs.setCode} setOriginalCode={fs.setOriginalCode} refreshFileTree={fs.refreshFileTree} analyzeProject={chat.analyzeProject} openFile={openInPanel} sendMessage={handleSendMessage}
                messagesEndRef={chatEndRef} ipc={ipc} showToast={showToast as any} diffFile={diffFile} setDiffFile={setDiffFile}
                pendingDelete={fs.pendingDelete} setPendingDelete={fs.setPendingDelete} pendingCommand={chat.pendingCommand} setPendingCommand={chat.setPendingCommand} onApproveCommand={chat.approveCommand} pendingQuestion={chat.pendingQuestion} setPendingQuestion={chat.setPendingQuestion} onAnswerQuestion={chat.answerQuestion} deleteFile={fs.deleteFile} setIsTerminalOpen={setIsTerminalOpen}
                activity={chat.activity}
                apiBase={API}
                mcpGate={mcp.activeGate} mcpWorkspaceMismatch={mcp.gateWorkspaceMismatch}
                mcpWorkspaceCheckPending={mcp.gateWorkspaceCheckPending}
                mcpOpenWorkspacePath={mcp.openWorkspacePath} onMcpResolved={mcp.resolveActiveGate}
                phonePaired={phonePaired}
              />
            </div>
          )}
          {hasUnreadBelow && !chatEmpty && (
            <button
              type="button"
              onClick={() => { chatScroll.scrollToBottom(); setHasUnreadBelow(false); }}
              className="new-below"
            >
              <ArrowDown size={13} aria-hidden="true" />
              {t('chat.newBelow')}
            </button>
          )}
          {/* Over the chat, outside ChatPanel: nothing it shows is part of
              the main chat until the user adds it to the message box. */}
          {side.isOpen && (
            <SideChatPanel
              messages={side.messages}
              loading={side.loading}
              onAsk={askSideQuestion}
              onStop={side.stop}
              onClose={side.close}
              onAddToMain={addSideAnswerToMain}
            />
          )}
        </div>

        <div className="composer-wrap" data-guide="composer">
          <SessionReportPanel
            open={reportsOpen}
            onClose={() => setReportsOpen(false)}
            API={API || ''}
            sessionToken={auth.user?.sessionToken ?? ''}
            convId={chat.activeConvId}
            onContextText={chat.applyContextReport}
          />
          <AnimatedChatInput
            value={chat.chatInput} setValue={chat.setChatInput} onSendMessage={handleSendMessage} isLoading={chat.loading}
            api={API}
            placeholder={t('chat.placeholder')}
            shortPlaceholder={t('chat.placeholderShort')}
            disabled={sohbetKilitli}
            disabledPlaceholder={t('gate.placeholder')}
            slashCommands={slashCommands}
            skills={skills}
            commandMeta={commandMeta}
            galleryProvider={slashProvider}
            chats={chat.conversations}
            currentChatId={chat.activeConvId}
            pickersRef={composerPickers}
            onStop={chat.stopMessage}
            queue={{
              items: chat.queue, paused: chat.queuePaused,
              onEdit: chat.editQueued, onDelete: chat.deleteQueued,
              onSendNow: (id) => { void chat.sendQueuedNow(id); }, onResume: () => { chat.resumeQueue(); },
            }}
            onFileDrop={(entry) => {
              const add = (prev: string) => prev + ` [File Attached: ${entry.path}]`;
              if (composerPickers.current) composerPickers.current.editDraft(add); else chat.setChatInput(add);
            }}
            onCommand={(cmd) => {
              if (cmd === '/compact') { chat.compactConversation(); return true; }
              // "/rehber telefon": the guide opens searching "telefon"; nothing is sent.
              const guideQuery = parseGuideCommand(cmd);
              if (guideQuery != null) { guide.openGuide(guideQuery); return true; }
              return false;
            }}
          />
          {/* The status strip under the box: thinking, memory, plan usage, the Add & chat menu. */}
          <ControlPanel
            thinkingLevel={thinkingLevel} setThinkingLevel={chooseEffort}
            isAnalyzingProject={chat.isAnalyzingProject} activeConvId={chat.activeConvId} analyzeProject={chat.analyzeProject}
            exportMemory={chat.exportMemory} importMemory={chat.importMemory} compactConversation={chat.compactConversation} isCompacting={chat.isCompacting} contextUsage={chat.contextUsage}
            reportsOpen={reportsOpen} onToggleReports={() => setReportsOpen(v => !v)}
            isClaudeSubscription={isClaudeSub} ultracode={ultracode} setUltracode={setUltracode} effortCaps={effortCaps}
            usage={usage.data} usageFamily={stripUsageFamily} modelId={ai.aiConfig.model_name}
            onAttachFile={() => composerPickers.current?.pickImage()} onAddVideo={() => composerPickers.current?.pickVideo()}
          />
        </div>
      </main>

      {/* ===== Workspace (mockup `aside.workspace`): the editor, the previews and the terminal,
          one panel for now. TODO(P3): the Sahne / Dosyalar / Kod / Onizleme tabs. ===== */}
      <div className="app-right">
        {/* Hidden, never unmounted: closing the panel must not kill the terminal session or
            drop the editor buffer (the old closable chat panel stayed mounted the same way). */}
        <Workspace
          editorOn={editorOn}
          open={ws.open}
          tab={ws.tab}
          onTab={setWsTab}
          width={ws.width}
          onWidth={ws.setWidth}
          onClose={() => setWsOpen(false)}
          panes={{
            sahne: editorOn ? <InspectorPane inspection={sceneEditor.inspection} loading={sceneEditor.inspectLoading}
              error={sceneEditor.inspectError} stale={sceneEditor.stale} /> : (
              <ScenePane
                change={pendingChange}
                changed={changedFiles.shown}
                changedTotal={changedFiles.total}
                seenHidden={changedFiles.seenHidden}
                onAck={onAckChanges}
                onShowAll={onShowAllChanges}
                isRepo={!!fs.gitStatus?.isRepo}
                workspacePath={fs.workspacePath}
                onShowChange={() => setWsTab('kod')}
                onOpen={openInPanel}
              />
            ),
            dosyalar: (
              <>
              {editorOn && <ChangedFiles
                change={pendingChange} changed={changedFiles.shown} changedTotal={changedFiles.total}
                seenHidden={changedFiles.seenHidden} onAck={onAckChanges} onShowAll={onShowAllChanges}
                isRepo={!!fs.gitStatus?.isRepo} workspacePath={fs.workspacePath}
                onShowChange={() => setWsTab('kod')} onOpen={openInPanel} />}
              <ProjectFiles
                {...fs}
                openFile={openInPanel}
                openPreview={previewInPanel}
                // The workspace Files tab is the only host for the right-click menu.
                showMenu
              />
              </>
            ),
            kod: (
              <KodPane
                workspacePath={fs.workspacePath}
                openedFilePath={fs.openedFilePath}
                isDirty={fs.isDirty}
                onSave={() => { void fs.saveFile(); }}
                // As the old top bar's X did: the buffer goes with the file.
                onCloseFile={fs.closeFile}
                diff={kodDiff}
                change={pendingChange}
                hint={<CsharpProjectHint inProject={csInProject} />}
                fileEditor={(
                  <EditorPanel
                    code={fs.code} setCode={fs.setCode} openedFilePath={fs.openedFilePath} isEditorFocused={isEditorFocused} setIsEditorFocused={setIsEditorFocused}
                    workspacePath={fs.workspacePath} problems={flattenedProblems} diffFile={null}
                    apiUrl={API} sessionToken={auth.user?.sessionToken} openFile={openInPanel}
                  />
                )}
                diffEditor={kodDiff && (
                  <EditorPanel
                    code={fs.code} setCode={fs.setCode} openedFilePath={fs.openedFilePath} isEditorFocused={isEditorFocused} setIsEditorFocused={setIsEditorFocused}
                    workspacePath={fs.workspacePath} problems={[]}
                    diffFile={{ name: kodDiff.name, code: kodDiff.modified, originalCode: kodDiff.original, suggestedPath: kodDiff.path ?? kodDiff.name }}
                  />
                )}
              />
            ),
            onizleme: (
              <PreviewPane
                file={fs.previewFile}
                kind={previewRoute == null ? null : previewRoute === 'image' || previewRoute === 'blocked-image' ? 'image' : 'model'}
                onClose={fs.closePreview}
                viewer={fs.previewFile && (previewRoute === 'image' || previewRoute === 'blocked-image'
                  ? <ImagePreviewPanel file={fs.previewFile} workspacePath={fs.workspacePath} />
                  : (
                    <ModelPreviewPanel
                      file={fs.previewFile}
                      workspacePath={fs.workspacePath}
                      overlay={(
                        <span className="pv-orbit-hint">
                          <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M3.5 10.5c0-2.4 2.9-4.3 6.5-4.3s6.5 1.9 6.5 4.3-2.9 4.3-6.5 4.3" /><path d="M8.6 12.9l1.6 1.9-1.9 1.6" /></svg>
                          {t('ws.orbitHint')}
                        </span>
                      )}
                    />
                  ))}
              />
            ),
          }}
          drawer={(
            <TerminalPanel
              id="main-terminal"
              isOpen={isTerminalOpen}
              onClose={() => setIsTerminalOpen(false)}
              onOpen={() => setIsTerminalOpen(true)}
              workspacePath={fs.workspacePath}
              problems={flattenedProblems}
              // The strip's "0 errors · 1 warning" only once C# diagnostics reported at least once.
              problemsKnown={Object.keys(projectProblems).length > 0}
              onProblemClick={handleProblemClick}
              apiUrl={API}
              sessionToken={auth.user?.sessionToken}
              unityConnected={ai.unityMcpStatus === 'connected'}
              tabRequest={drawerTabRequest}
              onTabChange={tab => { drawerTabRef.current = tab; }}
            />
          )}
        />
      </div>

      {/* The tour overlay (core tour or one guide topic) over the whole frame. */}
      {guide.tour && (
        <GuideTour
          tour={guide.tour} approvalMode={chat.generationMode}
          onNext={guide.next} onBack={guide.back} onSkip={guide.skip} onNameDraft={guide.setNameDraft}
        />
      )}

      {/* The achievement band / "Done" toast: the on-screen chat finished a turn. */}
      <AchievementToast event={achievementBand} title={achievementBand?.title}
        xp={achievementBand?.xp} achievement={achievementBand?.achievement} />

      {/* Bildirim kanalının çizen ucu. Bu satır olmadan `showToast` sessiz bir
          state güncellemesinden ibaret: mesaj üretiliyor, kimse görmüyor. */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
    </LangContext.Provider>
  );
}
