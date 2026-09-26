import React, { useEffect, useRef, useState } from 'react';
import { CornerDownLeft, MessageCircleQuestion, Plus, Square, X } from 'lucide-react';
import { useLang } from '../../lib/i18n';
import { MarkdownRenderer } from './MarkdownRenderer';
import { useAutoScroll } from '../../hooks/home/useAutoScroll';
import type { SideMessage } from '../../hooks/home/useSideChat';

interface SideQuestionButtonProps {
  convId: number | null;
  active: boolean;
  onOpen: (convId: number) => void;
}

/**
 * Opens the side question panel. Not gated on the branch rules: asking while
 * the main chat's turn runs is the point.
 */
export const SideQuestionButton: React.FC<SideQuestionButtonProps> = ({ convId, active, onOpen }) => {
  const { t } = useLang();
  if (convId == null) return null;
  return (
    <button
      type="button"
      data-testid="side-open"
      title={t('side.openTitle')}
      aria-label={t('side.open')}
      aria-pressed={active}
      onClick={() => onOpen(convId)}
      className={`p-1 rounded transition-all shrink-0 hover:bg-white/[0.06] ${active ? 'text-blue-400 bg-blue-500/10' : 'text-slate-500 hover:text-slate-300'}`}
    >
      <MessageCircleQuestion size={14} />
    </button>
  );
};

interface SideChatPanelProps {
  messages: SideMessage[];
  loading: boolean;
  onAsk: (question: string) => void;
  onStop: () => void;
  onClose: () => void;
  onAddToMain: (question: string, answer: string) => void;
}

export const SideChatPanel: React.FC<SideChatPanelProps> = ({
  messages, loading, onAsk, onStop, onClose, onAddToMain,
}) => {
  const { t } = useLang();
  const [draft, setDraft] = useState('');
  const scroll = useAutoScroll();
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => { scroll.followIfPinned(); }, [messages, scroll.followIfPinned]);

  const send = () => {
    const q = draft.trim();
    if (!q || loading) return;
    onAsk(q);
    setDraft('');
    scroll.scrollToBottom();
  };

  return (
    <div
      data-testid="side-panel"
      role="dialog"
      aria-label={t('side.title')}
      className="absolute left-3 right-3 bottom-3 z-30 flex flex-col max-h-[75%] min-h-[240px] rounded-xl border border-white/[0.08] bg-[#0F1218]/95 backdrop-blur shadow-2xl shadow-black/50"
    >
      <div className="h-10 px-3 flex items-center justify-between border-b border-white/[0.06] shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <MessageCircleQuestion size={13} className="text-blue-400 shrink-0" />
          <span className="text-[11px] font-bold text-slate-400 uppercase tracking-widest truncate">{t('side.title')}</span>
        </div>
        <button
          type="button"
          data-testid="side-close"
          onClick={onClose}
          title={t('side.close')}
          aria-label={t('side.close')}
          className="p-1 hover:bg-white/[0.06] rounded transition-all text-slate-500 hover:text-slate-300"
        >
          <X size={14} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar px-3 py-2 space-y-2 min-h-0" onScroll={scroll.onScroll}>
        {messages.length === 0 && (
          <p className="text-[11.5px] text-slate-500 leading-relaxed">{t('side.empty')}</p>
        )}
        {messages.map(m => m.role === 'user' ? (
          <div key={m.id} data-testid="side-question" className="ml-8 px-3 py-2 rounded-lg bg-blue-600/15 border border-blue-500/20 text-[12px] text-slate-200 whitespace-pre-wrap break-words">
            {m.content}
          </div>
        ) : (
          <div key={m.id} data-testid="side-answer" className="px-3 py-2 rounded-lg bg-white/[0.03] border border-white/[0.06] text-[12px] text-slate-300 break-words">
            {m.content
              ? <MarkdownRenderer content={m.content} />
              : <span className="text-slate-500 animate-pulse">{t('side.thinking')}</span>}
            {m.finished && !m.failed && m.content.trim() && (
              <button
                type="button"
                data-testid="side-add-to-main"
                onClick={() => onAddToMain(m.question || '', m.content)}
                className="mt-2 flex items-center gap-1 px-2 py-1 rounded-md border border-white/[0.08] bg-white/[0.03] hover:bg-white/[0.07] text-[10.5px] font-semibold text-slate-400 hover:text-slate-200 transition-colors"
              >
                <Plus size={11} />
                {t('side.addToMain')}
              </button>
            )}
          </div>
        ))}
        <div ref={scroll.endRef} />
      </div>

      <div className="p-2 border-t border-white/[0.06] flex items-end gap-2 shrink-0">
        <textarea
          ref={inputRef}
          data-testid="side-input"
          value={draft}
          rows={2}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
            if (e.key === 'Escape') { e.preventDefault(); onClose(); }
          }}
          placeholder={t('side.placeholder')}
          className="flex-1 resize-none rounded-lg bg-white/[0.03] border border-white/[0.08] focus:border-blue-500/40 outline-none px-2.5 py-1.5 text-[12px] text-slate-200 placeholder:text-slate-600"
        />
        {loading ? (
          <button
            type="button"
            data-testid="side-stop"
            onClick={onStop}
            title={t('side.stop')}
            aria-label={t('side.stop')}
            className="p-2 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 transition-colors"
          >
            <Square size={13} />
          </button>
        ) : (
          <button
            type="button"
            data-testid="side-send"
            onClick={send}
            disabled={!draft.trim()}
            title={t('side.send')}
            aria-label={t('side.send')}
            className="p-2 rounded-lg bg-blue-600/15 hover:bg-blue-600/25 text-blue-400 border border-blue-500/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <CornerDownLeft size={13} />
          </button>
        )}
      </div>
    </div>
  );
};
