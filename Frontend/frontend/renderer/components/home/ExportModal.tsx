import { AlertTriangle, CheckCircle2, FileCode, FileDown, Folder, X } from "lucide-react";
import { AnimatePresence, motion, useIsPresent } from "framer-motion";
import type { HTMLAttributes } from "react";

import { ExportModalState } from "./types";
import { useLang } from "../../lib/i18n";


/** The backdrop dialog. AnimatePresence keeps it through the card's exit animation; aria-modal
 *  goes once it is leaving, so the window's Ctrl+S guard does not swallow a save meanwhile. */
const ModalFrame = (props: HTMLAttributes<HTMLDivElement>) => {
  const present = useIsPresent();
  return <div role="dialog" aria-modal={present ? "true" : undefined} {...props} />;
};


interface ExportModalProps {
  exportModal: ExportModalState | null;
  exportFileName: string;
  workspacePath: string | null;
  onFileNameChange: (value: string) => void;
  onClose: () => void;
  onChangeExportDir: () => Promise<void>;
  onExportSingleFile: (fileName: string, content: string) => Promise<void>;
  onExportMultipleFiles: () => Promise<void>;
}


export const ExportModal = ({
  exportModal,
  exportFileName,
  workspacePath,
  onFileNameChange,
  onClose,
  onChangeExportDir,
  onExportSingleFile,
  onExportMultipleFiles,
}: ExportModalProps) => {
  const { t } = useLang();
  return (
  <AnimatePresence>
    {exportModal?.isOpen && (
      <ModalFrame className="fixed inset-0 flex items-center justify-center z-[100]" style={{ background: 'color-mix(in srgb, var(--shell-bg) 70%, transparent)' }} onClick={() => !exportModal.exportResult && onClose()}>
        <motion.div
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          className="bg-[color:var(--shell-bg)] border border-[color:var(--shell-line)] rounded-2xl p-6 max-w-lg w-full shadow-2xl mx-4"
          onClick={(e) => e.stopPropagation()}
        >
          {exportModal.exportResult ? (
            <div className="flex flex-col items-center gap-4 py-4">
              <div className={`p-4 rounded-2xl ${exportModal.exportResult.success ? 'bg-[color:var(--ok-soft)] border border-[color:var(--ok)]' : 'bg-[color:var(--accent-soft)] border border-[color:var(--accent)]'}`}>
                {exportModal.exportResult.success ? (
                  <CheckCircle2 size={40} className="text-[color:var(--ok-shell)]" />
                ) : (
                  <AlertTriangle size={40} className="text-[color:var(--del-text)]" />
                )}
              </div>
              <div className="text-center">
                <h3 className="text-base font-bold text-[color:var(--shell-text)] mb-1">
                  {exportModal.exportResult.success ? t('export.success') : t('export.error')}
                </h3>
                <p className="text-[13px] text-[color:var(--shell-text-dim)] whitespace-pre-line">
                  {exportModal.exportResult.message}
                </p>
                <p className="text-[12px] text-[color:var(--shell-text-dim)] mt-2">
                  📁 {exportModal.targetDir}
                </p>
              </div>
              <button
                onClick={onClose}
                className="mt-2 px-6 py-2.5 bg-[color:var(--accent)] hover:opacity-90 text-[color:var(--accent-ink)] rounded-xl font-bold text-xs tracking-wide transition-all"
              >
                {t('export.ok')}
              </button>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-5">
                <div className="flex items-center gap-2.5">
                  <div className="p-1.5 bg-[color:var(--ok-soft)] rounded-lg text-[color:var(--ok-shell)]">
                    <FileDown size={18} />
                  </div>
                  <div>
                    <h2 className="text-base font-bold text-[color:var(--shell-text)]">
                      {exportModal.multiFile ? t('export.multiTitle') : t('export.singleTitle')}
                    </h2>
                    <p className="text-[12px] text-[color:var(--shell-text-dim)] mt-0.5">
                      {exportModal.multiFile ? t('export.multiSubtitle', { sayi: exportModal.files.length }) : t('export.singleSubtitle')}
                    </p>
                  </div>
                </div>
                <button onClick={onClose} className="p-1.5 hover:bg-[color:var(--shell-bg-2)] rounded-lg transition-colors text-[color:var(--shell-text-dim)]">
                  <X size={18} />
                </button>
              </div>

              <div className="mb-4 p-3 bg-[color:var(--shell-bg-2)] rounded-xl border border-[color:var(--shell-line)] transition-colors">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2 text-[12px] text-[color:var(--shell-text-dim)] font-semibold uppercase tracking-wider">
                    <Folder size={11} /> {t('export.targetDir')}
                  </div>
                  <button
                    onClick={onChangeExportDir}
                    className="text-[12px] text-[color:var(--focus)] hover:opacity-80 font-semibold transition-colors px-2 py-0.5 rounded"
                  >
                    {t('export.change')}
                  </button>
                </div>
                <p className="text-[12px] text-[color:var(--shell-text-dim)] font-mono truncate">
                  {exportModal.targetDir}
                </p>
              </div>

              {exportModal.multiFile ? (
                <div className="space-y-3 mb-5">
                  <label className="block text-[12px] font-semibold text-[color:var(--shell-text-dim)] uppercase tracking-wider">
                    {t('export.filesToCreate')}
                  </label>
                  <div className="space-y-1.5 max-h-[240px] overflow-y-auto custom-scrollbar">
                    {exportModal.files.map((file, idx) => (
                      <div key={idx} className="flex items-center gap-2.5 p-2.5 bg-[color:var(--shell-bg-2)] rounded-lg border border-[color:var(--shell-line)]">
                        <FileCode size={14} className="text-[color:var(--focus)] shrink-0" />
                        <div className="flex-1 min-w-0">
                          <p className="text-[12px] text-[color:var(--shell-text)] font-medium truncate">{file.name}</p>
                          <p className="text-[12px] text-[color:var(--shell-text-dim)] truncate">{file.path.replace(workspacePath || '', '').replace(/^\//, '')}</p>
                        </div>
                        <span className="text-[12px] text-[color:var(--shell-text-dim)] shrink-0">
                          {t('export.lines', { sayi: file.code.split('\n').length })}
                        </span>
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={onExportMultipleFiles}
                    className="w-full bg-[color:var(--ok)] hover:opacity-90 text-[color:var(--paper-bg)] p-3 rounded-xl font-bold text-xs tracking-wide transition-all flex items-center justify-center gap-2"
                  >
                    <FileDown size={14} />
                    {t('export.writeAll', { sayi: exportModal.files.length })}
                  </button>
                </div>
              ) : (
                <div className="space-y-4 mb-5">
                  <div>
                    <label className="block text-[12px] font-semibold text-[color:var(--shell-text-dim)] uppercase tracking-wider mb-1.5">
                      {t('export.fileName')}
                    </label>
                    <input
                      type="text"
                      value={exportFileName}
                      onChange={(e) => onFileNameChange(e.target.value)}
                      className="w-full bg-[color:var(--shell-bg-2)] border border-[color:var(--shell-line)] rounded-xl p-3 text-[color:var(--shell-text)] text-sm outline-none focus:border-[color:var(--focus)] transition-colors font-mono"
                      placeholder="ClassName.cs"
                    />
                  </div>

                  {exportModal.existingFile && (
                    <div className="p-3 bg-[color:var(--accent-soft)] border border-[color:var(--accent)] rounded-xl flex items-start gap-2.5">
                      <AlertTriangle size={16} className="text-[color:var(--accent-text)] shrink-0 mt-0.5" />
                      <div>
                        <p className="text-[12px] text-[color:var(--accent-text)] font-medium">{t('export.exists')}</p>
                        <p className="text-[12px] text-[color:var(--accent-text)] mt-0.5">
                          {t('export.existsHint')}
                        </p>
                      </div>
                    </div>
                  )}

                  <div className="p-3 bg-[color:var(--shell-bg-2)] rounded-xl border border-[color:var(--shell-line)]">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-[12px] text-[color:var(--shell-text-dim)] font-semibold uppercase tracking-wider">{t('export.preview')}</span>
                      <span className="text-[12px] text-[color:var(--shell-text-dim)]">{t('export.lines', { sayi: exportModal.codeString.split('\n').length })}</span>
                    </div>
                    <pre className="text-[12px] text-[color:var(--shell-text-dim)] font-mono max-h-[120px] overflow-y-auto custom-scrollbar leading-relaxed">
                      {exportModal.codeString.substring(0, 400)}{exportModal.codeString.length > 400 ? '\n...' : ''}
                    </pre>
                  </div>

                  <div className="flex gap-3">
                    <button
                      onClick={() => onExportSingleFile(exportFileName, exportModal.codeString)}
                      className="flex-1 bg-[color:var(--ok)] hover:opacity-90 text-[color:var(--paper-bg)] p-3 rounded-xl font-bold text-xs tracking-wide transition-all flex items-center justify-center gap-2"
                    >
                      <FileDown size={14} />
                      {exportModal.existingFile ? t('export.overwrite') : t('export.create')}
                    </button>
                    <button
                      onClick={onClose}
                      className="px-4 py-3 bg-[color:var(--shell-bg-2)] hover:bg-[color:var(--shell-bg-active)] text-[color:var(--shell-text-dim)] rounded-xl font-bold text-xs tracking-wide transition-all"
                    >
                      {t('export.cancel')}
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </motion.div>
      </ModalFrame>
    )}
  </AnimatePresence>
  );
};
