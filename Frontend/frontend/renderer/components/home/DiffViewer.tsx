// EN ÜSTTE — gerekçe için bkz. monaco-loader.ts ve EditorPanel.tsx.
import './monaco-loader';
import { DiffEditor } from '@monaco-editor/react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, Check, CheckCircle2, FileCode, X } from 'lucide-react';
import { defineUnityTheme, THEME_NAME, codeFontFamily, watchEditorFont } from './monaco-theme';
import { useLang } from '../../lib/i18n';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
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
  filePath?: string | null;
  applied?: boolean;
  onAccept: (fixedCode: string) => void | Promise<void>;
  onReject: () => void;
  /** The phone can decide this diff: true only for the unityMCP gate's diff (a registry card);
   *  the chat flow's own diff writes through IPC here and no phone can answer it. */
  phonePaired?: boolean;
}

export const DiffViewer = ({ diffData, filename, filePath, applied, onAccept, onReject, phonePaired }: DiffViewerProps) => {
  const { t } = useLang();
  // While undecided, the workspace's Kod tab shows this diff with an Accept / Reject strip that
  // calls these same two handlers (lib/pendingChange.ts); deciding there is deciding here.
  const id = useId();
  const handlers = useRef({ onAccept, onReject });
  handlers.current = { onAccept, onReject };
  const accepting = useRef(false);
  const [busy, setBusy] = useState(false);
  const stopFontWatch = useRef<(() => void) | null>(null);
  const withdraw = useRef<(() => void) | null>(null);
  useEffect(() => () => stopFontWatch.current?.(), []);
  const accept = useCallback(async () => {
    // The strip and card can call before React has painted the disabled state.
    if (accepting.current) return;
    accepting.current = true;
    setBusy(true);
    try {
      await handlers.current.onAccept(diffData.fixed_code);
    } finally {
      accepting.current = false;
      setBusy(false);
    }
  }, [diffData]);
  const reject = useCallback(() => {
    // The owning card reports a conflicting decision while acceptance is in flight.
    handlers.current.onReject();
  }, []);
  useEffect(() => {
    if (applied) return;
    withdraw.current = publishPendingChange({
      id: `fix:${id}`,
      name: filename || t('diff.fileFallback'),
      path: filePath ?? undefined,
      original: diffData.original_code,
      modified: diffData.fixed_code,
      accept,
      reject,
      busy,
    });
  }, [applied, diffData, filename, filePath, id, t, accept, reject, busy]);
  // Busy updates replace the entry without withdrawing its position.
  useEffect(() => () => withdraw.current?.(), [applied, id]);
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
            onMount={(editor) => {
              stopFontWatch.current?.();
              stopFontWatch.current = watchEditorFont(editor);
            }}
            options={{
              readOnly: true,
              renderSideBySide: true,
              minimap: { enabled: false },
              fontSize: 12,
              fontFamily: codeFontFamily(),
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
          <button type="button" onClick={accept} disabled={busy} className="btn btn-primary">
            <Check size={13} strokeWidth={2.5} aria-hidden="true" />
            {t('diff.accept')}
          </button>
          <button type="button" onClick={reject} className="btn btn-ghost">
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
