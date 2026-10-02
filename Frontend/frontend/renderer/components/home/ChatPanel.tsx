import React, { useState, useEffect, useRef, useMemo } from 'react';
import { motion } from 'framer-motion';
import { useLang } from '../../lib/i18n';
import {
  User,
  Cpu,
  History,
  Trash2,
  AlertTriangle,
  Bot,
  Sparkles,
  RefreshCw,
  Mail
} from 'lucide-react';
import { Message, UserData, FileEntry, GenerationMode, ChatActivity, Conversation } from './types';
import { ModelAvatar } from './ModelAvatar';
import { messageAgent } from './messageAgent';
import { MarkdownRenderer } from './MarkdownRenderer';
import { stripBidi } from '../../lib/modelText';
import { SlashCommandCard } from './SlashCommandCard';
import { ThinkingBlock } from './ThinkingBlock';
import { ToolGroup } from './ToolGroup';
import { FileCreationApproval, PendingFile } from './FileCreationApproval';
import { FileDeleteApproval } from './FileDeleteApproval';
import { CommandApproval } from './CommandApproval';
import { QuestionApproval } from './QuestionApproval';
import { DiffViewer, DiffData } from './DiffViewer';
import { postMcpDecision, decisionToast, GateFailure } from '../../hooks/home/gateResponse';
import { McpActiveGate } from '../../hooks/home/useMCPApproval';
import { McpApprovalCards } from './McpApprovalCards';
import { MessageNotices } from './MessageNotices';
import { modelFamily } from '../../lib/modelFamily';

/**
 * hh:mm for a message's author line. The backend stores naive local "YYYY-MM-DD HH:MM:SS" (no
 * zone), which `new Date` would read as local anyway but parses differently across engines, so
 * that form is read literally; ISO stamps from the live stream go through Date. An unparsable
 * stamp shows no time rather than a wrong one.
 */
