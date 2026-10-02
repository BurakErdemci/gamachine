// EN ÜSTTE — gerekçe için bkz. monaco-loader.ts ve EditorPanel.tsx.
import './monaco-loader';
import { DiffEditor } from '@monaco-editor/react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, Check, CheckCircle2, FileCode, X } from 'lucide-react';
import { defineUnityTheme, THEME_NAME } from './monaco-theme';
import { useLang } from '../../lib/i18n';
import { useEffect, useId, useRef } from 'react';
import { publishPendingChange } from '../../lib/pendingChange';

export interface DiffData {
  original_code: string;
  fixed_code: string;
  explanation: string;
  editor_hint?: string | null;
}

interface DiffViewerProps {
  diffData: DiffData;
  filename?: string;
  applied?: boolean;
  onAccept: (fixedCode: string) => void;
  onReject: () => void;
  /** The phone can decide this diff: true only for the unityMCP gate's diff (a registry card);
   *  the chat flow's own diff writes through IPC here and no phone can answer it. */
  phonePaired?: boolean;
}

export const DiffViewer = ({ diffData, filename, applied, onAccept, onReject, phonePaired }: DiffViewerProps) => {
  const { t } = useLang();
  // While undecided, the workspace's Kod tab shows this diff with an Accept / Reject strip that
  // calls these same two handlers (lib/pendingChange.ts); deciding there is deciding here.
  const id = useId();
  const handlers = useRef({ onAccept, onReject });
  handlers.current = { onAccept, onReject };
  useEffect(() => {
    if (applied) return;
    return publishPendingChange({
      id: `fix:${id}`,
      name: filename || t('diff.fileFallback'),
      original: diffData.original_code,
      modified: diffData.fixed_code,
      accept: () => handlers.current.onAccept(diffData.fixed_code),
      reject: () => handlers.current.onReject(),
    });
  }, [applied, diffData, filename, id, t]);
  // The fix card on the chat's code plate (thread.css `.code`, `--code-*`): head with the file and
  // the old / new legend, the explanation and the Unity hint as lines under it, Monaco's diff, then
  // the decision row with the shared `.btn`s. Applied, it folds to one record line.
  return (
  <AnimatePresence mode="wait">
    {applied ? (
      <motion.div
        key="applied"
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        className="diffcard-done"
      >
        <CheckCircle2 size={15} aria-hidden="true" />
        <div>
          <p className="diffcard-done-k">{t('diff.updated', { ad: filename || t('diff.fileFallback') })}</p>
          <p className="diffcard-done-s">{diffData.explanation}</p>
        </div>
      </motion.div>
    ) : (
      <motion.div
        key="diff"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 6 }}
        transition={{ duration: 0.18 }}
        className="code diffcard"
      >
        <div className="code-head">
          <FileCode size={14} aria-hidden="true" />
          <span className="code-path"><b>{filename || t('diff.suggestionTitle')}</b></span>
          <span className="code-diff">patch</span>
          <span className="diffcard-legend"><i className="is-old" aria-hidden="true" />{t('diff.old')}</span>
          <span className="diffcard-legend"><i className="is-new" aria-hidden="true" />{t('diff.new')}</span>
        </div>

        <p className="diffcard-why">
          <b>{t('diff.badge')}</b>
          {diffData.explanation}
        </p>

        {diffData.editor_hint && (
          <p className="diffcard-why is-hint">
            <AlertTriangle size={13} aria-hidden="true" />
            <span><b>Unity Editor · </b>{diffData.editor_hint}</span>
          </p>
        )}

        <div className="diffcard-ed">
          <DiffEditor
            height="100%"
            language="csharp"
            original={diffData.original_code}
            modified={diffData.fixed_code}
            theme={THEME_NAME}
            beforeMount={defineUnityTheme}
            options={{
              readOnly: true,
              renderSideBySide: true,
              minimap: { enabled: false },
              fontSize: 12,
              fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
              lineHeight: 1.6,
              scrollBeyondLastLine: false,
              padding: { top: 14, bottom: 14 },
              diffWordWrap: 'off',
              ignoreTrimWhitespace: true,
              scrollbar: { verticalScrollbarSize: 4, horizontalScrollbarSize: 4 },
            }}
          />
        </div>

        <div className="diffcard-acts">
          <button type="button" onClick={() => onAccept(diffData.fixed_code)} className="btn btn-primary">
            <Check size={13} strokeWidth={2.5} aria-hidden="true" />
            {t('diff.accept')}
          </button>
          <button type="button" onClick={onReject} className="btn btn-ghost">
            <X size={13} strokeWidth={2.5} aria-hidden="true" />
            {t('diff.reject')}
          </button>
          <span className="diffcard-hint">{t('diff.acceptHint')}</span>
          {phonePaired && <span className="approval-hint" data-testid="diff-phone-hint">{t('card.phoneHint')}</span>}
        </div>
      </motion.div>
    )}
  </AnimatePresence>
  );
};
