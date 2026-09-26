import { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import { flushSync } from 'react-dom';
import axios from 'axios';
import { Message, Conversation, UserData, AIConfig, GenerationMode, ChatActivity, ContextUsage } from '../../components/home/types';
import { PendingFile } from '../../components/home/FileCreationApproval';
import { confirmDialog } from '../../components/ui/ConfirmDialog';
import { deliveryFromFetch, gateFailure } from './gateResponse';
import { cevir } from '../../lib/i18n';
import { parseContextReport } from '../../lib/contextReport';
import { backendWorkspacePath } from '../../lib/backendWorkspacePath';
import { apiHataMesaji } from '../../lib/apiError';
import { familyOf, familyRootId, isBranchIn } from '../../lib/convFamily';

const ipc = typeof window !== 'undefined' ? (window as any).ipc : null;
const LEGACY_MODE_KEY = 'unityai-generation-mode';

type PendingCommand = { command: string; gateId: string; messageId: number; kind?: 'shell' | 'unity' };
type PendingQuestion = { questions: any[]; gateId: string; messageId: number };
type SetArg<T> = T | ((prev: T) => T);
const resolveArg = <T,>(arg: SetArg<T>, prev: T): T =>
  typeof arg === 'function' ? (arg as (p: T) => T)(prev) : arg;

export type ConvStatus = 'running' | 'awaiting' | 'unread';

/** How a chat's last turn ended on its own; a user Stop records nothing. */
export type TurnEnd = { seq: number; failed: boolean };

/**
 * What the desktop notifications watch per chat (`useChatNotifications`).
 * Every id is stable for the request it names, so a re-render or a poll that
 * reports the same state again produces no new id.
 */
export interface ChatAttention {
  /** Approval requests raised by the chat's own stream: gate ids and file-card markers. */
  approvals: string[];
  /** Unity bridge requests this chat owns, as reported by `/mcp-pending`. */
  bridgeGates: string[];
  /** Same predicate as the sidebar's "awaiting approval". */
  awaiting: boolean;
  turnEnd: TurnEnd | null;
}

/**
 * Everything one conversation owns while it runs. Parallel chats (Phase 3):
 * a turn keeps streaming after the user opens another chat, so every write a
 * turn makes goes to ITS conversation's entry. There used to be one shared
 * copy of all of this, and a background turn wrote into whatever was on
 * screen (measured: A's answer under B, Stop in B aborting A).
 */
interface ConvRuntime {
  messages: Message[];
  loading: boolean;
  // Canlı aktivite: Claude'un o an ne yaptığı (düşünüyor/araç/subagent) + token sayacı.
  // Backend status event'lerinden beslenir; done/error/stop'ta temizlenir.
  activity: ChatActivity | null;
  // `null` means "no reading available", NOT "the context is empty". A truthy
  // `{percent: 0, estimated: true}` placeholder used to sit here and stayed put
  // when the context request failed, so a request that only ever errored was
  // drawn as a confident near-empty gauge. The gauge renders the unavailable
  // state itself (ControlPanel: `usage.noData`).
  contextUsage: ContextUsage | null;
  // Paralel araç çağrılarında (ör. Bash + Write, ya da iki Write) birden fazla
  // onay/soru aynı anda gelebilir. Tek state'te tutarsak ikincisi birincisini EZER
  // ve ezilen gate 300sn bekleyip tıkanır ("düşünüyor"da kalır). Bu yüzden bekleyen
  // ek onay/soruları kuyruğa alıp tek tek gösteririz; biri çözülünce sıradaki açılır.
  pendingCommand: PendingCommand | null;
  commandQueue: PendingCommand[];
  pendingQuestion: PendingQuestion | null;
  questionQueue: PendingQuestion[];
  // File cards (generated files, delete) live in useFileSystem's single slot,
  // which only the chat on screen may fill. A background chat's card, and an
  // unanswered one taken back out of the slot when its chat is left, waits
  // here and is handed over when that chat is opened.
  parkedCards: Array<() => void>;
  // Unity MCP requests this chat owns that are still in `/mcp-pending`. The
  // cards themselves are rebuilt from the poll whenever the chat is on screen,
  // so only the ids are kept, for the sidebar's "awaiting approval". Not client
  // state for `hasClientState`: they are bound to no message, and the chat may
  // have been started by a renderer that has since reloaded.
  bridgeGates: string[];
  // Sequence number of the newest file card this chat's stream raised; 0 for
  // none. File cards have no gate id, and a card parked again when its chat
  // is left must not read as a new request.
  lastCard: number;
  turnEnd: TurnEnd | null;
  // A turn finished while another chat was on screen.
  unread: boolean;
  // That turn has not been re-read from the server yet (its ids are client
  // ids), so opening the chat shows the live copy instead of refetching.
  unsynced: boolean;
  // The list holds content the server copy lacks (an error bubble, a notice,
  // a slash card). Unlike `unsynced` this survives being opened and later
  // turns: only replacing the list from the server clears it.
  clientOnly: boolean;
}

const EMPTY_RUNTIME: ConvRuntime = {
  messages: [], loading: false, activity: null, contextUsage: null,
  pendingCommand: null, commandQueue: [], pendingQuestion: null, questionQueue: [],
  parkedCards: [], bridgeGates: [], lastCard: 0, turnEnd: null,
  unread: false, unsynced: false, clientOnly: false,
};

// Runtime key while no conversation is selected. Database ids start at 1.
const NO_CONV = 0;
const keyOf = (id: number | null | undefined) => id ?? NO_CONV;

const hasClientState = (r: ConvRuntime) =>
  r.loading || !!r.pendingCommand || !!r.pendingQuestion || r.parkedCards.length > 0
  || r.unsynced || r.clientOnly;

const isAwaiting = (r: ConvRuntime) =>
  !!r.pendingCommand || !!r.pendingQuestion || r.parkedCards.length > 0 || r.bridgeGates.length > 0;

type SlotSetter = (val: any) => void;

export const useChat = (
  API: string,
  user: UserData | null,
  aiConfig: AIConfig,
  workspacePath: string | null,
  showToast: (msg: string, type: any) => void,
  refreshFileTree: () => void,
  suggestFilePath: (name: string) => string
) => {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const conversationsRef = useRef<Conversation[]>([]);
  conversationsRef.current = conversations;
  const [activeConvId, setActiveConvIdState] = useState<number | null>(null);
  // Stream loops, the Stop button and the wake channel outlive the render that
  // created them; they read the chat on screen from here, never from a closure.
  const activeConvIdRef = useRef<number | null>(null);
  // Bumped by every selection, so an async step that started before one can
  // tell it no longer decides what is on screen.
  const selectionRef = useRef(0);

  // Writes land in the ref first, so consecutive stream events see each
  // other's result in event order (the old per-field updaters needed
  // workarounds for React running them later); the state is the render copy.
  const runtimesRef = useRef<Record<number, ConvRuntime>>({});
  const [runtimes, setRuntimes] = useState<Record<number, ConvRuntime>>({});
  const rt = useCallback((id: number) => runtimesRef.current[id] ?? EMPTY_RUNTIME, []);
  const patchConv = useCallback((id: number, fn: (r: ConvRuntime) => Partial<ConvRuntime>) => {
    const cur = runtimesRef.current[id] ?? EMPTY_RUNTIME;
    runtimesRef.current = { ...runtimesRef.current, [id]: { ...cur, ...fn(cur) } };
    setRuntimes(runtimesRef.current);
  }, []);
  const dropConv = useCallback((id: number) => {
    const { [id]: _gone, ...rest } = runtimesRef.current;
    runtimesRef.current = rest;
    setRuntimes(rest);
  }, []);

  // The useFileSystem setters each chat's file cards were handed to.
  const cardSlotsRef = useRef<Map<number, { gen?: SlotSetter; del?: SlotSetter }>>(new Map());

  // Takes a chat's unanswered file cards back out of the shared slots; with
  // `keep` they are parked for the chat's return, otherwise dropped. Left in
  // the slot, the next chat's card replaced them (generated files) or queued
  // unseen behind them (delete).
  const releaseCards = useCallback((convId: number, keep: boolean) => {
    const slots = cardSlotsRef.current.get(convId);
    if (!slots) return;
    // ChatPanel draws a card under the message whose id it carries, so that
    // message's chat owns it; bridge cards (MCP_MSG_ID) belong to no chat.
    const ids = new Set(rt(convId).messages.map(m => m.id));
    const mine = (c: any) => !!c && ids.has(c.messageId);
    let gen: any = null;
    let dels: any[] = [];
    // Only the slot knows whether the user already answered a card, so it is
    // read through updaters; flushSync runs them now instead of at the next
    // render, because what they find decides whether anything is parked.
    flushSync(() => {
      slots.gen?.((prev: any) => { gen = mine(prev) ? prev : null; return gen ? null : prev; });
      slots.del?.((list: any[]) => { dels = list.filter(mine); return list.filter(c => !mine(c)); });
    });
    if (!keep || (!gen && dels.length === 0)) return;
    patchConv(convId, r => ({ parkedCards: [...r.parkedCards, () => {
      if (gen) slots.gen?.(gen);
      dels.forEach(d => slots.del?.(d));
    }] }));
  }, [patchConv, rt]);

  const setActiveConvId = useCallback((id: number | null) => {
    const leaving = activeConvIdRef.current;
    selectionRef.current += 1;
    if (leaving && leaving !== id) releaseCards(leaving, true);
    activeConvIdRef.current = id;
    setActiveConvIdState(id);
  }, [releaseCards]);

  const screen = runtimes[keyOf(activeConvId)] ?? EMPTY_RUNTIME;
  const messages = screen.messages;
  const loading = screen.loading;
  const activity = screen.activity;
  const contextUsage = screen.contextUsage;

  const setMessages = useCallback((arg: SetArg<Message[]>) => {
    patchConv(keyOf(activeConvIdRef.current), r => ({ messages: resolveArg(arg, r.messages) }));
  }, [patchConv]);
  const setLoading = useCallback((arg: SetArg<boolean>) => {
    patchConv(keyOf(activeConvIdRef.current), r => ({ loading: resolveArg(arg, r.loading) }));
  }, [patchConv]);
  const setContextUsage = useCallback((arg: SetArg<ContextUsage | null>) => {
    patchConv(keyOf(activeConvIdRef.current), r => ({ contextUsage: resolveArg(arg, r.contextUsage) }));
  }, [patchConv]);
  const setPendingQuestion = useCallback((arg: SetArg<PendingQuestion | null>) => {
    patchConv(keyOf(activeConvIdRef.current), r => ({ pendingQuestion: resolveArg(arg, r.pendingQuestion) }));
  }, [patchConv]);

  // The exported command setter is the bridge path: `useMCPApproval` fills it
  // from `/mcp-pending`, and only with a request of the chat on screen (or one
  // from a backend that names no owner); it takes the card back out when that
  // chat is left. Cards from a chat's own SSE stream never go through here.
  const globalCommandRef = useRef<PendingCommand | null>(null);
  const [globalCommand, setGlobalCommand] = useState<PendingCommand | null>(null);
  const setPendingCommand = useCallback((arg: SetArg<PendingCommand | null>) => {
    globalCommandRef.current = resolveArg(arg, globalCommandRef.current);
    setGlobalCommand(globalCommandRef.current);
  }, []);
  const pendingCommand = globalCommand ?? screen.pendingCommand;

  // Called on every `/mcp-pending` poll. Only chats whose list changed are
  // written, so a steady poll does not re-render anything.
  const setBridgeGates = useCallback((gatesByConv: Record<number, string[]>) => {
    const same = (a: string[], b: string[]) => a.length === b.length && a.every((g, i) => g === b[i]);
    const ids = new Set([
      ...Object.keys(gatesByConv).map(Number),
      ...Object.entries(runtimesRef.current).filter(([, r]) => r.bridgeGates.length > 0).map(([k]) => Number(k)),
    ]);
    ids.forEach(id => {
      const next = gatesByConv[id] ?? [];
      if (!same(rt(id).bridgeGates, next)) patchConv(id, () => ({ bridgeGates: next }));
    });
  }, [patchConv, rt]);
  const pendingQuestion = screen.pendingQuestion;

  const [chatInput, setChatInput] = useState('');
  const [isCompacting, setIsCompacting] = useState(false);
  const [isAnalyzingProject, setIsAnalyzingProject] = useState(false);
  const [pendingFix, setPendingFix] = useState<{ data: any; messageId?: number; applied?: boolean } | null>(null);
  // The approval mode is global and lives in the backend (closed-loop.md §5):
  // external MCP clients carry no request, so a per-request field could never
  // make them auto. Until the backend answers, the UI shows step - the safe side
  // (a fresh install then reads auto from the backend; nothing is written here).
  const [generationMode, setGenerationModeState] = useState<GenerationMode>('step');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [tempTitle, setTempTitle] = useState('');

  useEffect(() => {
    if (!API || !user?.sessionToken) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await axios.get(`${API}/approval-mode`, {
          headers: { 'X-Session-Token': user.sessionToken },
        });
        let mode: GenerationMode = res.data?.mode === 'auto' ? 'auto' : 'step';
        let legacy: string | null = null;
        try { legacy = window.localStorage.getItem(LEGACY_MODE_KEY); } catch { /* storage blocked */ }
        // One-time migration: the backend has never stored a mode, so the
        // renderer's old localStorage choice becomes the global one. Without a
        // valid old value nothing is written, so the backend's fresh-install
        // default stays unsaved and a later explicit choice is the first write.
        if (!res.data?.stored && (legacy === 'auto' || legacy === 'step') && ipc?.invoke) {
          const out = await ipc.invoke('approval-mode-set', legacy, 'migrate');
          mode = out?.mode === 'auto' ? 'auto' : 'step';
          legacy = null;
        }
        if (res.data?.stored || legacy === null) {
          try { window.localStorage.removeItem(LEGACY_MODE_KEY); } catch { /* storage blocked */ }
        }
        if (!cancelled) setGenerationModeState(mode);
      } catch {
        // 401 while the token is still the initial 'local'; the effect reruns
        // when the real token arrives.
      }
    })();
    return () => { cancelled = true; };
  }, [API, user?.sessionToken]);

  // Writes go renderer -> Electron main -> backend: only main holds the UI
  // secret the backend demands, so model-run processes cannot flip the mode.
  const setGenerationMode = useCallback(async (mode: GenerationMode, source: 'chat' | 'settings' = 'chat') => {
    if (!ipc?.invoke) {
      showToast(cevir('mode.writeUnavailable'), 'error');
      return;
    }
    try {
      const out = await ipc.invoke('approval-mode-set', mode, source);
      if (out?.refused) {
        // The mode did not change; say why in the UI's language when the code is known.
        const refused = out.refused as { code?: string; message?: string; pids?: string };
        showToast(refused.code === 'agy_step_refused'
          ? cevir('mode.agyStepRefused', { pids: refused.pids || '?' })
          : cevir('mode.writeFailed', { hata: refused.message || String(refused.code) }), 'error');
        return;
      }
      const applied: GenerationMode = out?.mode === 'auto' ? 'auto' : 'step';
      setGenerationModeState(applied);
      if (applied === 'auto') {
        // The backend approved every open card on the switch; drop the in-chat
        // ones - in every chat, since the mode is global.
        setPendingCommand(null);
        for (const id of Object.keys(runtimesRef.current)) {
          patchConv(Number(id), () => ({ pendingCommand: null, commandQueue: [] }));
        }
      }
    } catch (e) {
      showToast(cevir('mode.writeFailed', { hata: e instanceof Error ? e.message : String(e) }), 'error');
    }
  }, [showToast, patchConv, setPendingCommand]);

  // One controller per conversation: Stop in one chat must abort that chat's
  // stream only.
  const controllersRef = useRef<Map<number, AbortController>>(new Map());
  // The turn currently owning each conversation (its assistant message id). A
  // stopped or superseded stream loop checks it and stops writing, so a late
  // chunk of an old turn cannot touch the next turn's cards or loading flag.
  const turnRef = useRef<Map<number, number>>(new Map());
  const eventSeqRef = useRef(0);
  // AUTO-WAKE: arguments needed to start a turn that this hook does NOT own
  // (language, generation mode, thinking level, and two card setters live in
  // the page component). Since a wake turn starts without user input, it
  // borrows them from the last real send; if there was no send yet, no wake
  // happens — making up a missing argument would mean starting a turn in a
  // mode the user never chose.
  const lastSendArgsRef = useRef<{
    lang: string; genMode: GenerationMode; thinkingLevel: any;
    setPendingGenFiles: (v: any) => void; setPendingDelete: (v: any) => void;
  } | null>(null);

  // Only the newest list request may write the list: an older answer was read
  // before a later hide, unhide or branch and would undo it. A local change
  // bumps this too, so reads already in flight cannot overwrite it.
  const listSeqRef = useRef(0);
  // Hide/unhide requests the server may not have stored yet; laid over any
  // list read while they are in flight. The token tells overlapping requests
  // for the same chat apart.
  const pendingHiddenRef = useRef(new Map<number, { hidden: boolean; token: number }>());

  const fetchConversations = useCallback(async (userId: number) => {
    if (!API) return;
    const seq = ++listSeqRef.current;
    try {
      const res = await axios.get(`${API}/conversations/${userId}`);
      if (seq !== listSeqRef.current) return;
      const pending = pendingHiddenRef.current;
      setConversations(!Array.isArray(res.data) || pending.size === 0 ? res.data
        : res.data.map((c: Conversation) => (pending.has(c.id) ? { ...c, hidden: pending.get(c.id)!.hidden } : c)));
    } catch (err) { console.error("Sohbet listesi hatası:", err); }
  }, [API]);

  // Göstergeyi backend'den TAZELE. Burada bir kopya formül vardı (chars/200k) ve
  // backend'deki asıl formülle sessizce ayrışabiliyordu — aynı kuralın iki
  // bağımsız metni. Artık tek kaynak `GET .../context-usage`.
  const refreshContextUsage = useCallback(async (convId: number) => {
    if (!API) return;
    try {
      const res = await axios.get(`${API}/conversations/${convId}/context-usage`);
      patchConv(convId, () => ({ contextUsage: res.data }));
    } catch (err) {
      // Keeping the previous reading would attribute a number to a request that
      // failed; falling back to zero would invent one. Only `null` says what
      // actually happened — we do not know.
      patchConv(convId, () => ({ contextUsage: null }));
      console.error('Bağlam göstergesi hatası:', err);
    }
  }, [API, patchConv]);

  // `/context` raporu geldiğinde göstergeyi TAHMİNDEN gerçek sayıya çevir.
  // Kaba tahmin (harf/200k) modele giden bağlamın en hacimli parçalarını
  // görmüyor; bu metin ise modelin kendi bildirdiği doluluk.
  const applyContextReport = useCallback((text: string) => {
    const r = parseContextReport(text);
    if (!r) return;
    setContextUsage(prev => ({
      ...prev,
      // `prev` is null until the first successful reading; the report itself
      // carries no message count, so the field needs a base that is a number.
      message_count: prev?.message_count ?? 0,
      percent: Math.round(r.pct),
      should_compact: r.pct >= 85,
      estimated: false,
      real: { used: r.used, total: r.total, model: r.model },
    }));
  }, [setContextUsage]);

  const fetchMessages = useCallback(async (convId: number) => {
    if (!API) return;
    try {
      const res = await axios.get(`${API}/conversations/${convId}/messages`);
      // A turn that started while this request was out owns the list now: the
      // server copy has neither its placeholder nor its streamed text yet.
      if (!rt(convId).loading) patchConv(convId, () => ({ messages: res.data, unsynced: false, clientOnly: false }));
      await refreshContextUsage(convId);
    } catch (err) { console.error("Mesaj hatası:", err); }
  }, [API, patchConv, refreshContextUsage, rt]);

  // Optimistic: the tab moves at once and moves back if the server refuses.
  // Either way the list is read again afterwards, so the server's state wins.
  const setBranchHidden = useCallback(async (convId: number, hidden: boolean) => {
    if (!API) return false;
    const mark = (h: boolean) =>
      setConversations(prev => prev.map(c => (c.id === convId ? { ...c, hidden: h } : c)));
    const pending = pendingHiddenRef.current;
    const token = ++listSeqRef.current;
    pending.set(convId, { hidden, token });
    mark(hidden);
    const settle = () => {
      const latest = pending.get(convId)?.token === token;
      if (latest) pending.delete(convId);
      return latest;
    };
    try {
      await axios.put(`${API}/conversations/${convId}/hidden`, { hidden });
      settle();
      return true;
    } catch (err) {
      // A later request for the same chat owns its state; do not roll it back.
      if (settle()) mark(!hidden);
      showToast(apiHataMesaji(err, cevir('branch.hideFailed')), 'error');
      return false;
    } finally {
      if (user) void fetchConversations(user.id);
    }
  }, [API, fetchConversations, showToast, user]);

  const selectConversation = useCallback(async (conv: Conversation) => {
    if (editingId) return;
    // Every way into a chat (closed-branches menu, notification click) lands
    // here. Not awaited: the tab does not depend on it, since the chat on
    // screen is always drawn as a tab (familyOf's activeId), so a failed
    // unhide cannot leave it tabless.
    const listed = conversationsRef.current.find(c => c.id === conv.id) ?? conv;
    if (isBranchIn(conversationsRef.current, listed) && listed.hidden) void setBranchHidden(conv.id, false);
    setActiveConvId(conv.id);
    // Switching never cancels anything. A chat whose client copy holds more
    // than the server's (a running turn, an open card bound to a client
    // message id, a background turn not yet synced) is shown as it is;
    // refetching would replace the live list and orphan its cards.
    const r = rt(conv.id);
    if (hasClientState(r)) {
      patchConv(conv.id, () => ({ unread: false, unsynced: false, parkedCards: [] }));
      r.parkedCards.forEach(handOver => handOver());
      return;
    }
    // Nothing has been measured for the new conversation yet — the previous
    // conversation's reading must not carry over, and a zero placeholder would
    // be a claim about a conversation we have not looked at.
    patchConv(conv.id, () => ({ contextUsage: null, unread: false }));
    await fetchMessages(conv.id);
  }, [editingId, fetchMessages, patchConv, rt, setActiveConvId, setBranchHidden]);

  const deleteConversation = useCallback(async (e: React.MouseEvent, convId: number) => {
    e.stopPropagation();
    if (!user) return;
    if (!(await confirmDialog(cevir('chat.deleteConfirm')))) return;
    try {
      const res = await axios.delete(`${API}/conversations/${convId}`);
      // Deleting a root takes its branches with it; an older backend does not
      // list them, and then only the chat itself is known to be gone.
      const listed = res?.data?.deleted_ids;
      const ids = Array.from(new Set([convId,
        ...(Array.isArray(listed) ? listed.map(Number).filter(Number.isSafeInteger) : [])]));
      for (const id of ids) {
        // Its cards leave the slot while its messages still say which they are.
        releaseCards(id, false);
        cardSlotsRef.current.delete(id);
        // Clearing the turn first makes a still-running loop stop writing, so
        // the deleted chat's entry is not recreated by a late chunk.
        turnRef.current.delete(id);
        controllersRef.current.get(id)?.abort();
        controllersRef.current.delete(id);
        dropConv(id);
      }
      if (activeConvIdRef.current != null && ids.includes(activeConvIdRef.current)) setActiveConvId(null);
      setConversations(prev => prev.filter(c => !ids.includes(c.id)));
      fetchConversations(user.id);
    } catch (err) { console.error("Sohbet silme hatası:", err); }
  }, [API, dropConv, fetchConversations, releaseCards, setActiveConvId, user]);

  const saveRename = useCallback(async (convId: number) => {
    if (!tempTitle.trim()) { setEditingId(null); return; }
    try {
      await axios.put(`${API}/conversations/${convId}`, { title: tempTitle });
      setEditingId(null);
      if (user) fetchConversations(user.id);
    } catch (err) { console.error("Yeniden adlandırma hatası:", err); }
  }, [API, fetchConversations, tempTitle, user]);

  const createNewConversation = useCallback(async (title?: string) => {
    const baslik = title ?? cevir('sidebar.newChat');
    if (!user || !API) return null;
    const selection = selectionRef.current;
    try {
      const res = await axios.post(`${API}/conversations`, { user_id: user.id, title: baslik });
      await fetchConversations(user.id);
      patchConv(res.data.id, () => ({ ...EMPTY_RUNTIME }));
      // A chat the user opened while this was in flight stays on screen; the
      // new chat still exists and is returned to the caller (a send from the
      // empty screen keeps it as its target).
      if (selectionRef.current === selection || activeConvIdRef.current === null) {
        setActiveConvId(res.data.id);
      }
      return res.data.id;
    } catch (err) { console.error("Yeni sohbet hatası:", err); return null; }
  }, [API, fetchConversations, patchConv, setActiveConvId, user]);

  // "Branch from now": the server copies the source chat's text history into a
  // new chat under the same root. A chat mid-turn or waiting on a card is
  // refused here as the server would (409), since the copy would cut the turn.
  const branchConversation = useCallback(async (sourceId: number) => {
    if (!user || !API) return null;
    const src = rt(sourceId);
    if (src.loading || isAwaiting(src)) {
      showToast(cevir('branch.busy'), 'error');
      return null;
    }
    const selection = selectionRef.current;
    try {
      const res = await axios.post(`${API}/conversations/${sourceId}/branch`);
      const created = res.data as Conversation;
      setConversations(prev => (prev.some(c => c.id === created.id) ? prev : [...prev, created]));
      // Started now so it supersedes list reads made before the server had it.
      void fetchConversations(user.id);
      // A chat the user opened meanwhile stays on screen; the tab still appears.
      if (selectionRef.current === selection) await selectConversation(created);
      return created.id;
    } catch (err: any) {
      const fallback = err?.response?.status === 409 ? 'branch.busy' : 'branch.failed';
      showToast(apiHataMesaji(err, cevir(fallback)), 'error');
      return null;
    }
  }, [API, fetchConversations, rt, selectConversation, showToast, user]);

  // Closing a tab hides the branch; nothing is deleted and a running turn
  // keeps running. Closing the tab on screen moves to its left neighbour.
  const closeBranch = useCallback(async (convId: number) => {
    const list = conversationsRef.current;
    const conv = list.find(c => c.id === convId);
    if (!conv || !isBranchIn(list, conv)) return false;
    if (activeConvIdRef.current === convId) {
      const fam = familyOf(list, familyRootId(list, convId), convId);
      const tabs = [fam.root, ...fam.visible].filter((c): c is Conversation => !!c);
      const next = tabs[tabs.findIndex(c => c.id === convId) - 1];
      if (next) void selectConversation(next);
    }
    return setBranchHidden(convId, true);
  }, [selectConversation, setBranchHidden]);

  // A turn that finished off screen is re-read from the server once, so its
  // ids and persisted content line up. Only called for a clean finish with
  // nothing client-only on it: the server list carries no notices, error
  // bubbles or slash cards, and re-iding the list would orphan an open card.
  const syncFinished = useCallback(async (convId: number) => {
    const notSyncable = (r: ConvRuntime) =>
      !r.unsynced || r.clientOnly || r.loading || !!r.pendingCommand || !!r.pendingQuestion
      || r.parkedCards.length > 0;
    if (!API || notSyncable(rt(convId))) return;
    try {
      const res = await axios.get(`${API}/conversations/${convId}/messages`);
      // Opened in the meantime (the user is reading the live copy) or a new
      // turn started: the server copy would replace what is being looked at.
      if (notSyncable(rt(convId))) return;
      patchConv(convId, () => ({ messages: res.data, unsynced: false }));
      await refreshContextUsage(convId);
    } catch {
      // The live copy stays; opening the chat shows it.
    }
  }, [API, patchConv, refreshContextUsage, rt]);

  const sendMessage = useCallback(async (
    messageContent: string, 
    code: string, 
    lang: string, 
    genMode: GenerationMode, 
    thinkingLevel: 'auto' | 'off' | 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max',
    setPendingGenFiles: (val: any) => void,
    setPendingDelete: (val: any) => void,
    images?: string[],
    ultracode: boolean = false,    // Claude-only; mesaja keyword enjekte edilir
    videos?: any[],                // [{kind:'path',path} | {kind:'url',url}] → converted to frames by the backend
    // 'wake' = a turn the client starts BY ITSELF once a background job finishes.
    // The backend stores this with the `system` role and runs the consecutive-wake counter.
    origin: 'user' | 'wake' = 'user',
    // The wake channel names its own conversation; a user send goes to the chat on screen.
    targetOverride?: number
  ) => {
    if (!user || !API) return;
    const requested = targetOverride ?? activeConvIdRef.current;
    // Only THIS chat's running turn blocks a send; other chats run independently.
    if (rt(keyOf(requested)).loading) return;
    patchConv(keyOf(requested), () => ({ loading: true }));
    if (origin === 'user') {
      lastSendArgsRef.current = { lang, genMode, thinkingLevel, setPendingGenFiles, setPendingDelete };
    }

    let created: number | null = null;
    if (!requested) {
      // The no-conversation slot held `loading` only to refuse a double send
      // while the conversation is being created.
      created = await createNewConversation();
      patchConv(NO_CONV, () => ({ loading: false }));
      if (!created) return;
      patchConv(created, () => ({ loading: true }));
    }
    const targetConvId: number = requested || created!;
    // Yeni tur: bu sohbetin önceki turundan kalmış bekleyen onay/soru ve
    // kuyruklarını temizle - yalnız bu sohbetin; başka sohbetin kartı onun.
    patchConv(targetConvId, () => ({
      pendingCommand: null, commandQueue: [], pendingQuestion: null, questionQueue: [],
      unread: false, unsynced: false,
    }));
    const updateMessages = (fn: (prev: Message[]) => Message[]) =>
      patchConv(targetConvId, r => ({ messages: fn(r.messages) }));
    const onScreen = () => activeConvIdRef.current === targetConvId;
    // File cards go straight to useFileSystem's slot only while this chat is
    // on screen; otherwise they wait in this chat's entry (see `parkedCards`).
    const fileCard = (kind: 'gen' | 'del', card: { messageId: number; [field: string]: unknown }) => {
      patchConv(targetConvId, () => ({ lastCard: ++eventSeqRef.current }));
      const set: SlotSetter = kind === 'gen' ? setPendingGenFiles : setPendingDelete;
      const handOver = () => {
        cardSlotsRef.current.set(targetConvId, { ...cardSlotsRef.current.get(targetConvId), [kind]: set });
        set(card);
      };
      if (onScreen()) handOver();
      else patchConv(targetConvId, r => ({ parkedCards: [...r.parkedCards, handOver] }));
    };
    // Anything the server copy will not carry (notice, error text, slash card)
    // makes an off-screen refresh lossy; then the live copy is kept instead.
    let lossy = false;
    let finishedCleanly = false;
    let errored = false;

    const userMsg: Message = { 
      id: Date.now(), 
      role: origin === 'wake' ? 'system' : 'user', 
      content: messageContent, 
      smells: [], 
      timestamp: new Date().toISOString(),
      images: images 
    };
    updateMessages(prev => [...prev, userMsg]);
    setChatInput('');

    // Özel kart render edilen slash komutları → asistan mesajını etiketle.
    //   /usage   → Claude (Claude Code) + Codex (app-server rateLimits kartı)
    //   /context → yalnızca Claude (Codex/agy'de yok)
    // NOT: /cost bu Claude Code sürümünde YOK (abonelikte session cost /usage'a dahil).
    const _trimmed = messageContent.trim().toLowerCase();
    const _m = (aiConfig?.model_name || '').toLowerCase();
    const _isSub = aiConfig?.provider_type === 'subscription';
    const _isCodex = _isSub && _m.startsWith('gpt-');
    const _isClaude = _isSub && !_m.startsWith('gpt-') && !(_m.startsWith('gemini') || _m.startsWith('agy-'));
    let slashCard: string | undefined;
    if (_trimmed === '/usage' && (_isClaude || _isCodex)) slashCard = 'usage';
    else if (_trimmed === '/context' && _isClaude) slashCard = 'context';

    const aiMsgId = Date.now() + 1;
    let currentAiMsg: Message = { id: aiMsgId, role: 'assistant', content: '', smells: [], timestamp: new Date().toISOString(), thinking: null, tool_calls: [], slashCommand: slashCard };
    updateMessages(prev => [...prev, currentAiMsg]);
    if (slashCard) lossy = true;
    turnRef.current.set(targetConvId, aiMsgId);
    const ownsTurn = () => turnRef.current.get(targetConvId) === aiMsgId;
    const controller = new AbortController();
    controllersRef.current.set(targetConvId, controller);

    // D4-02 (audit, high): a `{kind:'path', path}` video entry carries a HOST
    // path from the folder picker (`open-video-dialog`). In Docker mode the
    // backend only sees the one bind-mounted tree, so it must be translated
    // exactly like every other filesystem path BEFORE it leaves this process
    // — sending the untranslated host spelling is the defect itself, since
    // `video_extract.py` runs `os.path.isfile()` on it INSIDE the container.
    //
    // A `null` translation means the file is outside the mount and there is
    // no container name for it at all (Docker mounts exactly one tree). That
    // is refused here rather than sent — uploading the file's bytes as a
    // fallback transport is deliberately out of scope. `{kind:'url', ...}`
    // entries are not filesystem paths and pass through untouched. Outside
    // Docker mode `backendWorkspacePath` is an identity function, so this
    // loop is a no-op for the ordinary (non-Docker) user.
    let videosToSend = videos;
    if (videos && videos.length > 0) {
      const resolved: any[] = [];
      for (const v of videos) {
        if (v && v.kind === 'path') {
          const mapped = await backendWorkspacePath(v.path);
          if (mapped === null) {
            showToast(cevir('chat.videoOutsideDockerMount', { ad: v.name || v.path }), 'warning');
            continue;
          }
          resolved.push({ ...v, path: mapped });
        } else {
          resolved.push(v);
        }
      }
      videosToSend = resolved;
    }

    try {
      const response = await fetch(`${API}/chat-stream`, {
        method: 'POST',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json', 'X-Session-Token': user.sessionToken },
        body: JSON.stringify({
          conversation_id: targetConvId, message: messageContent, language: lang, user_id: user.id,
          editor_code: code || '',
          // thinking_level: geriye-uyum alanı (use_thinking türetimi için).
          // off dışındaki her şey (auto dahil) → 'medium' nötr değeri; gerçek
          // seviye effort_level'da gider, backend kayıtçısı (effort_caps) eşler.
          thinking_level: (thinkingLevel === 'off' ? 'off'
            : ['low', 'medium', 'high'].includes(thinkingLevel) ? thinkingLevel : 'medium'),
          generation_mode: genMode, generation_confirmed: false,
          images: images,
          videos: videosToSend,
          // effort_level: birleşik seçicinin TAM değeri (auto/off/minimal/low..max).
          // Backend her provider dalında effort_caps kayıtçısıyla gerçek parametreye çevirir.
          effort_level: thinkingLevel,
          ultracode: !!ultracode,
          origin,
        }),
      });

      const reader = response.body?.getReader();
      const decoder = new TextDecoder('utf-8');
      if (reader) {
        let buffer = '';
        while (ownsTurn()) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            // Stopped, superseded by a new turn, or the chat was deleted.
            if (!ownsTurn()) break;
            if (line.startsWith('data: ')) {
              const data = JSON.parse(line.slice(6));
              // Events carry no conversation id today. If they ever do, one
              // that names another chat is not this stream's to apply.
              if (data.conversation_id != null && Number(data.conversation_id) !== targetConvId) continue;
              if (data.type === 'done' || data.type === 'response') finishedCleanly = true;
              if (data.type === 'error') errored = true;
              updateMessages(prev => prev.map(msg => {
                if (msg.id === aiMsgId) {
                  const updated = { ...msg };
                  if (data.type === 'thinking') updated.thinking = (updated.thinking || '') + (data.text || '');
                  else if (data.type === 'text') updated.content += data.content;
                  else if (data.type === 'response') updated.content = data.content || updated.content;
                  else if (data.type === 'error' && data.message) {
                    // Hata artık chat'te GÖRÜNÜR (eskiden sessizce yutuluyordu → "boş baloncuk")
                    //
                    // Kodu TANIYORSAK kendi dilimizde yazıyoruz, tanımıyorsak
                    // backend'in metnine düşüyoruz. Backend mesajları sabit
                    // Türkçe, yani İngilizce arayüzde Türkçe cümle çıkıyordu;
                    // ama koda göre dallanıp bilinmeyeni YUTMAK da olmaz —
                    // `warning` sözleşmesinin uyardığı tuzak tam olarak o.
                    // Düşüş kuralı ikisini birden kapatıyor.
                    const kodlu: Record<string, string> = {
                      provider_quota: 'error.providerQuota',
                      provider_unavailable: 'error.providerUnavailable',
                      provider_unreachable: 'error.providerUnreachable',
                      model_no_tools: 'error.modelNoTools',
                      agy_closed_child_alive: 'error.agyClosedChildAlive',
                    };
                    const anahtar = typeof data.code === 'string' ? kodlu[data.code] : undefined;
                    const metin = anahtar
                      ? cevir(anahtar as any, { model: data.model || '', pids: data.pids || '?' })
                      : String(data.message);
                    updated.content += (updated.content ? '\n\n' : '') + `❌ ${metin}`;
                    lossy = true;
                  }
                  // Side-pipeline failure (video download/extract today). The run
                  // is NOT killed — the stream keeps going — but the user has to
                  // learn that a piece of their input never made it in.
                  //
                  // Deliberately NOT switched on `data.code`: the contract says a
                  // machine-readable code plus a ready-to-show `message`, and a
                  // frontend `switch` over today's three codes would swallow
                  // tomorrow's fourth. The code only rides along in the detail.
                  else if (data.type === 'warning' && data.message) {
                    const detail = [data.code ? `code=${data.code}` : null, data.detail || null]
                      .filter(Boolean).join(' · ');
                    updated.notices = [...(updated.notices || []), {
                      kind: 'warning',
                      title: cevir('notice.warningTitle'),
                      message: String(data.message),
                      detail: detail || undefined,
                    }];
                    lossy = true;
                  }
                  // A run that hit the iteration cap looked EXACTLY like a run that
                  // finished: `done` only cleared the activity line. The user was
                  // left with a half-done task and no reason to ask for more.
                  //
                  // Missing `stop_reason` means an older backend → assume
                  // `complete`, except that the pre-contract `max_reached` flag
                  // still carries the same fact and is honoured.
                  else if (data.type === 'done') {
                    const reason = typeof data.stop_reason === 'string'
                      ? data.stop_reason
                      : (data.max_reached ? 'max_iterations' : 'complete');
                    if (reason !== 'complete') {
                      const detail = [`stop_reason=${reason}`,
                        typeof data.iterations === 'number' ? `iterations=${data.iterations}` : null]
                        .filter(Boolean).join(' · ');
                      // Tekrarlayan aracın adı varsa ONU söyle. "İlerleme
                      // kaydedemedi" bir teşhis değil; hangi çağrının kısır
                      // döndüğü kullanıcının üzerine hareket edebileceği tek
                      // bilgi. Ad gelmiyorsa (eski backend) genel metne düşülür.
                      const message = reason === 'wake_chain_exhausted'
                        // Wake-chain safety valve. A separate text is required:
                        // `stoppedOther` says "the run stopped midway", but here
                        // the run NEVER started, and the reason is a limit, not a fault.
                        ? cevir('notice.wakeChainExhausted')
                        : reason === 'max_iterations'
                        ? cevir('notice.maxIterations')
                        : reason === 'no_progress'
                          ? (typeof data.repeated_tool === 'string' && data.repeated_tool
                            ? cevir('notice.noProgressTool', { arac: data.repeated_tool })
                            : cevir('notice.noProgress'))
                          // An unrecognised reason still gets a notice: "we do not
                          // know why, but it did not finish" beats silence.
                          : cevir('notice.stoppedOther');
                      updated.notices = [...(updated.notices || []), {
                        kind: 'stopped',
                        title: cevir('notice.stoppedTitle'),
                        message,
                        detail,
                      }];
                      lossy = true;
                    }
                  }
                  else if (data.type === 'turn_usage') {
                    updated.usage = { output_tokens: data.output_tokens, duration_ms: data.duration_ms };
                  }
                  else if (data.type === 'tool_call') {
                    const args = typeof data.arguments === 'string' ? JSON.parse(data.arguments) : data.arguments;
                    (updated.tool_calls ||= []).push({ tool: data.tool, args: args, summary: data.summary || undefined, id: data.tool_id || undefined });
                  }
                  else if (data.type === 'tool_result') {
                    updated.tool_calls ||= [];
                    // 1) tool_id ile birebir eşle (en güvenilir); 2) sondan geriye ilk
                    // sonuçsuz aynı-isimli chip; 3) hiçbiri yoksa ayrı chip (örn. arka plan görevi).
                    let tc = data.tool_id ? updated.tool_calls.find(t => t.id === data.tool_id) : undefined;
                    if (!tc) {
                      for (let i = updated.tool_calls.length - 1; i >= 0; i--) {
                        const c = updated.tool_calls[i];
                        if (c.tool === data.tool && c.success === undefined) { tc = c; break; }
                      }
                    }
                    if (tc) {
                      tc.summary = data.summary ?? tc.summary;
                      tc.success = data.success;
                      if (data.output) tc.output = data.output;
                    } else {
                      updated.tool_calls.push({ tool: data.tool, summary: data.summary, success: data.success, output: data.output || undefined });
                    }
                  }
                  currentAiMsg = updated;
                  return updated;
                }
                return msg;
              }));
              // Canlı aktivite göstergesi: status event'leri + türev sinyaller.
              const setActivity = (fn: (prev: ChatActivity | null) => ChatActivity | null) =>
                patchConv(targetConvId, r => ({ activity: fn(r.activity) }));
              if (data.type === 'status') {
                setActivity(prev => ({
                  detail: data.detail || prev?.detail || cevir('activity.working'),
                  tokens: (typeof data.tokens === 'number' && data.tokens > 0) ? data.tokens : prev?.tokens,
                }));
              } else if (data.type === 'thinking') {
                setActivity(prev => ({ detail: cevir('activity.thinking'), tokens: prev?.tokens }));
              } else if (data.type === 'text') {
                setActivity(prev => ({ detail: cevir('activity.writing'), tokens: prev?.tokens }));
              } else if (data.type === 'tool_call' && data.tool !== 'TodoWrite') {
                const s = data.summary ? ` — ${String(data.summary).slice(0, 60)}` : '';
                setActivity(prev => ({ detail: `🔧 ${data.tool}${s}`, tokens: prev?.tokens }));
              } else if (data.type === 'done' || data.type === 'error' || data.type === 'response') {
                // A terminal event ends the turn, and with it every gate the
                // turn was holding. Only the activity line used to be cleared,
                // so a question whose gate had expired stayed on screen: the
                // user could still answer a decision that no longer belonged to
                // anything, and `answerQuestion` posted into a dead gate. The
                // QUEUE goes too — a queued card is just one that has not been
                // shown yet, and it belongs to the same finished turn.
                //
                // The per-chat entry is written synchronously in event order, so
                // a question queued earlier in the same chunk is already there to
                // clear (the old React-updater version needed care here).
                patchConv(targetConvId, () => ({ activity: null, pendingQuestion: null, questionQueue: [] }));
              }
              if (data.type === 'context_usage') patchConv(targetConvId, () => ({ contextUsage: {
                percent: data.percent,
                should_compact: data.should_compact,
                message_count: data.message_count,
                estimated: data.estimated !== false,
                last_turn: data.last_turn,
              } }));
              if (data.type === 'command_approval_needed') {
                const item = { command: data.command, gateId: data.gate_id, messageId: aiMsgId };
                // Zaten gösterilen bir onay varsa sıraya al (paralel araçlarda ezilmesin)
                patchConv(targetConvId, r => r.pendingCommand
                  ? { commandQueue: [...r.commandQueue, item] }
                  : { pendingCommand: item });
              }
              if (data.type === 'question_needed') {
                const item = { questions: data.questions || [], gateId: data.gate_id, messageId: aiMsgId };
                patchConv(targetConvId, r => r.pendingQuestion
                  ? { questionQueue: [...r.questionQueue, item] }
                  : { pendingQuestion: item });
              }
              if (data.type === 'pending_delete' && data.path) {
                fileCard('del', { path: data.path, messageId: aiMsgId });
              }
              if (data.type === 'refresh_file_tree') refreshFileTree();
              if (data.type === 'done') refreshFileTree();
              // Subscription (claude/codex/agy) provider'larda dosya yazımı MCP/CLI
              // gate'iyle yapılır (useMCPApproval). Bu provider'lar açıklama metninde
              // kod bloğunu da yazdığı için parseGeneratedFiles burada FAZLADAN diff
              // kartı üretir (agy kodu iki kez yazınca iki kart). O yüzden sadece
              // tool kullanmayan provider'larda (Gemini/OpenAI/Ollama API) metni ayrıştır.
              if ((data.type === 'done' || data.type === 'response') && aiConfig.provider_type !== 'subscription') {
                const { parseGeneratedFiles } = await import('../../components/home/export-utils');
                // currentAiMsg o anki en güncel mesaj içeriğini tutmalı
                const parsed = parseGeneratedFiles(currentAiMsg.content);
                if (workspacePath && parsed.length > 0) {
                  const withPaths: PendingFile[] = [];
                  for (const f of parsed) {
                    const suggestedPath = suggestFilePath(f.name);
                    const res = await ipc?.invoke('read-file', suggestedPath, workspacePath);
                    withPaths.push({ 
                      name: f.name, 
                      code: f.code, 
                      suggestedPath, 
                      originalCode: (res && res.content) ? res.content : "" 
                    });
                  }
                  // Message ID'yi state'ten doğrula veya doğrudan kullan
                  fileCard('gen', { files: withPaths, messageId: aiMsgId });
                }
              }
            }
          }
        }
      }
      fetchConversations(user.id);
    } catch (err: any) {
      // After `done`/`response` the answer is complete and the server has it;
      // a later failure is not the user's to see, and must not block the refetch.
      if (err?.name !== 'AbortError' && ownsTurn() && !finishedCleanly) {
        lossy = true;
        updateMessages(prev => [...prev, { id: Date.now() + 2, role: 'assistant', content: cevir('chat.errorOccurred'), smells: [], timestamp: new Date().toISOString() }]);
      }
    } finally {
      // A stopped or deleted turn was already cleaned up by whoever ended it.
      if (ownsTurn()) {
        turnRef.current.delete(targetConvId);
        controllersRef.current.delete(targetConvId);
        const visible = onScreen();
        patchConv(targetConvId, r => ({
          loading: false, activity: null, unread: !visible, unsynced: !visible,
          clientOnly: r.clientOnly || lossy,
          // Failed = the backend reported an error, or the stream ended without
          // `done`/`response`. A transport error AFTER `done` does not undo a
          // finished turn (Codex notifyaudit, done-then-read-error).
          turnEnd: { seq: ++eventSeqRef.current, failed: errored || !finishedCleanly },
        }));
        if (!visible && finishedCleanly && !lossy) void syncFinished(targetConvId);
      }
    }
  }, [API, aiConfig.provider_type, aiConfig.model_name, createNewConversation, fetchConversations, patchConv, rt, suggestFilePath, syncFinished, user, workspacePath]);

  // ── AUTO-WAKE channel ────────────────────────────────────────────────────
  // Once a background task finishes, the backend sends ONE coalesced `wake`
  // frame from `/wake-stream`; we start the turn from here. There is no
  // server-side loop to resume the turn (one run = one HTTP request), so the
  // client has to be the one deciding to "continue".
  //
  // `fetch`, NOT `EventSource`: like its sibling endpoints, this one wants an
  // `X-Session-Token` header, and EventSource can't send headers. Putting the
  // token in the query string would write it into the address bar and logs.
  //
  // `loading` is a dependency: the channel closes while a turn is running and
  // reopens once it ends. This way a second turn can't be started on top of one
  // already in flight. It is the ON-SCREEN chat's own flag: a turn running in
  // another chat does not keep this one from waking. The channel still exists
  // only for the chat on screen (a background chat does not wake in slice 1).
  useEffect(() => {
    if (!API || !user || !activeConvId || loading) return;
    const convId = activeConvId;
    const ac = new AbortController();
    let iptal = false;
    (async () => {
      try {
        const res = await fetch(`${API}/conversations/${convId}/wake-stream`, {
          headers: { 'X-Session-Token': user.sessionToken },
          signal: ac.signal,
        });
        const reader = res.body?.getReader();
        if (!reader) return;
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parcalar = buffer.split('\n\n');
          buffer = parcalar.pop() || '';
          for (const parca of parcalar) {
            const satir = parca.split('\n').find(l => l.startsWith('data: '));
            if (!satir) continue;
            let data: any;
            const payload = satir.slice(6);
            try { data = JSON.parse(payload); } catch (err) {
              console.warn('[AUTO-WAKE] malformed wake frame:', payload.slice(0, 500), err);
              continue;
            }
            if (data?.type !== 'wake' || iptal) continue;
            const args = lastSendArgsRef.current;
            // No args means nothing has been sent yet in this session; in that
            // case dropping the wake is better than starting a turn in a mode
            // that was made up.
            if (!args) continue;
            void sendMessage(
              String(data.text || ''), '', args.lang, args.genMode, args.thinkingLevel,
              args.setPendingGenFiles, args.setPendingDelete,
              undefined, false, undefined, 'wake', convId,
            );
          }
        }
      } catch {
        // Abort or a dropped connection: a wake is best-effort, not a failure to
        // show the user — the chat can still be continued by hand.
      }
    })();
    return () => { iptal = true; ac.abort(); };
  }, [API, activeConvId, loading, sendMessage, user]);

  const clearHistory = useCallback(async () => {
    if (!activeConvId) return;
    try {
      // session-clear IPC channel removed; session management now handled by auth layer
      setMessages([]);
      showToast(cevir('chat.historyCleared'), 'info');
    } catch (err) { showToast(cevir('chat.historyClearFailed'), 'error'); }
  }, [activeConvId, setMessages, showToast]);

  const analyzeProject = useCallback(async (silent = false) => {
    if (!user || !API) return;

    // Aktif sohbet yoksa Projeyi Öğren tek tıkla çalışsın diye yenisini açıyoruz.
    let targetConvId = activeConvId;
    if (!targetConvId) {
      targetConvId = await createNewConversation(cevir('chat.projectAnalysisTitle'));
      if (!targetConvId) return;
    }

    setIsAnalyzingProject(true);
    try {
      const res = await axios.post(`${API}/conversations/${targetConvId}/analyze-project`, {}, {
        headers: { 'X-Session-Token': user.sessionToken }, timeout: 120000
      });
      if (res.data.status === 'success') {
        if (!silent) {
          showToast(cevir('memory.learned', { sayi: res.data.file_count }), 'success');
          const convId = targetConvId;
          patchConv(convId, r => ({ messages: [...r.messages, { id: Date.now(), role: 'assistant', content: `${cevir('memory.analysisReport')}\n\n${res.data.summary}`, timestamp: new Date().toISOString(), smells: [] }] }));
        }
      }
    } catch (err: any) { if (!silent) showToast(cevir('memory.analysisError'), 'error'); }
    finally { setIsAnalyzingProject(false); }
  }, [API, activeConvId, createNewConversation, patchConv, showToast, user]);

  const exportMemory = useCallback(async () => {
    if (!activeConvId || !user || !API) return;
    try {
      const res = await axios.get(`${API}/conversations/${activeConvId}/export-memory`, { headers: { 'X-Session-Token': user.sessionToken } });
      if (!res.data.content) { showToast(cevir('memory.none'), 'error'); return; }
      const out = await ipc?.invoke('export-text-file', `wisdom_${activeConvId}.md`, res.data.content);
      if (out?.canceled) return;
      if (out?.success) showToast(cevir('memory.saved'), 'success');
      else showToast(cevir('memory.saveFailed', { hata: out?.error || cevir('common.unknownError') }), 'error');
    } catch { showToast(cevir('memory.exportError'), 'error'); }
  }, [API, activeConvId, showToast, user]);

  const importMemory = useCallback(async () => {
    if (!activeConvId || !user || !API) return;
    try {
      const res = await ipc?.invoke('import-text-file', { filters: [{ name: 'Markdown', extensions: ['md'] }] });
      if (res?.canceled) return;
      if (res?.content) {
        await axios.post(`${API}/conversations/${activeConvId}/import-memory`, { content: res.content }, { headers: { 'X-Session-Token': user.sessionToken } });
        showToast(cevir('memory.imported'), 'success');
        patchConv(activeConvId, r => ({ messages: [...r.messages, { id: Date.now(), role: 'assistant', content: cevir('memory.importedHeading'), timestamp: new Date().toISOString(), smells: [] }] }));
      }
    } catch { showToast(cevir('memory.importError'), 'error'); }
  }, [API, activeConvId, patchConv, showToast, user]);

  const compactConversation = useCallback(async () => {
    if (!activeConvId || !API || !user) return;
    setIsCompacting(true);
    showToast(cevir('compact.running'), 'info');
    try {
      // Timeout ŞART: backend'de AI özetleme takılırsa buton sonsuza dek kilitli
      // kalıyordu ("basınca bir şey olmuyor" bug'ı). Backend 120s'de fallback'e düşer.
      const res = await axios.post(`${API}/conversations/${activeConvId}/compact`, {}, {
        headers: { 'X-Session-Token': user.sessionToken }, timeout: 150000,
      });
      if (res.data.status === 'success') {
        if (res.data.summary) {
          const msgRes = await axios.get(`${API}/conversations/${activeConvId}/messages`);
          patchConv(activeConvId, () => ({ messages: msgRes.data, unsynced: false, clientOnly: false }));
          // Eskiden buraya sabit `percent: 5` yazılıyordu — sıkıştırmadan sonra
          // doluluğun ne olduğu ölçülmeden, makul görünen bir sayıyla. Gösterge
          // artık tek kaynaktan tazeleniyor.
          await refreshContextUsage(activeConvId);
          showToast(cevir('compact.done'), 'success');
        } else {
          // Backend'in `message`'ı sabit TÜRKÇE — İngilizce arayüzde Türkçe toast
          // çıkıyordu. Metin sözlükten geliyor; backend yalnız hangi dal olduğunu söylüyor.
          showToast(cevir('compact.tooShort'), 'info');
        }
      }
    } catch { showToast(cevir('compact.error'), 'error'); } finally { setIsCompacting(false); }
  }, [API, activeConvId, patchConv, showToast, user, refreshContextUsage]);

  // Stop acts on the chat on screen and nothing else: its own stream, its own
  // backend turn, its own cards. Bridge cards (`globalCommand`, the tray) are
  // not cleared here: the backend denies the ones this Stop covers and they
  // leave with the next `/mcp-pending` poll; the rest stay decidable.
  const stopMessage = useCallback(() => {
    const convId = activeConvIdRef.current;
    const key = keyOf(convId);
    turnRef.current.delete(key);
    controllersRef.current.get(key)?.abort();
    controllersRef.current.delete(key);
    // Claude SDK turunu gerçekten iptal et (bekleyen onay/soru gate'lerini çöz + interrupt)
    if (convId && user) {
      fetch(`${API}/chat-stop/${convId}`, {
        method: 'POST',
        headers: { 'X-Session-Token': user.sessionToken },
      }).catch(() => {});
    }
    // Bekleyen onay/soru kartlarını ve kuyrukları temizle (backend gate'leri reddetti)
    patchConv(key, () => ({
      pendingCommand: null, commandQueue: [], pendingQuestion: null, questionQueue: [],
      activity: null, loading: false,
    }));
  }, [API, patchConv, user]);

  // The chat holding a gate; the screen's chat when none does (a card set
  // directly through `setPendingQuestion`).
  const ownerOf = (match: (r: ConvRuntime) => boolean) => {
    const hit = Object.entries(runtimesRef.current).find(([, r]) => match(r));
    return hit ? Number(hit[0]) : keyOf(activeConvIdRef.current);
  };

  const convStatus = useMemo(() => {
    const out: Record<number, ConvStatus> = {};
    for (const [key, r] of Object.entries(runtimes)) {
      const id = Number(key);
      if (id === NO_CONV) continue;
      if (isAwaiting(r)) {
        out[id] = 'awaiting';
      }
      else if (r.loading) out[id] = 'running';
      else if (r.unread && id !== activeConvId) out[id] = 'unread';
    }
    return out;
  }, [runtimes, activeConvId]);

  const attention = useMemo(() => {
    const out: Record<number, ChatAttention> = {};
    for (const [key, r] of Object.entries(runtimes)) {
      const id = Number(key);
      if (id === NO_CONV) continue;
      const approvals = [
        ...[r.pendingCommand, ...r.commandQueue].filter(Boolean).map(c => `cmd:${c!.gateId}`),
        ...[r.pendingQuestion, ...r.questionQueue].filter(Boolean).map(q => `q:${q!.gateId}`),
      ];
      if (r.lastCard) approvals.push(`card:${r.lastCard}`);
      out[id] = { approvals, bridgeGates: r.bridgeGates, awaiting: isAwaiting(r), turnEnd: r.turnEnd };
    }
    return out;
  }, [runtimes]);

  return {
    conversations, setConversations,
    activeConvId, setActiveConvId,
    messages, setMessages,
    loading, setLoading,
    chatInput, setChatInput,
    contextUsage, setContextUsage, applyContextReport,
    isCompacting, setIsCompacting,
    isAnalyzingProject, setIsAnalyzingProject,
    pendingFix, setPendingFix,
    pendingCommand, setPendingCommand, setBridgeGates,
    pendingQuestion, setPendingQuestion,
    activity,
    generationMode, setGenerationMode,
    editingId, setEditingId,
    tempTitle, setTempTitle,
    fetchConversations, fetchMessages, createNewConversation,
    selectConversation, deleteConversation, saveRename,
    branchConversation, setBranchHidden, closeBranch,
    convStatus, attention,
    sendMessage, stopMessage,
    clearHistory, analyzeProject, exportMemory, importMemory, compactConversation,
    // Kararı backend'e iletir ve İLETİLDİĞİNİ DOĞRULAR. Yanıt gövdesi eskiden
    // hiç okunmuyordu: gate düşmüşse backend {"status":"gate_not_found"} dönüyor,
    // kart yine de kapanıyor ve kullanıcı onayladığını sanıyordu (sessiz veri
    // kaybı, ölçüldü 2026-07-28). Kart yine kapanır — asılı kalması daha kötü —
    // ama kullanıcı ne olduğunu görür.
    approveCommand: async (gateId: string, approved: boolean) => {
      let failure = null as ReturnType<typeof gateFailure>;
      try {
        const res = await fetch(`${API}/command-approval/${gateId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Session-Token': user?.sessionToken ?? '' },
          body: JSON.stringify({ approved }),
        });
        failure = gateFailure('command', await deliveryFromFetch(res));
      } catch (err) {
        console.warn('approveCommand fetch failed', err);
        failure = gateFailure('command', { httpOk: false, error: err });
      }
      if (failure) showToast(failure.message, failure.type);
      // Çözüldü → kuyrukta sıradaki onayı göster (yoksa kapat)
      if (globalCommandRef.current?.gateId === gateId) {
        setPendingCommand(null);
      } else {
        patchConv(ownerOf(r => r.pendingCommand?.gateId === gateId), r => ({
          pendingCommand: r.commandQueue[0] ?? null, commandQueue: r.commandQueue.slice(1),
        }));
      }
      // Sonucu ÇAĞIRANA da ver: kart, "Komut onaylandı — çalışıyor..." yeşil
      // toast'ını koşulsuz basıyordu; kullanıcı sarı "iletilemedi" ile yeşili
      // aynı anda görüyordu (Toast.tsx:32 toast'ları diziye ekliyor).
      // Mesajı burada basmaya devam ediyoruz — bu fonksiyonun kart olmadan da
      // (kuyruk yolu) çağrıldığı yerler var.
      return failure;
    },
    // AskUserQuestion (A/B/C) cevabı: { "<soru metni>": "<seçilen label>" }
    answerQuestion: async (gateId: string, answers: Record<string, string>) => {
      let failure = null as ReturnType<typeof gateFailure>;
      try {
        const res = await fetch(`${API}/question-answer/${gateId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Session-Token': user?.sessionToken ?? '' },
          body: JSON.stringify({ answers }),
        });
        failure = gateFailure('question', await deliveryFromFetch(res));
      } catch (err) {
        console.warn('answerQuestion fetch failed', err);
        failure = gateFailure('question', { httpOk: false, error: err });
      }
      if (failure) showToast(failure.message, failure.type);
      // Çözüldü → kuyrukta sıradaki soruyu göster (yoksa kapat)
      patchConv(ownerOf(r => r.pendingQuestion?.gateId === gateId), r => ({
        pendingQuestion: r.questionQueue[0] ?? null, questionQueue: r.questionQueue.slice(1),
      }));
    },
  };
};
