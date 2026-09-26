import React, { useState, useEffect, useRef } from 'react';
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
  RefreshCw
} from 'lucide-react';
import { Message, UserData, FileEntry, GenerationMode, ChatActivity } from './types';
import { ModelAvatar } from './ModelAvatar';
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

interface ChatPanelProps {
  messages: Message[];
  activeConvId: number | null;
  user: UserData | null;
  loading: boolean;
  clearHistory: () => void;
  lang: string;
  effectiveProvider: string;
  modelName?: string;
  thinkingLevel: 'auto' | 'off' | 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
  workspacePath: string | null;
  handleExportToUnity: (code: string) => void;
  pendingGenFiles: { files: PendingFile[]; messageId: number } | null;
  setPendingGenFiles: (val: any) => void;
  pendingFix: { data: DiffData; messageId?: number; applied?: boolean; gateId?: string } | null;
  setPendingFix: (val: any) => void;
  openedFilePath: string | null;
  setCode: (code: string) => void;
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
  pendingCommand: { command: string; gateId: string; messageId: number; kind?: 'shell' | 'unity' } | null;
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
}

export const ChatPanel: React.FC<ChatPanelProps> = ({
  messages,
  activeConvId,
  user,
  loading,
  clearHistory,
  effectiveProvider,
  modelName,
  thinkingLevel,
  workspacePath,
  handleExportToUnity,
  pendingGenFiles,
  setPendingGenFiles,
  pendingFix,
  setPendingFix,
  openedFilePath,
  setCode,
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
}) => {
  const { t } = useLang();

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

  // Mesaj meta satırı için insan-okur model/sağlayıcı adı.
  // Mesaj aktif sağlayıcıdan geldiyse tam model adını göster; geçmiş
  // mesajlarda (farklı sağlayıcı) marka adına düş.
  const PROVIDER_LABELS: Record<string, string> = {
    claude: 'Claude', anthropic: 'Claude', openai: 'GPT', codex: 'Codex',
    gemini: 'Gemini', agy: 'Antigravity', copilot: 'Copilot', cursor: 'Cursor',
    opencode: 'OpenCode', nvidia: 'NVIDIA', groq: 'Groq', ollama: 'Ollama',
  };
  // Sanitised because this is a LABEL, not the value anything acts on: the
  // model id it returns comes from a provider's catalogue, and a directional
  // override in it would reorder the name the user reads while the stored id
  // stays what it is. The settings field that lets the user EDIT that id is
  // deliberately left raw — showing a cleaned string in an editable box would
  // save a different value than the one displayed. The id itself is refused
  // upstream instead (`model_catalog.usable_model_id`).
  const metaName = (msgProvider?: string) => {
    const p = (msgProvider || effectiveProvider || '').toLowerCase();
    if ((!msgProvider || msgProvider === effectiveProvider) && modelName) return stripBidi(modelName);
    return PROVIDER_LABELS[p] || (p ? p.charAt(0).toUpperCase() + p.slice(1) : 'AI');
  };

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

  if (!activeConvId && !hasMcpCard) {
    return (
      <div className="flex flex-col h-full">
        <div className="flex-1 flex flex-col items-center justify-center text-slate-600 gap-3">
          <Bot size={32} className="opacity-20" />
          <p className="text-[11px] text-center">
            {t('chat.empty').split('\n').map((line, i) => <span key={i}>{line}{i === 0 && <br />}</span>)}
          </p>
        </div>
      </div>
    );
  }

  if (messages.length === 0 && !loading && !hasMcpCard) {
    return (
      <div className="flex flex-col h-full">
        <div className="flex-1 flex items-center justify-center">
          <Bot size={20} className="opacity-10 text-blue-500" />
        </div>
      </div>
    );
  }

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
  const pendingFixCard = pendingFix ? (
    <DiffViewer
      diffData={pendingFix.data}
      filename={pendingFix.data?.editor_hint?.split('/').pop() || (openedFilePath ? openedFilePath.split('/').pop() : undefined)}
      applied={pendingFix.applied}
      onAccept={async (fixedCode) => {
        // Editör tamponu her hâlükârda güncellenir
        // (kullanıcı düzeltmeyi görsün), ama "dosya güncellendi"
        // iddiası yalnız disk yazımı DOĞRULANDIYSA basılır.
        setCode(fixedCode);
        if (!ipc || !openedFilePath || !workspacePath) {
          // Hiçbir yazım denenmedi: diskte değişen bir şey yok.
          // Eskiden burada da "✅ Dosya güncellendi" basılıyordu.
          setPendingFix((prev: any) => prev ? { ...prev, applied: true } : null);
          showToast(t('chat.diffApplied'), 'info');
          return;
        }
        const res = await ipc.invoke('write-file', openedFilePath, fixedCode, workspacePath);
        if (!res?.success) {
          showToast(writeErrorText(openedFilePath.split('/').pop() || openedFilePath, res), 'error');
          return;
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
    <div className="flex-1 overflow-y-auto px-4 py-6 custom-scrollbar scroll-smooth">
      <div className="max-w-4xl mx-auto space-y-8">
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
              return wakeKey ? `${t(wakeKey)} — ${detail}` : part;
            }).join(' · ');
          }
          return (
          <div key={msg.id} className={`chat-message-enter ${msg.role === 'user' ? 'flex justify-end' : ''}`}>
            {msg.role === 'system' ? (
              /* AUTO-WAKE row. This branch is MANDATORY: the ternary below only
                 distinguished assistant/other, so a `system` role would render
                 as a BLUE USER BUBBLE — a sentence the user never wrote would
                 look like theirs. The event row is deliberately muted: not a
                 message, but a system marker saying the chat continued on its
                 own. */
              <div className="flex items-center gap-2 py-1 text-[11px] text-slate-500 select-none">
                <RefreshCw size={11} className="text-violet-400/70 shrink-0" />
                <span className="font-medium shrink-0">{t('chat.wakeRow')}</span>
                <span className="truncate text-slate-600">· {wakeText}</span>
              </div>
            ) : msg.role === 'assistant' ? (
              // Avatar yan sütun yerine ÜSTTE tek meta satırı — dar panelde
              // içerik tam genişlik akar, model/süre/token bilgisi tek bakışta.
              <div className="max-w-full group">
                <div className="flex items-center gap-2 mb-2 select-none">
                  <ModelAvatar provider={msg.provider || effectiveProvider} size={14} />
                  <span className="text-[11px] font-medium text-slate-400 truncate">{metaName(msg.provider)}</span>
                  {msg.usage && (msg.usage.duration_ms || msg.usage.output_tokens) ? (
                    <span className="text-[10.5px] text-slate-600 tabular-nums shrink-0">
                      {msg.usage.duration_ms ? `· ${Math.max(1, Math.round(msg.usage.duration_ms / 1000))}sn` : null}
                      {fmtTok(msg.usage.output_tokens) ? ` · ↓${fmtTok(msg.usage.output_tokens)}` : null}
                    </span>
                  ) : null}
                </div>
                <div className="min-w-0">
                  {/* Statik Bulgular */}
                  {msg.smells && msg.smells.length > 0 && (
                    <div className="mb-3 bg-[#000000] rounded-lg border border-orange-500/20 p-3">
                      <div className="flex items-center gap-1.5 mb-2">
                        <AlertTriangle size={12} className="text-orange-400" />
                        <span className="text-[10px] font-semibold text-orange-400 uppercase tracking-wider">Static Analysis</span>
                      </div>
                      <div className="space-y-1.5">
                        {msg.smells.map((s: any, i: number) => (
                          <div key={i} className="text-[11px] text-slate-400 flex items-start gap-2">
                            <span className="bg-orange-500/10 text-orange-500 px-1.5 py-0.5 rounded text-[9px] font-bold shrink-0">
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
                  <ToolGroup tools={msg.tool_calls} />
                  <ToolGroup tools={msg.tools} />

                  {/* Content or Loading Typing */}
                  {(msg.content === "" || !msg.content) && loading && msgIdx === messages.length - 1 ? (
                    // Modern "düşünüyor": kutu yok — ışıltısı kayan metin + canlı sayaç.
                    // (Sayaç ilerliyorsa süreç KESİN canlı; "dondu mu?" sorusunun cevabı.)
                    <div className="inline-flex items-center gap-2 py-0.5 max-w-full">
                      <Sparkles size={13} className="text-violet-400/80 animate-pulse shrink-0" />
                      <span className="text-[12.5px] font-medium shimmer-text truncate">
                        {activity?.detail || t('chat.thinking')}
                      </span>
                      {activity && fmtTok(activity.tokens) && (
                        <span className="text-[11px] text-slate-600 shrink-0">· {fmtTok(activity.tokens)}</span>
                      )}
                      <span className="text-[11px] text-slate-600 tabular-nums shrink-0">· {fmtElapsed(elapsedSec)}</span>
                    </div>
                  ) : slashCmd ? (
                    <SlashCommandCard command={slashCmd} text={msg.content} workspacePath={workspacePath} onOpenFile={openFile} />
                  ) : (
                    <div className="chat-prose max-w-none">
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
                    <div className="flex gap-2 mt-3">
                      <button onClick={() => sendMessage(t('chat.generateFull'))} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600/20 border border-blue-500/30 text-blue-300 text-[12px] font-medium hover:bg-blue-600/35 transition-colors"> ✅ {t('chat.generateFull')} </button>
                      <button onClick={() => sendMessage(t('chat.simpleVersion'))} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-700/40 border border-slate-600/30 text-slate-300 text-[12px] font-medium hover:bg-slate-700/60 transition-colors"> ⚡ {t('chat.simpleVersion')} </button>
                    </div>
                  )}

                  {/* File Creation Approval */}
                  {pendingGenFiles && pendingGenFiles.messageId === msg.id && (
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
                      onSubmit={async (answers) => {
                        // onAnswerQuestion kuyruktaki sıradaki soruyu kendi gösterir
                        await onAnswerQuestion(pendingQuestion.gateId, answers);
                      }}
                    />
                  )}

                  {/* Diff Viewer */}
                  {pendingFix?.messageId === msg.id && pendingFixCard}
                </div>
              </div>
            ) : (
              // Kullanıcı Mesajı
              <div className="max-w-[85%]">
                <div className="bg-blue-500/10 border border-blue-400/15 rounded-2xl rounded-tr-md px-4 py-2.5">
                  {msg.images && msg.images.length > 0 && (
                    <div className="flex gap-2 mb-3 flex-wrap">
                      {msg.images.map((img, i) => (
                        <img 
                          key={i} 
                          src={img} 
                          alt="user upload" 
                          className="max-w-[200px] max-h-[200px] rounded-lg border border-white/10 shadow-lg cursor-zoom-in hover:scale-[1.02] transition-transform" 
                          onClick={() => setLightboxSrc(img)}
                        />
                      ))}
                    </div>
                  )}
                  <div className="text-[13px] text-slate-200 whitespace-pre-wrap break-words">
                    {/* Kullanıcının KENDİ mesajı da markdown'dan geçiyor, yani
                        oraya yazdığı bir yol da link olabiliyor. `onOpenFile`
                        burada da geçiliyor: aksi halde link ölü kalırdı ve
                        "bazı linkler açılıyor, bazıları hiçbir şey yapmıyor"
                        diye açıklaması olmayan bir davranış doğardı. */}
                    <MarkdownRenderer content={msg.content} onOpenFile={openFile} />
                  </div>
                </div>
              </div>
            )}
          </div>
          );
        })}

        {/* Canlı aktivite şeridi: metin akmaya başladıktan sonra da Claude'un çalıştığı
            görünür kalsın (typing bubble yalnızca içerik boşken görünüyor). */}
        {loading && activity && messages.length > 0 && !!messages[messages.length - 1]?.content && (
          <div className="flex items-center gap-2 mb-6 text-[11.5px]">
            <Sparkles size={12} className="text-violet-400/80 animate-pulse shrink-0" />
            <span className="shimmer-text font-medium truncate">{activity.detail}</span>
            {fmtTok(activity.tokens) && <span className="text-slate-600 shrink-0">· {fmtTok(activity.tokens)} token</span>}
            <span className="text-slate-600 tabular-nums shrink-0">· {fmtElapsed(elapsedSec)}</span>
          </div>
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
        />

        <div ref={messagesEndRef} className="h-4" />
      </div>

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
