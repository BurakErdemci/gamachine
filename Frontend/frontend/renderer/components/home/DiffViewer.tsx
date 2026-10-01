// EN ÜSTTE — gerekçe için bkz. monaco-loader.ts ve EditorPanel.tsx.
import './monaco-loader';
import { DiffEditor } from '@monaco-editor/react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, Check, CheckCircle2, FileCode, X } from 'lucide-react';
import { defineUnityTheme, THEME_NAME } from './monaco-theme';
import { useLang } from '../../lib/i18n';

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
  return (
  <AnimatePresence mode="wait">
    {applied ? (
      <motion.div
        key="applied"
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-xl border border-emerald-800/40 bg-emerald-950/10 px-4 py-3 mt-3 flex items-center gap-3"
      >
        <div className="p-1.5 rounded-lg bg-emerald-500/10">
          <CheckCircle2 size={14} className="text-emerald-400" />
        </div>
        <div>
          <p className="text-[12px] font-semibold text-emerald-300 leading-none">{t('diff.updated', { ad: filename || t('diff.fileFallback') })}</p>
          <p className="text-[11px] text-slate-500 mt-1">{diffData.explanation}</p>
        </div>
      </motion.div>
    ) : (
      <motion.div
        key="diff"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 6 }}
        transition={{ duration: 0.18 }}
        className="mt-3 rounded-2xl overflow-hidden border border-white/[0.06] bg-[#0c0c0e] shadow-2xl"
        style={{ boxShadow: '0 0 0 1px rgba(255,255,255,0.04), 0 24px 48px rgba(0,0,0,0.5)' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 bg-[#111114] border-b border-white/[0.05]">
          <div className="flex items-center gap-2.5">
            <div className="p-1 rounded-md bg-blue-500/10">
              <FileCode size={13} className="text-blue-400" />
            </div>
            <span className="text-[13px] font-semibold text-slate-200 tracking-tight">
              {filename || t('diff.suggestionTitle')}
            </span>
            <span className="text-[10px] text-slate-600 font-mono">patch</span>
          </div>
          <div className="flex items-center gap-3 text-[10px] font-mono">
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-red-500/60" />
              <span className="text-slate-600">{t('diff.old')}</span>
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-emerald-500/60" />
              <span className="text-slate-600">{t('diff.new')}</span>
            </span>
          </div>
        </div>

        {/* Explanation */}
        <div className="px-4 py-3 border-b border-white/[0.04] flex items-start gap-3 bg-[#0e0e11]">
          <div className="w-1 self-stretch rounded-full bg-amber-500/40 shrink-0" />
          <p className="text-[12px] text-slate-300 leading-relaxed">
            <span className="font-semibold text-amber-400/90">{t('diff.badge')}</span>
            {diffData.explanation}
          </p>
        </div>

        {/* Unity Editor hint */}
        {diffData.editor_hint && (
          <div className="px-4 py-3 border-b border-white/[0.04] bg-amber-950/20 flex items-start gap-3">
            <div className="p-1 rounded-md bg-amber-500/10 shrink-0">
              <AlertTriangle size={12} className="text-amber-400" />
            </div>
            <p className="text-[12px] text-amber-200/70 leading-relaxed">
              <span className="font-semibold text-amber-400/90">Unity Editor · </span>
              {diffData.editor_hint}
            </p>
          </div>
        )}

        {/* Monaco Diff Editor */}
        <div className="h-[300px]">
          <DiffEditor
            height="100%"
            language="csharp"
            original={diffData.original_code}
            modified={diffData.fixed_code}
            theme={THEME_NAME}
            onMount={(_, monaco) => defineUnityTheme(monaco)}
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

        {/* Action bar */}
        <div className="flex items-center gap-2 px-4 py-3 bg-[#111114] border-t border-white/[0.05]">
          <button
            onClick={() => onAccept(diffData.fixed_code)}
            className="group flex items-center gap-2 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-[12px] font-semibold transition-all duration-150 shadow-lg shadow-emerald-900/30"
          >
            <Check size={13} strokeWidth={2.5} />
            {t('diff.accept')}
          </button>
          <button
            onClick={onReject}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/[0.07] text-slate-400 hover:text-slate-300 text-[12px] font-semibold transition-all duration-150"
          >
            <X size={13} strokeWidth={2.5} />
            {t('diff.reject')}
          </button>
          <span className="text-[10px] text-slate-700 ml-auto tracking-wide">
            {t('diff.acceptHint')}
          </span>
          {phonePaired && <span className="approval-hint" data-testid="diff-phone-hint">{t('card.phoneHint')}</span>}
        </div>
      </motion.div>
    )}
  </AnimatePresence>
  );
};
