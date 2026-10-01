import { AlertTriangle, CheckCircle2, FileCode, FileDown, Folder, X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";

import { ExportModalState } from "./types";
import { useLang } from "../../lib/i18n";


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
      <div role="dialog" aria-modal="true" className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-[100]" onClick={() => !exportModal.exportResult && onClose()}>
        <motion.div
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          className="bg-[#0a0a0a] border border-slate-800 rounded-2xl p-6 max-w-lg w-full shadow-2xl mx-4"
          onClick={(e) => e.stopPropagation()}
        >
          {exportModal.exportResult ? (
            <div className="flex flex-col items-center gap-4 py-4">
              <div className={`p-4 rounded-2xl ${exportModal.exportResult.success ? 'bg-emerald-500/10 border border-emerald-500/20' : 'bg-red-500/10 border border-red-500/20'}`}>
                {exportModal.exportResult.success ? (
                  <CheckCircle2 size={40} className="text-emerald-400" />
                ) : (
                  <AlertTriangle size={40} className="text-red-400" />
                )}
              </div>
              <div className="text-center">
                <h3 className="text-base font-bold text-white mb-1">
                  {exportModal.exportResult.success ? t('export.success') : t('export.error')}
                </h3>
                <p className="text-[13px] text-slate-400 whitespace-pre-line">
                  {exportModal.exportResult.message}
                </p>
                <p className="text-[11px] text-slate-600 mt-2">
                  📁 {exportModal.targetDir}
                </p>
              </div>
              <button
                onClick={onClose}
                className="mt-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-xl font-bold text-xs tracking-wide transition-all"
              >
                {t('export.ok')}
              </button>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-5">
                <div className="flex items-center gap-2.5">
                  <div className="p-1.5 bg-emerald-500/10 rounded-lg text-emerald-500">
                    <FileDown size={18} />
                  </div>
                  <div>
                    <h2 className="text-base font-bold text-white">
                      {exportModal.multiFile ? t('export.multiTitle') : t('export.singleTitle')}
                    </h2>
                    <p className="text-[10px] text-slate-500 mt-0.5">
                      {exportModal.multiFile ? t('export.multiSubtitle', { sayi: exportModal.files.length }) : t('export.singleSubtitle')}
                    </p>
                  </div>
                </div>
                <button onClick={onClose} className="p-1.5 hover:bg-slate-800 rounded-lg transition-colors text-slate-400">
                  <X size={18} />
                </button>
              </div>

              <div className="mb-4 p-3 bg-slate-900/50 rounded-xl border border-slate-800/50 hover:border-slate-700/60 transition-colors">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2 text-[10px] text-slate-500 font-semibold uppercase tracking-wider">
                    <Folder size={11} /> {t('export.targetDir')}
                  </div>
                  <button
                    onClick={onChangeExportDir}
                    className="text-[10px] text-blue-400 hover:text-blue-300 font-semibold transition-colors px-2 py-0.5 rounded hover:bg-blue-500/10"
                  >
                    {t('export.change')}
                  </button>
                </div>
                <p className="text-[12px] text-slate-300 font-mono truncate">
                  {exportModal.targetDir}
                </p>
              </div>

              {exportModal.multiFile ? (
                <div className="space-y-3 mb-5">
                  <label className="block text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                    {t('export.filesToCreate')}
                  </label>
                  <div className="space-y-1.5 max-h-[240px] overflow-y-auto custom-scrollbar">
                    {exportModal.files.map((file, idx) => (
                      <div key={idx} className="flex items-center gap-2.5 p-2.5 bg-slate-900/30 rounded-lg border border-slate-800/30">
                        <FileCode size={14} className="text-blue-400 shrink-0" />
                        <div className="flex-1 min-w-0">
                          <p className="text-[12px] text-slate-200 font-medium truncate">{file.name}</p>
                          <p className="text-[10px] text-slate-600 truncate">{file.path.replace(workspacePath || '', '').replace(/^\//, '')}</p>
                        </div>
                        <span className="text-[9px] text-slate-600 shrink-0">
                          {t('export.lines', { sayi: file.code.split('\n').length })}
                        </span>
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={onExportMultipleFiles}
                    className="w-full bg-emerald-600 hover:bg-emerald-500 text-white p-3 rounded-xl font-bold text-xs tracking-wide transition-all flex items-center justify-center gap-2"
                  >
                    <FileDown size={14} />
                    {t('export.writeAll', { sayi: exportModal.files.length })}
                  </button>
                </div>
              ) : (
                <div className="space-y-4 mb-5">
                  <div>
                    <label className="block text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
                      {t('export.fileName')}
                    </label>
                    <input
                      type="text"
                      value={exportFileName}
                      onChange={(e) => onFileNameChange(e.target.value)}
                      className="w-full bg-[#000000] border border-slate-800 rounded-xl p-3 text-white text-sm outline-none focus:border-emerald-500 transition-colors font-mono"
                      style={{ backgroundColor: '#000000', color: 'white' }}
                      placeholder="ClassName.cs"
                    />
                  </div>

                  {exportModal.existingFile && (
                    <div className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl flex items-start gap-2.5">
                      <AlertTriangle size={16} className="text-amber-400 shrink-0 mt-0.5" />
                      <div>
                        <p className="text-[12px] text-amber-300 font-medium">{t('export.exists')}</p>
                        <p className="text-[11px] text-amber-400/70 mt-0.5">
                          {t('export.existsHint')}
                        </p>
                      </div>
                    </div>
                  )}

                  <div className="p-3 bg-slate-900/30 rounded-xl border border-slate-800/30">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">{t('export.preview')}</span>
                      <span className="text-[10px] text-slate-600">{t('export.lines', { sayi: exportModal.codeString.split('\n').length })}</span>
                    </div>
                    <pre className="text-[11px] text-slate-400 font-mono max-h-[120px] overflow-y-auto custom-scrollbar leading-relaxed">
                      {exportModal.codeString.substring(0, 400)}{exportModal.codeString.length > 400 ? '\n...' : ''}
                    </pre>
                  </div>

                  <div className="flex gap-3">
                    <button
                      onClick={() => onExportSingleFile(exportFileName, exportModal.codeString)}
                      className="flex-1 bg-emerald-600 hover:bg-emerald-500 text-white p-3 rounded-xl font-bold text-xs tracking-wide transition-all flex items-center justify-center gap-2"
                    >
                      <FileDown size={14} />
                      {exportModal.existingFile ? t('export.overwrite') : t('export.create')}
                    </button>
                    <button
                      onClick={onClose}
                      className="px-4 py-3 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl font-bold text-xs tracking-wide transition-all"
                    >
                      {t('export.cancel')}
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </motion.div>
      </div>
    )}
  </AnimatePresence>
  );
};
