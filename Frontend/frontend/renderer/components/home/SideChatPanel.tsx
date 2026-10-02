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
      className={`p-1 rounded transition-all shrink-0 hover:bg-[color:var(--paper-sunk)] ${active ? 'text-[color:var(--focus)] bg-[color:color-mix(in_srgb,var(--focus)_14%,transparent)]' : 'text-[color:var(--ink-faint)] hover:text-[color:var(--ink-dim)]'}`}
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
      className="absolute left-3 right-3 bottom-3 z-30 flex flex-col max-h-[75%] min-h-[240px] rounded-xl border border-[color:var(--paper-line)] bg-[color:var(--paper-raised)] shadow-2xl"
    >
      <div className="h-10 px-3 flex items-center justify-between border-b border-[color:var(--paper-line)] shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <MessageCircleQuestion size={13} className="text-[color:var(--focus)] shrink-0" />
          <span className="text-[12px] font-bold text-[color:var(--ink-dim)] uppercase tracking-widest truncate">{t('side.title')}</span>
        </div>
        <button
          type="button"
          data-testid="side-close"
          onClick={onClose}
          title={t('side.close')}
          aria-label={t('side.close')}
          className="p-1 hover:bg-[color:var(--paper-sunk)] rounded transition-all text-[color:var(--ink-faint)] hover:text-[color:var(--ink-dim)]"
        >
          <X size={14} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar px-3 py-2 space-y-2 min-h-0" onScroll={scroll.onScroll}>
        {messages.length === 0 && (
          <p className="text-[12px] text-[color:var(--ink-faint)] leading-relaxed">{t('side.empty')}</p>
        )}
        {messages.map(m => m.role === 'user' ? (
          <div key={m.id} data-testid="side-question" className="ml-8 px-3 py-2 rounded-lg bg-[color:color-mix(in_srgb,var(--focus)_14%,transparent)] border border-[color:var(--paper-line-strong)] text-[12px] text-[color:var(--ink)] whitespace-pre-wrap break-words">
            {m.content}
          </div>
        ) : (
          <div key={m.id} data-testid="side-answer" className="px-3 py-2 rounded-lg bg-[color:var(--paper-sunk)] border border-[color:var(--paper-line)] text-[12px] text-[color:var(--ink-dim)] break-words">
            {m.content && <MarkdownRenderer content={m.content} />}
            {!m.content && !(m.activity && !m.finished && !m.failed) && (
              <span className="text-[color:var(--ink-faint)] animate-pulse">{t('side.thinking')}</span>
            )}
            {m.activity && !m.finished && !m.failed && (
              <div data-testid="side-activity" className={`text-[12px] text-[color:var(--ink-faint)] animate-pulse truncate ${m.content ? 'mt-1.5' : ''}`}>
                {m.activity.kind === 'reading' ? t('side.activityReading') : t('side.activityThinking')}
                {m.activity.detail ? ` — ${m.activity.detail.slice(0, 60)}` : ''}
              </div>
            )}
            {m.finished && !m.failed && m.content.trim() && (
              <button
                type="button"
                data-testid="side-add-to-main"
                onClick={() => onAddToMain(m.question || '', m.content)}
                className="mt-2 flex items-center gap-1 px-2 py-1 rounded-md border border-[color:var(--paper-line)] bg-[color:var(--paper-sunk)] hover:bg-[color:var(--paper-sunk)] text-[12px] font-semibold text-[color:var(--ink-dim)] hover:text-[color:var(--ink)] transition-colors"
              >
                <Plus size={11} />
                {t('side.addToMain')}
              </button>
            )}
          </div>
        ))}
        <div ref={scroll.endRef} />
      </div>

      <div className="p-2 border-t border-[color:var(--paper-line)] flex items-end gap-2 shrink-0">
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
          className="flex-1 resize-none rounded-lg bg-[color:var(--paper-sunk)] border border-[color:var(--paper-line)] focus:border-[color:var(--focus)] outline-none px-2.5 py-1.5 text-[12px] text-[color:var(--ink)] placeholder:text-[color:var(--ink-faint)]"
        />
        {loading ? (
          <button
            type="button"
            data-testid="side-stop"
            onClick={onStop}
            title={t('side.stop')}
            aria-label={t('side.stop')}
            className="p-2 rounded-lg bg-[color:var(--accent-soft)] hover:bg-[color:var(--accent-soft)] text-[color:var(--del-text)] border border-[color:var(--paper-line-strong)] transition-colors"
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
            className="p-2 rounded-lg bg-[color:color-mix(in_srgb,var(--focus)_14%,transparent)] hover:bg-[color:color-mix(in_srgb,var(--focus)_24%,transparent)] text-[color:var(--focus)] border border-[color:var(--paper-line-strong)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <CornerDownLeft size={13} />
          </button>
        )}
      </div>
    </div>
  );
};
