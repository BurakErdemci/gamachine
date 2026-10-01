import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';
import axios from 'axios';
import { motion } from 'framer-motion';
import {
  Terminal as TerminalIcon,
  Code2, Activity, X, PanelRightClose,
  Zap, Code, Layout, MessageSquare, ArrowDown
} from 'lucide-react';
import { LangContext, aktifDilAyarla, ceviriUygula, type Lang, type TValues } from '../lib/i18n';
import { sohbetKilitliMi } from '../lib/providerGate';
import { getUnsavedEditorContext } from '../lib/editor-context';
import { contentPane } from '../lib/contentPane';
import { routeForFile } from '../components/model-viewer/extensions';

import { Sidebar } from '../components/home/Sidebar';
import { EditorPanel, hostOpenTarget } from '../components/home/EditorPanel';
import { CsharpProjectHint } from '../components/home/CsharpProjectHint';
import { TerminalPanel } from '../components/home/TerminalPanel';
import { ChatPanel } from '../components/home/ChatPanel';
import { SettingsModal } from '../components/home/SettingsModal';
import { ExportModal } from '../components/home/ExportModal';
import { ModelSelector } from '../components/home/ModelSelector';
import { WorkspaceScreen } from '../components/home/WorkspaceScreen';
import { ControlPanel, ThinkingLevel, EffortCaps } from '../components/home/ControlPanel';
import { SessionReportPanel } from '../components/home/SessionReportPanel';
import { UnityMcpToggle } from '../components/home/UnityMcpToggle';
import { AnimatedChatInput } from '../components/ui/animated-ai-chat';
import { ToastContainer } from '../components/ui/Toast';