export const clockOf = (stamp?: string | null): string => {
  if (!stamp) return '';
  const naive = /^\d{4}-\d{2}-\d{2}[ T](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$/.exec(stamp);
  if (naive) return `${naive[1]}:${naive[2]}`;
  const d = new Date(stamp);
  if (Number.isNaN(d.getTime())) return '';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/** The mockup's "working" line: three hopping dots, then what the agent is doing right now. */
const Working: React.FC<{ text: string; tokens?: string | null; elapsed: string }> = ({ text, tokens, elapsed }) => (
  <div className="working is-on" aria-live="polite">
    <span className="dots" aria-hidden="true"><i /><i /><i /></span>
    <span className="step"><span className="step-text">{text}</span></span>
    {tokens && <span className="working-meta">· {tokens}</span>}
    <span className="working-meta num">· {elapsed}</span>
  </div>
);

/** Fixed start of a stored note between chats (backend `mailbox.MAIL_MARKER`). */
export const MAIL_MARKER = '📨';
export const isMailNote = (msg: Pick<Message, 'role' | 'content'>) =>
  msg.role === 'system' && typeof msg.content === 'string' && msg.content.startsWith(MAIL_MARKER);
/** A note the startup sweep could not deliver because the app restarted
 *  (backend `mailbox.UNDELIVERED_MARKER`, owner decision, 28 Sep 2026). A
 *  distinct marker from MAIL_MARKER on both sides of this line, on purpose:
 *  it renders grey here instead of the amber mail-note style, and it stays
 *  off `is_mail_message` on the backend so a CLI handoff never replays it as
 *  a note the model could act on. */
export const UNDELIVERED_MARKER = '📭';
export const isUndeliveredNote = (msg: Pick<Message, 'role' | 'content'>) =>
  msg.role === 'system' && typeof msg.content === 'string' && msg.content.startsWith(UNDELIVERED_MARKER);
/** A forwarded reply's header tag (backend `mailbox.AUTO_FORWARD_TAG`): the
 *  other chat ended its turn without sending, so its last message came here. */
export const MAIL_AUTO_TAG = '[otomatik iletildi]';
const MAIL_AUTO_HEADER = /^(📨 #\d+) \[otomatik iletildi\] /gmu;
/** The note text without the tag, and whether any note in it carried one. */
export const mailNoteParts = (content: string) => {
  const text = content.replace(MAIL_AUTO_HEADER, '$1 ');
  return { text, autoForwarded: text !== content };
};

/**
 * Nothing to draw in the thread: no chat yet, or a chat with no messages and no turn running,
 * and no Unity request waiting. home.tsx shows the empty new chat (mission board) in that case;
 * ChatPanel draws nothing. One predicate for both, so they cannot disagree.
 *
 * The MCP card is the exception on purpose (see `hasMcpCard` below): the bridge works without a
 * chat, and a card hidden behind an empty-state screen is rejected after 180 s unseen.
 */
export const isChatEmpty = (
  activeConvId: number | null,
  messageCount: number,
  loading: boolean,
  hasMcpCard: boolean,
): boolean => !hasMcpCard && (!activeConvId || (messageCount === 0 && !loading));

interface ChatPanelProps {
  messages: Message[];
  activeConvId: number | null;
  user: UserData | null;
  loading: boolean;
  clearHistory: () => void;
  lang: string;
  thinkingLevel: 'auto' | 'off' | 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
  workspacePath: string | null;
  handleExportToUnity: (code: string) => void;
  pendingGenFiles: { files: PendingFile[]; messageId: number } | null;
  setPendingGenFiles: (val: any) => void;
  pendingFix: { data: DiffData; messageId?: number; applied?: boolean; gateId?: string } | null;
  setPendingFix: (val: any) => void;
  openedFilePath: string | null;
  setCode: (code: string) => void;
  setOriginalCode?: (code: string) => void;
  refreshFileTree: () => void;
  analyzeProject: (silent?: boolean) => void;
  openFile: (path: string) => void;
  sendMessage: (msg: string) => void;
  messagesEndRef: React.RefObject<HTMLDivElement>;
  ipc: any;
  showToast: (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;
  diffFile: any | null;
  setDiffFile: (val: any | null) => void;
  pendingDelete: { path: string; messageId: number } | null;
  setPendingDelete: (val: any | null) => void;
  pendingCommand: { command: string; gateId: string; messageId: number; kind?: 'shell' | 'unity' | 'mail'; riskReason?: string; riskDetail?: string } | null;
  setPendingCommand: (val: any | null) => void;
  /** Kararın backend'e ULAŞMADIĞINI döner (`null` = ulaştı). Kart, başarı
   *  iddiasını buna bakarak basar; hata metnini çağrılan taraf kendi basıyor. */
  onApproveCommand: (gateId: string, approved: boolean) => Promise<GateFailure | null>;
  pendingQuestion: { questions: any[]; gateId: string; messageId: number } | null;
  setPendingQuestion: (val: any | null) => void;
  onAnswerQuestion: (gateId: string, answers: Record<string, string>) => Promise<void>;
  deleteFile: (path: string) => Promise<void>;
  setIsTerminalOpen: (val: boolean) => void;
  /** Backend kök adresi. Kart kararları buraya POST'lanır. Eskiden
   *  `window.__API__` global'inden okunuyordu — test edilemez ve sessizce boş
   *  kalabilir bir bağımlılıktı. */
  apiBase: string;
  /** Karar bekleyen MCP isteği; `null` ise MCP kartı yok. `useMCPApproval`'dan. */
  mcpGate: McpActiveGate | null;
  mcpWorkspaceMismatch: boolean;
  /** Karsilastirma henuz yapilamiyor; banner "dogrulaniyor" der. */
  mcpWorkspaceCheckPending?: boolean;
  mcpOpenWorkspacePath: string | null;
  onMcpResolved: () => void;
  // Canlı aktivite (status event'leri): "🤖 Subagent çalışıyor · 45.2k token" gibi.
  activity?: ChatActivity | null;
  /** Titles for the `@<id>` chips in the user's bubbles. */
  conversations?: Conversation[];
  /** A phone is paired: pending cards say it can approve there too. */
  phonePaired?: boolean;
}

export const ChatPanel: React.FC<ChatPanelProps> = ({
  messages,
  activeConvId,
  user,
  loading,
  clearHistory,
  thinkingLevel,
  workspacePath,
  handleExportToUnity,
  pendingGenFiles,
  setPendingGenFiles,
  pendingFix,
  setPendingFix,
  openedFilePath,
  setCode,
  setOriginalCode,
  refreshFileTree,
  analyzeProject,
  openFile,
  sendMessage,
  messagesEndRef,
  ipc,
  showToast,
  diffFile,
  setDiffFile,
  pendingDelete,
  setPendingDelete,
  pendingCommand,
  setPendingCommand,
  onApproveCommand,
  pendingQuestion,
  setPendingQuestion,
  onAnswerQuestion,
  deleteFile,
  activity,
  apiBase,
  mcpGate,
  mcpWorkspaceMismatch,
  mcpWorkspaceCheckPending,
  mcpOpenWorkspacePath,
  onMcpResolved,
  conversations,
  phonePaired,
}) => {
  const { t } = useLang();
  // A card keeps the file/workspace it was created for across editor navigation.
  const fixTarget = useRef<{ data: DiffData; path: string | null; workspace: string | null } | null>(null);
  if (!pendingFix) fixTarget.current = null;
  else if (fixTarget.current?.data !== pendingFix.data) {
    fixTarget.current = { data: pendingFix.data, path: openedFilePath, workspace: workspacePath };
  }
  const currentEditor = useRef({ path: openedFilePath, workspace: workspacePath });
  currentEditor.current = { path: openedFilePath, workspace: workspacePath };
  // Stable across stream tokens, so the memoised bubbles do not re-parse.
  const mentionTitles = useMemo(
    () => new Map((conversations || []).map(c => [c.id, c.title] as [number, string])),
    [conversations]);

  /**
   * IPC yazma reddinin kullanıcıya gösterilecek metni.
   *
   * `ipc.invoke('write-file')` ASLA throw etmiyor; `{success:false, error}`
   * dönüyor (main/background.ts:397-418) ve reddetme gerçek — örneğin
   * `Assets/Scripts` dışındaki bir `.cs` `isAllowedWorkspaceWriteFile` tarafından
   * reddediliyor. Ham `error` alanı AYNEN taşınıyor: "bir hata oldu" kullanıcıya
   * ne yapacağını söylemiyor, "workspace içindeki kod/metin dosyalarına
   * yazılabilir" söylüyor.
   */
  const writeErrorText = (name: string, res: any) =>
    t('chat.writeFailed', { ad: name, hata: res?.error || t('common.unknownError') });

  // 12400 → "12.4k" (aktivite satırı + usage özeti için)
  const fmtTok = (n?: number | null) =>
    typeof n === 'number' && n > 0 ? (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`) : null;

  // Backend'den BAĞIMSIZ, saniyede tikleyen sayaç: token/aktivite metni uzunca
  // değişmese bile (örn. büyük bir dosya okunurken) kullanıcı "hala çalışıyor mu
  // yoksa dondu mu" diye soruyordu — bu sayaç ilerliyorsa süreç KESİN canlı,
  // donarsa (donmuş sayı) gerçekten bir renderer/bağlantı sorunu var demektir.
  const turnStartRef = useRef<number | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);

  // Resim lightbox: window.open(dataURI) Electron'da bembeyaz sekme açıyordu
  // (data: URL yeni pencerede render edilmiyor) → uygulama içi tam ekran önizleme.
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!lightboxSrc) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setLightboxSrc(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lightboxSrc]);
  useEffect(() => {
    if (!loading) { turnStartRef.current = null; setElapsedSec(0); return; }
    turnStartRef.current = Date.now();
    setElapsedSec(0);
    const iv = setInterval(() => {
      if (turnStartRef.current) setElapsedSec(Math.floor((Date.now() - turnStartRef.current) / 1000));
    }, 1000);
    return () => clearInterval(iv);
  }, [loading]);
  const fmtElapsed = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

  /**
   * MCP köprüsünden gelen, gerçek bir sohbet mesajına bağlı OLMAYAN bir onay
   * kartı bekliyor mu?
   *
   * Aşağıdaki iki erken `return` bu kontrol olmadan kartı YUTUYORDU: köprü
   * (`approval_bridge.py`) ürünün sohbet durumundan bağımsız çalışıyor, yani
   * "henüz konuşma açılmamış" ya da "mesaj listesi boş" olduğu anda gelen bir
   * istek ekrana hiç çıkmıyor, 180 sn sonra reddediliyordu. Kartın kendisi
   * zaten mesaj listesinin DIŞINDA (`McpApprovalCards`) çiziliyor; onu boş
   * listeye takılan bir kapının arkasında bırakmak gizli bir bağımlılıktı.
   *
   * Koşul artık dört ayrı `messageId` karşılaştırması değil tek bir kayda
   * bakıyor: kartı kuran hook zaten "şu an karar bekleyen istek bu" diyor.
   * Dördünü burada tekrar saymak, hook ile ayrışabilecek ikinci bir doğruluk
   * kaynağıydı — bu depodaki arızaların ortak biçimi tam olarak o.
   */
  const hasMcpCard = mcpGate !== null;

  // The empty new chat is drawn by home.tsx in the thread's place (mission board).
  if (isChatEmpty(activeConvId, messages.length, loading, hasMcpCard)) return null;

  /**
   * Mesaja bağlı (API akışı) düzeltme kartı — `messageId === msg.id`.
   *
   * MCP akışının kartı BURADA DEĞİL, `McpApprovalCards` içinde. 2026-07-29'da
   * ikisi tek elemanda birleştirilmişti; gerekçesi "MCP dalı hiç yazılmamıştı,
   * kopyalar sessizce ayrışıyor" idi. Akışlar ayrı bileşenlere alınınca o
   * gerekçe düştü ve birleşik eleman iki farklı davranışı (köprüye POST vs.
   * IPC ile diske yazma) tek `if` ile taşıyan bir yük haline geldi.
   *
   * `pendingFix.gateId` bu daldan kaldırıldı çünkü o alanı YALNIZ
   * `useMCPApproval` yazıyor ve o kayıt `messageId: MCP_MSG_ID` taşıyor —
   * gerçek bir `msg.id` ile asla eşleşmez, yani buradaki gate dalı ulaşılamaz
   * koddu. (API akışının kendi tipinde `gateId` alanı hiç yok:
   * `useChat.ts:29`.)
   */
  const target = fixTarget.current;
  const pendingFixCard = pendingFix && target ? (
    <DiffViewer
      diffData={pendingFix.data}
      filename={target.path?.split(/[\\/]/).pop() || pendingFix.data?.editor_hint?.split('/').pop()}
      filePath={target.path}
      applied={pendingFix.applied}
      onAccept={async (fixedCode) => {
        const isOpenTarget = () => currentEditor.current.path === target.path
          && currentEditor.current.workspace === target.workspace;
        if (isOpenTarget()) setCode(fixedCode);
        if (!ipc || !target.path || !target.workspace) {
          // Hiçbir yazım denenmedi: diskte değişen bir şey yok.
          // Eskiden burada da "✅ Dosya güncellendi" basılıyordu.
          setPendingFix((prev: any) => prev ? { ...prev, applied: true } : null);
          showToast(t('chat.diffApplied'), 'info');
          return;
        }
        const res = await ipc.invoke('write-file', target.path, fixedCode, target.workspace);
        if (!res?.success) {
          showToast(writeErrorText(target.path.split(/[\\/]/).pop() || target.path, res), 'error');
          return;
        }
        if (isOpenTarget()) {
          setCode(fixedCode);
          setOriginalCode?.(fixedCode);
        }
        setPendingFix((prev: any) => prev ? { ...prev, applied: true } : null);
        refreshFileTree();
        setTimeout(() => analyzeProject(true), 1500);
        showToast(t('chat.fileUpdated'), 'success');
      }}
      onReject={() => setPendingFix(null)}
    />
  ) : null;

  return (
    <div className="thread-col" data-testid="thread-col">
        {messages.map((msg, msgIdx) => {
          // /usage, /context → özel kart. Canlı turda mesaj etiketli gelir (slashCommand);
          // geçmişten yüklenende etiket yok → bir önceki kullanıcı mesajından tespit et.
          let slashCmd: string | undefined = msg.slashCommand;
          if (!slashCmd && msg.role === 'assistant' && msgIdx > 0) {
            const prevC = (messages[msgIdx - 1]?.role === 'user' ? messages[msgIdx - 1].content : '').trim().toLowerCase();
            if (prevC === '/usage') slashCmd = 'usage';
            else if (prevC === '/context') slashCmd = 'context';
          }
          let wakeText = msg.content;
          if (msg.role === 'system') {
            wakeText = msg.content.split(' · ').map((part) => {
              const separator = part.indexOf('|');
              if (separator < 0) return part;
              const reason = part.slice(0, separator);
              const detail = part.slice(separator + 1);
              const wakeKey = reason === 'tasks_done_saved'
                ? 'chat.wakeRow.tasks_done_saved'
                : reason === 'tasks_done'
                  ? 'chat.wakeRow.tasks_done'
                  : null;
              // A mail notice's detail is only a chat number; the note itself
              // replaces this row once the turn starts (`wake_message`).
              if (reason === 'mail') return t('chat.wakeRow.mail');
              return wakeKey ? `${t(wakeKey)} — ${detail}` : part;
            }).join(' · ');
          }
          // The message's own agent, never the current selection (Burak,
          // 27 Sep 2026): an answer from before a switch keeps its writer.
          const agent = msg.role === 'assistant' ? messageAgent(msg.provider, msg.model) : null;
          return (
          <React.Fragment key={msg.id}>
            {isMailNote(msg) ? (() => {
              /* A note another chat's AI left here (backend agentic/mailbox.py).
                 Its own bubble: not the user's words (no blue bubble), and not
                 the muted wake row either, since the note is content the user
                 should read. */
              const note = mailNoteParts(msg.content);
              return (
              <div data-testid="mail-note" className="msg-note">
                <div className="msg-note-head">
                  <Mail size={13} className="shrink-0" aria-hidden="true" />
                  {t('chat.mailNote')}
                  {note.autoForwarded && (
                    <span data-testid="mail-note-auto" className="msg-note-tag">
                      {t('chat.mailNoteAuto')}
                    </span>
                  )}
                </div>
                <p className="msg-note-body">
                  {stripBidi(note.text.slice(MAIL_MARKER.length).trimStart())}
                </p>
              </div>
              );
            })() : isUndeliveredNote(msg) ? (
              /* A note the startup sweep could not deliver (backend
                 agentic/mailbox.py, owner decision, 28 Sep 2026): its own
                 grey bubble, visually distinct from the amber mail-note above
                 so it reads as inactive rather than something this chat can
                 still act on. */
              <div data-testid="mail-note-undelivered" className="msg-note is-undelivered">
                <div className="msg-note-head">
                  <Mail size={13} className="shrink-0" aria-hidden="true" />
                  {t('chat.mailUndelivered')}
                </div>
                <p className="msg-note-body">
                  {stripBidi(msg.content.slice(UNDELIVERED_MARKER.length).trimStart())}
                </p>
              </div>
            ) : msg.role === 'system' ? (
              /* AUTO-WAKE row. This branch is MANDATORY: the ternary below only
                 distinguished assistant/other, so a `system` role would render
                 as a BLUE USER BUBBLE — a sentence the user never wrote would
                 look like theirs. The event row is deliberately muted: not a
                 message, but a system marker saying the chat continued on its
                 own. */
              <div className="msg-event" data-role="system">
                <RefreshCw size={13} className="shrink-0" aria-hidden="true" />
                <span className="msg-event-k">{t('chat.wakeRow')}</span>
                <span className="msg-event-v">· {wakeText}</span>
              </div>
            ) : msg.role === 'assistant' ? (
              // The author line is the model's nameplate (mockup `.msg-who`): the dot keeps the
              // colour of the model that WROTE the answer (m4), not the current pick.
              <article
                className="msg msg-ai"
                data-role="assistant"
                data-m={agent ? modelFamily(msg.model, agent.brand) : 'other'}
              >
                <div className="msg-who">
                  <span className="model-dot" aria-hidden="true" />
                  <span className="who-name" data-testid="message-agent">
                    {agent ? (agent.model ? `${agent.name} · ${agent.model}` : agent.name) : 'AI'}
                  </span>
                  {clockOf(msg.timestamp) && <time>{clockOf(msg.timestamp)}</time>}
                  {msg.usage && (msg.usage.duration_ms || msg.usage.output_tokens) ? (
                    <span className="msg-meta num">
                      {msg.usage.duration_ms ? `· ${Math.max(1, Math.round(msg.usage.duration_ms / 1000))}sn` : null}
                      {fmtTok(msg.usage.output_tokens) ? ` · ↓${fmtTok(msg.usage.output_tokens)}` : null}
                    </span>
                  ) : null}
                </div>
                <div className="msg-stack">
                  {/* Statik Bulgular */}
                  {msg.smells && msg.smells.length > 0 && (
                    <div className="smells">
                      <div className="smells-head">
                        <AlertTriangle size={13} aria-hidden="true" />
                        <span>Static Analysis</span>
                      </div>
                      <div className="smells-list">
                        {msg.smells.map((s: any, i: number) => (
                          <div key={i} className="smells-row">
                            <span className="smells-line num">
                              L{s.line || "?"}
                            </span>
                            <span>{s.msg}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Thinking Block */}
                  {msg.thinking && (
                    <ThinkingBlock thinking={msg.thinking!} durationMs={msg.thinking_duration_ms} />
                  )}

                  {/* Tool Blocks (3'ten fazlaysa collapse grubu) */}
                  <ToolGroup tools={msg.tool_calls} onOpenFile={openFile} />
                  <ToolGroup tools={msg.tools} onOpenFile={openFile} />

                  {/* Content or Loading Typing */}
                  {(msg.content === "" || !msg.content) && loading && msgIdx === messages.length - 1 ? (
                    // "Thinking": the mockup's working line (dots + the current step) plus the
                    // live counter. A counter that moves proves the turn is alive ("is it stuck?").
                    <Working
                      text={activity?.detail || t('chat.thinking')}
                      tokens={activity ? fmtTok(activity.tokens) : null}
                      elapsed={fmtElapsed(elapsedSec)}
                    />
                  ) : slashCmd ? (
                    <SlashCommandCard command={slashCmd} text={msg.content} workspacePath={workspacePath} onOpenFile={openFile} />
                  ) : (
                    <div className="msg-body">
                      <MarkdownRenderer
                        content={msg.content.replace('<!-- SCOPE_WARNING_ACTIVE -->', '')}
                        workspacePath={workspacePath}
                        onExportToUnity={handleExportToUnity}
                        onOpenFile={openFile}
                      />
                    </div>
                  )}

                  <MessageNotices notices={msg.notices} />

                  {/* Tur istatistiği artık mesajın ÜSTÜNDEKİ meta satırında */}

                  {/* Scope Warning Buttons */}
                  {msg.content.includes('SCOPE_WARNING_ACTIVE') && msgIdx === messages.length - 1 && !loading && (
                    <div className="msg-actions">
                      <button type="button" onClick={() => sendMessage(t('chat.generateFull'))} className="btn btn-primary"> ✅ {t('chat.generateFull')} </button>
                      <button type="button" onClick={() => sendMessage(t('chat.simpleVersion'))} className="btn btn-ghost"> ⚡ {t('chat.simpleVersion')} </button>
                    </div>
                  )}

                  {/* File Creation Approval */}
                  {pendingGenFiles && pendingGenFiles.messageId === msg.id && (
                    // No phone hint: this flow writes through IPC on this machine; the card is
                    // not in the backend's card registry, so no phone can decide it.
                    <FileCreationApproval
                      files={pendingGenFiles.files}
                      autoAccept={false}
                      onAcceptOne={async (file) => {
                        if (!ipc || !workspacePath) return false;
                        const res = await ipc.invoke('write-file', file.suggestedPath, file.code, workspacePath);
                        if (!res?.success) { showToast(writeErrorText(file.name, res), 'error'); return false; }
                        if (file.suggestedPath === openedFilePath) setCode(file.code);
                        refreshFileTree();
                        return true;
                      }}
                      onSkipOne={() => { }}
                      onAcceptAll={async (files) => {
                        if (!ipc || !workspacePath) return false;
                        let allWritten = true;
                        for (const file of files) {
                          const res = await ipc.invoke('write-file', file.suggestedPath, file.code, workspacePath);
                          if (!res?.success) {
                            // Kalanları yazmaya devam et: bir dosyanın reddedilmesi
                            // (ör. `Assets/Scripts` dışındaki bir `.cs`) diğerlerini
                            // engellemesin. Hata dosya BAŞINA bildirilir, çünkü
                            // sebep dosyaya özgü.
                            showToast(writeErrorText(file.name, res), 'error');
                            allWritten = false;
                            continue;
                          }
                          if (file.suggestedPath === openedFilePath) setCode(file.code);
                        }
                        refreshFileTree();
                        setTimeout(() => analyzeProject(true), 1500);
                        return allWritten;
                      }}
                      onDone={() => {
                        setPendingGenFiles(null);
                        setDiffFile(null);
                      }}
                      setDiffFile={setDiffFile}
                      onOpenFile={(path) => openFile(path)}
                    />
                  )}

                  {/* File Deletion Approval */}
                  {pendingDelete && pendingDelete.messageId === msg.id && (
                    // No phone hint: a local delete, not a registry card (see the create card above).
                    <FileDeleteApproval
                      path={pendingDelete.path}
                      onConfirm={async () => {
                        await deleteFile(pendingDelete.path);
                        setPendingDelete(null);
                      }}
                      onCancel={() => setPendingDelete(null)}
                    />
                  )}

                  {/* Komut ya da Unity işlemi onayı — metni `kind` seçiyor */}
                  {pendingCommand && pendingCommand.messageId === msg.id && (
                    <CommandApproval
                      command={pendingCommand.command}
                      kind={pendingCommand.kind}
                      riskReason={pendingCommand.riskReason}
                      riskDetail={pendingCommand.riskDetail}
                      phonePaired={phonePaired}
                      onConfirm={async () => {
                        // onApproveCommand kuyruktaki sıradaki onayı kendi gösterir → burada setPendingCommand(null) ÇAĞIRMA
                        const failure = await onApproveCommand(pendingCommand.gateId, true);
                        // Hata metnini `useChat.approveCommand`'ın KENDİSİ basıyor
                        // (useChat.ts:457) — burada `decisionToast` kullanmak aynı
                        // mesajı ikinci kez basardı. Toast'lar diziye EKLENİYOR
                        // (Toast.tsx:32), yani kullanıcı sarı "iletilemedi" ile yeşil
                        // "çalışıyor"u AYNI ANDA görüyordu; burada koşullanan tek şey
                        // teslimat iddiası.
                        // Metin ve tip aşağıdaki -999 kardeşiyle AYNI olmak zorunda:
                        // ikisi de "onay iletildi"yi raporluyor, komutun çalıştığını
                        // değil (bkz. decisionToast'ın gerekçesi). 2026-07-29'da bu
                        // iki satırdan biri düzeltilip diğeri unutulursa aynı sınıf
                        // yarım kapanmış olur.
                        if (!failure) showToast(t('mcp.sentCommand'), 'info');
                      }}
                      onCancel={async () => {
                        const failure = await onApproveCommand(pendingCommand.gateId, false);
                        if (!failure) showToast(t('mcp.commandCancelled'), 'info');
                      }}
                    />
                  )}

                  {/* AskUserQuestion (A/B/C seçim kartı) */}
                  {pendingQuestion && pendingQuestion.messageId === msg.id && (
                    <QuestionApproval
                      questions={pendingQuestion.questions}
                      phonePaired={phonePaired}
                      onSubmit={async (answers) => {
                        // onAnswerQuestion kuyruktaki sıradaki soruyu kendi gösterir
                        await onAnswerQuestion(pendingQuestion.gateId, answers);
                      }}
                    />
                  )}

                  {/* Diff Viewer */}
                  {pendingFix?.messageId === msg.id && pendingFixCard}
                </div>
              </article>
            ) : (
              // The user's message: "YOU · time" over a raised bubble (mockup `.msg-user`).
              <article className="msg msg-user" data-role="user">
                <div className="msg-who">
                  {t('msg.you')}
                  {clockOf(msg.timestamp) && <time>{clockOf(msg.timestamp)}</time>}
                </div>
                {msg.source === 'phone' && (
                  <div data-testid="phone-marker"
                    title={t('chat.fromPhoneTitle', { cihaz: stripBidi(msg.sourceDevice || '') || t('chat.phoneUnnamed') })}
                    className="msg-phone">
                    📱 {stripBidi(msg.sourceDevice || '') || t('chat.phoneUnnamed')}
                  </div>
                )}
                <div className="msg-body">
                  {msg.images && msg.images.length > 0 && (
                    <div className="msg-images">
                      {msg.images.map((img, i) => (
                        <img
                          key={i}
                          src={img}
                          alt="user upload"
                          onClick={() => setLightboxSrc(img)}
                        />
                      ))}
                    </div>
                  )}
                  <div className="msg-user-text">
                    {/* Kullanıcının KENDİ mesajı da markdown'dan geçiyor, yani
                        oraya yazdığı bir yol da link olabiliyor. `onOpenFile`
                        burada da geçiliyor: aksi halde link ölü kalırdı ve
                        "bazı linkler açılıyor, bazıları hiçbir şey yapmıyor"
                        diye açıklaması olmayan bir davranış doğardı. */}
                    <MarkdownRenderer content={msg.content} onOpenFile={openFile} mentionTitles={mentionTitles} />
                  </div>
                </div>
              </article>
            )}
          </React.Fragment>
          );
        })}

        {/* Canlı aktivite şeridi: metin akmaya başladıktan sonra da Claude'un çalıştığı
            görünür kalsın (typing bubble yalnızca içerik boşken görünüyor). */}
        {loading && activity && messages.length > 0 && !!messages[messages.length - 1]?.content && (
          <Working
            text={activity.detail}
            tokens={fmtTok(activity.tokens) ? `${fmtTok(activity.tokens)} token` : null}
            elapsed={fmtElapsed(elapsedSec)}
          />
        )}

        {/* MCP onay kartları — mesaj listesinin DIŞINDA, tek bileşende.
            Kartların markup'ı `McpApprovalCards`'ta duruyor çünkü aynı kartlar
            workspace seçilmemişken de (ChatPanel hiç mount olmadan) çizilmek
            zorunda: köprü ürünün görünüm durumundan bağımsız çalışıyor ve o
            durumda istek 180 sn sonra sessizce reddediliyordu. */}
        <McpApprovalCards
          gate={mcpGate}
          workspaceMismatch={mcpWorkspaceMismatch}
          workspaceCheckPending={mcpWorkspaceCheckPending}
          openWorkspacePath={mcpOpenWorkspacePath}
          onResolved={onMcpResolved}
          apiBase={apiBase}
          sessionToken={user?.sessionToken ?? ''}
          showToast={showToast}
          refreshFileTree={refreshFileTree}
          pendingGenFiles={pendingGenFiles}
          setPendingGenFiles={setPendingGenFiles}
          pendingDelete={pendingDelete}
          setPendingDelete={setPendingDelete}
          pendingCommand={pendingCommand}
          setPendingCommand={setPendingCommand}
          pendingFix={pendingFix}
          setPendingFix={setPendingFix}
          setDiffFile={setDiffFile}
          onOpenFile={openFile}
          setCode={setCode}
          phonePaired={phonePaired}
        />

        <div ref={messagesEndRef} className="thread-end" aria-hidden="true" />

      {/* Resim lightbox (uygulama içi tam ekran önizleme) */}
      {lightboxSrc && (
        <div
          className="fixed inset-0 z-[300] bg-black/90 backdrop-blur-sm flex items-center justify-center cursor-zoom-out"
          onClick={() => setLightboxSrc(null)}
        >
          <motion.img
            initial={{ scale: 0.92, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.15 }}
            src={lightboxSrc}
            alt="preview"
            className="max-w-[92vw] max-h-[92vh] rounded-xl shadow-2xl object-contain"
            onClick={e => e.stopPropagation()}
          />
          <button
            onClick={() => setLightboxSrc(null)}
            className="absolute top-4 right-4 h-9 w-9 rounded-full bg-white/10 hover:bg-white/20 text-white text-lg leading-none flex items-center justify-center transition-colors"
            title={t('chat.closeEsc')}
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
};