import { useAppInitialization } from '../hooks/home/useAppInitialization';
import { useAuth } from '../hooks/home/useAuth';
import { useFileSystem } from '../hooks/home/useFileSystem';
import { useChat } from '../hooks/home/useChat';
import { useAutoChatTitles } from '../hooks/home/useAutoChatTitles';
import { useDictationSettings } from '../hooks/home/useDictationSettings';
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
import { RemoteBadge, useRemoteStatus } from '../components/home/RemoteBadge';
import { ModeChip } from '../components/home/ModeChip';
import { modelFamily } from '../lib/modelFamily';
import { useRemoteEffort } from '../lib/remoteControl';
import { awaitingElsewhere, rootsOf } from '../lib/convFamily';

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
  .monaco-editor, .monaco-editor .margin, .monaco-editor-background { background-color: #0B0D12 !important; }
  .no-scrollbar::-webkit-scrollbar { display: none; }
`;

// Aktif modelin marka rengi (r,g,b) — imza "ambient ışık" bunu kullanır:
// copilot panelindeki üst süzülme + hero glow, hangi zekayla konuşulduğunu
// renkle hissettirir (Claude turuncu, Gemini mavi, Copilot menekşe...).
const BRAND_RGB: Record<string, string> = {
  claude: '251, 146, 60',
  openai: '52, 211, 153',
  gemini: '96, 165, 250',
  copilot: '196, 181, 253',
  cursor: '226, 232, 240',
  opencode: '94, 234, 212',
};
const getBrandRgb = (modelName?: string, provider?: string): string => {
  const m = (modelName || '').toLowerCase();
  if (m.startsWith('claude-')) return BRAND_RGB.claude;
  if (m.startsWith('gpt-')) return BRAND_RGB.openai;
  if (m.startsWith('gemini') || m.startsWith('agy-')) return BRAND_RGB.gemini;
  if (m.startsWith('copilot-')) return BRAND_RGB.copilot;
  if (m.startsWith('cursor-')) return BRAND_RGB.cursor;
  if (m.startsWith('opencode:')) return BRAND_RGB.opencode;
  return BRAND_RGB[(provider || '').toLowerCase()] || '96, 165, 250';
};

export default function Home() {
  // `toasts` ve `dismissToast` bilerek alınıyor: hook ikisini de döndürüyordu,
  // burada yalnız `showToast` alınıp diğerleri atılıyordu ve `ToastContainer`
  // hiçbir yerde mount edilmemişti. Sonuç: `showToast` bir state'e yazıyor,
  // çizen kimse yok — onay kapısı işinin bütün kullanıcı bildirimi görünmezdi
  // ve bunu 10/10 mutasyon bile göremedi (hepsi hook state'ine bakıyordu).
  const { API, backendReady, backendError, showToast, toasts, dismissToast } = useAppInitialization();
  const auth = useAuth(API, backendReady);
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
    if (ai.showSettings) ai.fetchAvailableModels();
  }, [ai.showSettings]);

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

  // --- Electron Menu IPC Listeners ---
  useEffect(() => {
    if (ipc) {
      const handleToggleTerminal = () => setIsTerminalOpen(prev => !prev);
      const handleOpenTerminal = () => setIsTerminalOpen(true);
      const handleClearTerminal = () => {
        showToast(t('home.terminalCleared'), "info");
      };

      const off1 = ipc.on('menu-toggle-terminal', handleToggleTerminal);
      const off2 = ipc.on('menu-open-terminal', handleOpenTerminal);
      const off3 = ipc.on('menu-clear-terminal', handleClearTerminal);

      return () => {
        if (typeof off1 === 'function') off1();
        if (typeof off2 === 'function') off2();
        if (typeof off3 === 'function') off3();
      };
    }
  }, [ipc, showToast]);

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
  const setLang = (l: Lang) => { setLangState(l); localStorage.setItem('app-lang', l); };
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
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isChatOpen, setIsChatOpen] = useState(true);
  const [reportsOpen, setReportsOpen] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<'chats' | 'files'>('chats');
  const [isEditorFocused, setIsEditorFocused] = useState(false);
  const [isTerminalOpen, setIsTerminalOpen] = useState(false);
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

  // Sohbet paneli KAPALIYKEN kart 0 piksel genişlikte çiziliyor: state'te var,
  // ekranda yok. Aynı bulgunun ikinci bacağı. Onay isteği kullanıcının panelde
  // olup olmamasına bağlı olamaz — köprü paneli bilmiyor ve karar verilmezse
  // istek reddediliyor. Paneli açmak geri alınabilir bir müdahale; kaçırılan
  // onay değil.
  //
  // Dört kapının DÖRDÜ de burada: kapı yalnız `mcp.activeGate` için açılıyordu,
  // oysa komut / soru / silme kartları da kapalı panelin içinde kalıyordu —
  // 30 Ağu 2026 denetiminin bulgusu, ve bu deponun ölçülmüş en sık arıza
  // biçimi (kapı yollardan yalnız birine konuyor).
  // The unknown-source tray sits at the top of the chat column, so it is
  // hidden by the same closed panel.
  const hasTrayRequest = mcp.unknownGates.length > 0;
  useEffect(() => {
    if (mcp.activeGate || hasTrayRequest || chat.pendingCommand || chat.pendingQuestion || fs.pendingDelete) {
      setIsChatOpen(true);
    }
  }, [mcp.activeGate, hasTrayRequest, chat.pendingCommand, chat.pendingQuestion, fs.pendingDelete]);

  // --- Desktop notifications (background chats) ---
  useChatNotifications({
    conversations: chat.conversations,
    activeConvId: chat.activeConvId,
    attention: chat.attention,
    trayGates: mcp.unknownGates,
    bridgeSynced: mcp.synced,
    screenCardOpen: !!fs.pendingDelete || !!fs.pendingGenFiles,
    onOpenConversation: (conv) => { chat.selectConversation(conv); setIsChatOpen(true); },
  });

  // --- Save Shortcut (Ctrl+S / Cmd+S) ---
  useEffect(() => {
    const handleKeyDown = async (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        await fs.saveFile();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [fs.saveFile, fs.workspacePath, auth.user]);

  // --- New chat shortcut (Ctrl+N / Cmd+N), the one the sidebar's "New chat" row names ---
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'n') {
        e.preventDefault();
        chat.createNewConversation();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [chat.createNewConversation]);

  const handleLogout = () => { fs.closeWorkspace(); };

  const handleProblemClick = async (problem: any) => {
    if (!problem.file || !fs.workspacePath) return;
    // `problem.file` is the BACKEND's spelling. Usually relative (the manager
    // sends `os.path.relpath` output) and then mode-independent, but when the
    // backend has no workspace root it reports the absolute container path,
    // which names nothing on this side of the mount. Same rule as a definition
    // result, in one shared place, so the two return legs cannot drift.
    const hedef = await hostOpenTarget(problem.file);
    if (hedef) fs.openFile(hedef);
  };

  const handleSendMessage = async (msg?: string, images?: string[], videos?: any[]) => {
    const draft = msg || chat.chatInput;
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
      ai.setShowSettings(true);
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
    chat.setChatInput(prev => (prev.trim() ? `${prev}\n\n${quote}` : quote));
    showToast(t('side.added'), 'success');
  };

  const langCtxValue = { lang, setLang, t };

  // İmza ambient ışık: aktif modelin marka rengi
  const brandRgb = getBrandRgb(ai.aiConfig?.model_name, ai.effectiveProvider);

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
          userName={auth.user?.name ?? ''} lastWorkspacePath={fs.lastWorkspacePath}
          onOpenWorkspaceDialog={fs.openFolder} onSelectLastWorkspace={() => fs.selectWorkspace(fs.lastWorkspacePath!)}
          onLogout={handleLogout}
        />
        {/* Workspace seçilmemişken de onay kartı gelebilir: unityMCP köprüsü
            ürünün penceresinden bağımsız çalışıyor (kullanıcı unityMCP'yi
            doğrudan başka bir istemciye bağlamış olabilir). Bu dal olmadan kart
            state'e giriyor ama ekrana HİÇ çıkmıyordu ve köprü 180 sn sonra
            reddediyordu — ürün "güvenli" görünüp kullanılamaz hale geliyordu
            (dış denetim: `approval-card-hidden-by-view-state`).
            Editör yok, o yüzden setDiffFile/onOpenFile/setCode verilmiyor. */}
        {(mcp.activeGate || mcp.unknownGates.length > 0) && (
          <div className="fixed inset-x-0 bottom-0 z-[250] max-h-[70vh] overflow-y-auto
                          border-t border-slate-700 bg-slate-900/95 py-3 backdrop-blur">
            <div className="mx-auto max-w-3xl">
              <McpUnknownTray
                gates={mcp.unknownGates}
                apiBase={API}
                sessionToken={auth.user?.sessionToken ?? ''}
                showToast={showToast as any}
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
              />
            </div>
          </div>
        )}
      </LangContext.Provider>
    );
  }

  const pane = contentPane(fs.previewFile, diffFile, fs.openedFilePath);
  // Branching copies the chat as it stands; mid-turn or with a card open there
  // is no settled "now" to copy (the backend answers 409 for the same case).
  const branchBlocked = chat.loading
    || (chat.activeConvId != null && chat.convStatus[chat.activeConvId] === 'awaiting')
    || !!chat.pendingCommand || !!chat.pendingQuestion || !!fs.pendingDelete || !!fs.pendingGenFiles;
  const familyHasBranches = hasBranches(chat.conversations, chat.activeConvId);
  // Which preview panel the open file belongs to; the two share one slot.
  const previewRoute = fs.previewFile ? routeForFile(fs.previewFile.path) : null;

  return (
    <LangContext.Provider value={langCtxValue}>
    {/* The v4 frame (shell.css `.app`): sidebar | main | right panel under one top bar.
        data-model drives the model colour (--model) the picker's dot reads. */}
    <div className="app" data-model={modelFamily(ai.aiConfig?.model_name, ai.effectiveProvider)}>
      <Head>
        <title>{`Gamachine | ${auth.user?.name || t('home.signIn')}`}</title>
        <style>{globalStyles}</style>
      </Head>

      <SettingsModal
        open={ai.showSettings} aiConfig={ai.aiConfig} availableModels={ai.availableModels} providersWithKeys={ai.providersWithKeys}
        onChange={ai.setAiConfig} onClose={() => ai.setShowSettings(false)} onSave={ai.saveAIConfig}
        onLogout={handleLogout} onDeleteKey={ai.deleteApiKey}
        unityMcpStatus={ai.unityMcpStatus} unityMcpToggling={ai.unityMcpToggling} onToggleUnityMcp={ai.toggleUnityMcp}
        lang={lang} onLangChange={setLang}
        approvalMode={chat.generationMode} onApprovalModeChange={(m) => chat.setGenerationMode(m, 'settings')}
        autoTitles={autoTitles.autoTitles} autoTitlesSaving={autoTitles.autoTitlesSaving}
        onToggleAutoTitles={autoTitles.toggleAutoTitles}
        dictationAutoLang={dictation.autoLanguageCpu} dictationAutoLangSaving={dictation.autoLanguageCpuSaving}
        onToggleDictationAutoLang={dictation.toggleAutoLanguageCpu}
        onRemoteStatus={remote.setStatus}
      />

      <ExportModal
        exportModal={fs.exportModal} exportFileName={fs.exportFileName} workspacePath={fs.workspacePath}
        onFileNameChange={fs.setExportFileName} onClose={() => fs.setExportModal(null)}
        onChangeExportDir={fs.changeExportDir} onExportSingleFile={fs.exportSingleFile} onExportMultipleFiles={fs.exportMultipleFiles}
      />

      <Sidebar
        isSidebarOpen={isSidebarOpen} sidebarTab={sidebarTab} setSidebarTab={setSidebarTab}
        conversations={chat.conversations} activeConvId={chat.activeConvId} convStatus={chat.convStatus} selectConversation={chat.selectConversation}
        createNewConversation={chat.createNewConversation} deleteConversation={chat.deleteConversation}
        editingId={chat.editingId} setEditingId={chat.setEditingId} tempTitle={chat.tempTitle} setTempTitle={chat.setTempTitle} saveRename={chat.saveRename}
        workspacePath={fs.workspacePath} closeWorkspace={fs.closeWorkspace} rootFolderPath={fs.rootFolderPath}
        openFolder={fs.openFolder} openFilePicker={fs.openFilePicker} treeCreating={fs.treeCreating}
        setTreeCreating={fs.setTreeCreating} treeCreateValue={fs.treeCreateValue} setTreeCreateValue={fs.setTreeCreateValue}
        submitTreeCreate={fs.submitTreeCreate} fileTree={fs.fileTree}
        openedFilePath={fs.openedFilePath} expandedDirs={fs.expandedDirs} dirContents={fs.dirContents}
        toggleDir={fs.toggleDir} openFile={fs.openFile} openPreview={fs.openPreview} treeDragSource={fs.treeDragSource}
        treeDragTarget={fs.treeDragTarget} renamingPath={fs.renamingPath} renameValue={fs.renameValue}
        setRenameValue={fs.setRenameValue} submitRename={fs.submitRename} setRenamingPath={fs.setRenamingPath}
        handleTreeDragStart={fs.handleTreeDragStart} handleTreeDragOver={fs.handleTreeDragOver}
        handleTreeDragLeave={fs.handleTreeDragLeave} handleTreeDrop={fs.handleTreeDrop}
        handleTreeContextMenu={fs.handleTreeContextMenu} startTreeCreate={fs.startTreeCreate}
        startRename={fs.startRename} handleTreeDelete={fs.handleTreeDelete}
        treeContextMenu={fs.treeContextMenu} setTreeContextMenu={fs.setTreeContextMenu}
        gitStatus={fs.gitStatus}
        user={auth.user} setShowSettings={ai.setShowSettings} handleLogout={handleLogout}
        unityStatus={ai.unityMcpStatus}
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
            projectName={fs.workspacePath?.split(/[\\/]/).filter(Boolean).pop() ?? null}
          />
          {/* Not in the mockup: the editor's own controls (sidebar, terminal, open file). They
              stay here until the workspace panel (P3) gives them their place. */}
          <div className="topbar-mid">
            <SidebarToggle
              open={isSidebarOpen}
              onToggle={() => setIsSidebarOpen(!isSidebarOpen)}
              awaiting={awaitingElsewhere(chat.convStatus, chat.activeConvId, chat.conversations)}
            />
            <button
              type="button"
              onClick={() => setIsTerminalOpen(!isTerminalOpen)}
              className="icon-btn"
              aria-pressed={isTerminalOpen}
              aria-label={t('home.terminalToggle')}
              title={t('home.terminalToggle')}
            >
              <TerminalIcon size={16} />
            </button>
            <div className="topbar-file" title={fs.previewFile?.path || fs.openedFilePath || undefined}>
              <Code2 size={14} className="shrink-0" />
              {/* Windows paths use a backslash: split on both separators; trim on narrow windows */}
              <span className="topbar-file-name">
                {fs.previewFile ? fs.previewFile.name : (fs.openedFilePath ? fs.openedFilePath.split(/[\\/]/).pop() : 'C# Editor')}
              </span>
              {/* No dirty dot in preview mode: the model is never edited here. */}
              {!fs.previewFile && fs.isDirty && <span className="topbar-dirty" aria-hidden="true" />}
              {fs.previewFile && (
                <button type="button" onClick={fs.closePreview} title={t('approval.close')} className="chat-act"><X size={12} /></button>
              )}
              {!fs.previewFile && fs.openedFilePath && (
                <>
                  <button type="button" onClick={async () => { await fs.saveFile(); }} disabled={!fs.isDirty} className="chat-act disabled:opacity-50">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline></svg>
                  </button>
                  <button type="button" onClick={() => { fs.setOpenedFilePath(null); fs.setCode(''); }} className="chat-act"><X size={12} /></button>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="topbar-right">
          <RemoteBadge status={remote.status} onClick={() => ai.setShowSettings(true)} />
          {/* C# analysis (OmniSharp) is starting */}
          {lspStatus?.state === 'starting' && (
            <span className="bar-chip">
              <span className="status status-running" aria-hidden="true" />
              {t('home.csharpAnalyzing')}
            </span>
          )}
          <ModelSelector
            aiConfig={ai.aiConfig} setAiConfig={ai.setAiConfig} availableModels={ai.availableModels} providersWithKeys={ai.providersWithKeys}
            effectiveProvider={ai.effectiveProvider} displayModelName={ai.displayModelName} isModelDropdownOpen={ai.isModelDropdownOpen} setIsModelDropdownOpen={ai.setIsModelDropdownOpen}
            modelOrToggles={ai.modelOrToggles} setModelOrToggles={ai.setModelOrToggles} user={auth.user} fetchAvailableModels={ai.fetchAvailableModels} setShowSettings={ai.setShowSettings}
            API={API} axios={axios} showToast={showToast as any} conversationId={chat.activeConvId}
          />
          <ModeChip value={chat.generationMode} onChange={chat.setGenerationMode} />
          {/* The right panel toggle (mockup: the workspace; here: the chat panel until P3). */}
          <button
            type="button"
            className="icon-btn"
            data-testid="right-panel-toggle"
            aria-pressed={isChatOpen}
            aria-label={isChatOpen ? t('home.panelHide') : t('home.panelShow')}
            title={isChatOpen ? t('home.panelHide') : t('home.panelShow')}
            onClick={() => setIsChatOpen(!isChatOpen)}
          >
            <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="4" width="14" height="12" rx="1.2" /><path d="M12.5 4v12" /></svg>
          </button>
        </div>
      </header>

      <div className="app-main">
        <div className="flex-1 overflow-hidden relative flex flex-col bg-[#0B0D12]">
          {pane === 'preview' && fs.previewFile ? (
            previewRoute === 'image' || previewRoute === 'blocked-image' ? (
              <ImagePreviewPanel file={fs.previewFile} workspacePath={fs.workspacePath} />
            ) : (
              <ModelPreviewPanel file={fs.previewFile} workspacePath={fs.workspacePath} />
            )
          ) : pane === 'editor' ? (
            <>
            <CsharpProjectHint inProject={diffFile ? null : csInProject} />
            <EditorPanel
              code={fs.code} setCode={fs.setCode} openedFilePath={fs.openedFilePath} isEditorFocused={isEditorFocused} setIsEditorFocused={setIsEditorFocused}
              workspacePath={fs.workspacePath} problems={flattenedProblems} diffFile={diffFile}
              apiUrl={API} sessionToken={auth.user?.sessionToken} openFile={fs.openFile}
            />
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-center p-8 relative overflow-hidden">
              {/* Marka renkli ambient zemin — hangi zekayla çalışıldığını hissettirir */}
              <div
                className="pointer-events-none absolute inset-0 transition-all duration-700"
                style={{ background: `radial-gradient(ellipse 55% 42% at 50% 36%, rgba(${brandRgb}, 0.06), transparent 70%)` }}
              />
              <div className="relative mb-8">
                <div
                  className="absolute inset-0 blur-[80px] rounded-full animate-pulse transition-colors duration-700"
                  style={{ backgroundColor: `rgba(${brandRgb}, 0.16)` }}
                />
                <Zap size={48} className="relative z-10 opacity-60 transition-colors duration-700" style={{ color: `rgb(${brandRgb})` }} />
              </div>
              <h2 className="text-2xl font-bold text-slate-100 mb-3 tracking-tight relative">GAMACHINE ENGINE</h2>
              <p className="text-slate-500 text-sm max-w-md leading-relaxed mb-8 relative">{t("home.editorHint")}</p>
              <div className="grid grid-cols-3 gap-3 max-w-lg w-full mb-10 relative">
                {[ {icon:<Activity size={14}/>, label: t('home.bugfix')}, {icon:<Code size={14}/>, label: t('home.codegen')}, {icon:<Layout size={14}/>, label: t('home.analyze')} ].map((item, i) => (
                  <div key={i} className="px-4 py-3 bg-white/[0.03] border border-white/[0.07] rounded-xl flex items-center justify-center gap-2 text-[11px] font-semibold text-slate-400 hover:bg-white/[0.06] hover:border-white/[0.12] hover:text-slate-200 transition-all cursor-default">
                    {item.icon} {item.label}
                  </div>
                ))}
              </div>
              {/* Son sohbetler — boş ekran gerçek bir karşılamaya dönüşsün */}
              {rootsOf(chat.conversations).length > 0 && (
                <div className="w-full max-w-lg relative">
                  <div className="text-[10px] uppercase tracking-widest text-slate-600 font-semibold mb-2 text-left">
                    {t('chat.recent')}
                  </div>
                  <div className="space-y-1.5">
                    {rootsOf(chat.conversations).slice(0, 3).map((conv) => (
                      <button
                        key={conv.id}
                        onClick={() => { chat.selectConversation(conv); setIsChatOpen(true); }}
                        className="w-full flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.05] hover:border-white/[0.1] text-left transition-colors group"
                      >
                        <MessageSquare size={13} className="text-slate-600 group-hover:text-slate-400 shrink-0 transition-colors" />
                        <span className="text-[12px] text-slate-400 group-hover:text-slate-200 truncate transition-colors">{conv.title}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
        <TerminalPanel
          id="main-terminal"
          isOpen={isTerminalOpen}
          onClose={() => setIsTerminalOpen(false)}
          workspacePath={fs.workspacePath}
          problems={flattenedProblems}
          onProblemClick={handleProblemClick}
          apiUrl={API}
          sessionToken={auth.user?.sessionToken}
          unityConnected={ai.unityMcpStatus === 'connected'}
        />
      </div>

      <div className="app-right">
      <motion.div animate={{ width: isChatOpen ? 450 : 0, opacity: isChatOpen ? 1 : 0 }} transition={{ duration: 0.2 }} className="bg-[#0B0D12] flex flex-col overflow-hidden shrink-0 border-l border-white/[0.06]">
        <div className="flex-1 relative flex flex-col min-h-0">
          {/* İmza: aktif modelin markası panelin tepesinden içeri süzülen ışık */}
          <div
            className="pointer-events-none absolute top-0 inset-x-0 h-36 transition-all duration-700"
            style={{ background: `linear-gradient(180deg, rgba(${brandRgb}, 0.05), transparent)` }}
          />
          <div className="h-12 border-b border-white/[0.06] flex items-center justify-between px-4 shrink-0 relative">
            <div className="flex items-center gap-2">
              <span
                className="w-1.5 h-1.5 rounded-full transition-colors duration-700"
                style={{ backgroundColor: `rgba(${brandRgb}, 0.9)` }}
              />
              <span className="text-[11px] font-bold text-slate-400 uppercase tracking-widest">Architect Copilot</span>
            </div>
            <div className="flex items-center gap-1">
              <SideQuestionButton convId={chat.activeConvId} active={side.isOpen} onOpen={toggleSideChat} />
              {!familyHasBranches && (
                <BranchButton sourceId={chat.activeConvId} blocked={branchBlocked} onBranch={chat.branchConversation} />
              )}
              <button onClick={() => setIsChatOpen(false)} className="p-1 hover:bg-white/[0.06] rounded transition-all text-slate-500 hover:text-slate-300"><PanelRightClose size={16} /></button>
            </div>
          </div>

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

          {/* Outside ChatPanel on purpose: these requests belong to no chat,
              so they stay here whichever chat is open. */}
          <McpUnknownTray
            gates={mcp.unknownGates}
            apiBase={API}
            sessionToken={auth.user?.sessionToken ?? ''}
            showToast={showToast as any}
          />

          {/* The scroll listener sits HERE, not on `ChatPanel`'s own root. Measured:
              ChatPanel's root carries `flex-1 overflow-y-auto`, but its parent is a
              block box, so `flex-1` does nothing, its height stays `auto` and it
              never overflows — the element that actually scrolls is this one, and a
              handler on the inner div would never fire. */}
          <div className="flex-1 relative flex flex-col min-h-0">
          <div className="flex-1 overflow-y-auto custom-scrollbar relative" onScroll={chatScroll.onScroll}>
            <ChatPanel
              messages={chat.messages} activeConvId={chat.activeConvId} conversations={chat.conversations} user={auth.user} loading={chat.loading} clearHistory={chat.clearHistory} lang={lang}
              thinkingLevel={thinkingLevel} workspacePath={fs.workspacePath} handleExportToUnity={fs.handleExportToUnity}
              pendingGenFiles={fs.pendingGenFiles} setPendingGenFiles={fs.setPendingGenFiles} pendingFix={chat.pendingFix} setPendingFix={chat.setPendingFix} openedFilePath={fs.openedFilePath}
              setCode={fs.setCode} refreshFileTree={fs.refreshFileTree} analyzeProject={chat.analyzeProject} openFile={fs.openFile} sendMessage={handleSendMessage}
              messagesEndRef={chatEndRef} ipc={ipc} showToast={showToast as any} diffFile={diffFile} setDiffFile={setDiffFile}
              pendingDelete={fs.pendingDelete} setPendingDelete={fs.setPendingDelete} pendingCommand={chat.pendingCommand} setPendingCommand={chat.setPendingCommand} onApproveCommand={chat.approveCommand} pendingQuestion={chat.pendingQuestion} setPendingQuestion={chat.setPendingQuestion} onAnswerQuestion={chat.answerQuestion} deleteFile={fs.deleteFile} setIsTerminalOpen={setIsTerminalOpen}
              activity={chat.activity}
              apiBase={API}
              mcpGate={mcp.activeGate} mcpWorkspaceMismatch={mcp.gateWorkspaceMismatch}
              mcpWorkspaceCheckPending={mcp.gateWorkspaceCheckPending}
              mcpOpenWorkspacePath={mcp.openWorkspacePath} onMcpResolved={mcp.resolveActiveGate}
            />
          </div>
            {hasUnreadBelow && (
              <button
                type="button"
                onClick={() => { chatScroll.scrollToBottom(); setHasUnreadBelow(false); }}
                className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-blue-600/90 hover:bg-blue-600 text-white text-[11.5px] font-medium shadow-lg shadow-black/40 border border-white/10 transition-colors"
              >
                <ArrowDown size={13} />
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

          <div className="p-4 border-t border-white/[0.06] bg-white/[0.015] relative">
            <SessionReportPanel
              open={reportsOpen}
              onClose={() => setReportsOpen(false)}
              API={API || ''}
              sessionToken={auth.user?.sessionToken ?? ''}
              convId={chat.activeConvId}
              onContextText={chat.applyContextReport}
            />
            <ControlPanel
              thinkingLevel={thinkingLevel} setThinkingLevel={chooseEffort} generationMode={chat.generationMode} setGenerationMode={chat.setGenerationMode}
              isAnalyzingProject={chat.isAnalyzingProject} activeConvId={chat.activeConvId} analyzeProject={chat.analyzeProject}
              exportMemory={chat.exportMemory} importMemory={chat.importMemory} compactConversation={chat.compactConversation} isCompacting={chat.isCompacting} contextUsage={chat.contextUsage}
              reportsOpen={reportsOpen} onToggleReports={() => setReportsOpen(v => !v)}
              isClaudeSubscription={isClaudeSub} ultracode={ultracode} setUltracode={setUltracode} effortCaps={effortCaps}
            />
            <div className="mt-3">
              <AnimatedChatInput
                value={chat.chatInput} setValue={chat.setChatInput} onSendMessage={handleSendMessage} isLoading={chat.loading}
                api={API}
                placeholder={t('chat.placeholder')}
                disabled={sohbetKilitli}
                disabledPlaceholder={t('gate.placeholder')}
                slashCommands={slashCommands}
                skills={skills}
                commandMeta={commandMeta}
                galleryProvider={slashProvider}
                chats={chat.conversations}
                currentChatId={chat.activeConvId}
                onStop={chat.stopMessage}
                queue={{
                  items: chat.queue, paused: chat.queuePaused,
                  onEdit: chat.editQueued, onDelete: chat.deleteQueued,
                  onSendNow: (id) => { void chat.sendQueuedNow(id); }, onResume: () => { chat.resumeQueue(); },
                }}
                onFileDrop={(entry) => chat.setChatInput(prev => prev + ` [File Attached: ${entry.path}]`)}
                onCommand={(cmd) => {
                  if (cmd === '/compact') { chat.compactConversation(); return true; }
                  return false;
                }}
              />
            </div>
          </div>
        </div>
      </motion.div>
      </div>

      {/* Bildirim kanalının çizen ucu. Bu satır olmadan `showToast` sessiz bir
          state güncellemesinden ibaret: mesaj üretiliyor, kimse görmüyor. */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
    </LangContext.Provider>
  );
}
