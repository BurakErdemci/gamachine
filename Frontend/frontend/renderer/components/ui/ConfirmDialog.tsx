import React, { useState, useEffect, useCallback } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle } from 'lucide-react';
import { cevir } from '../../lib/i18n';

/**
 * Uygulama-içi onay dialog'u — native window.confirm() yerine.
 *
 * NEDEN: Electron'da native confirm()/alert() BLOKLAYAN dialog'dur; kapandıktan
 * sonra renderer focus'u kilitleniyor → sohbet/dosya silince chat input'a
 * tıklanamıyor, ancak alt-tab (pencere yeniden odaklanma) ya da başka bir
 * focusable'a tıklayınca düzeliyordu. Bu özel modal o bug'ı tamamen kaldırır.
 *
 * KULLANIM: `if (await confirmDialog("...")) { ... }`. ConfirmDialogHost bir kez
 * mount edilmiş olmalı (_app.tsx). Mount değilse güvenli fallback: native confirm.
 */

interface ConfirmState {
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  resolve: (v: boolean) => void;
}

let _trigger: ((opts: { message: string; confirmLabel: string; cancelLabel: string }) => Promise<boolean>) | null = null;

// Varsayilan etiketler cagri aninda cevriliyor: bu modul `_app.tsx`'te,
// yani `LangContext.Provider`'in DISINDA duruyor ve `useLang` cagiramaz.
export function confirmDialog(
  message: string,
  confirmLabel = cevir('confirm.delete'),
  cancelLabel = cevir('confirm.cancel'),
): Promise<boolean> {
  if (!_trigger) {
    return Promise.resolve(typeof window !== 'undefined' ? window.confirm(message) : false);
  }
  return _trigger({ message, confirmLabel, cancelLabel });
}

export function ConfirmDialogHost() {
  const [state, setState] = useState<ConfirmState | null>(null);

  useEffect(() => {
    _trigger = ({ message, confirmLabel, cancelLabel }) =>
      new Promise<boolean>((resolve) => setState({ message, confirmLabel, cancelLabel, resolve }));
    return () => { _trigger = null; };
  }, []);

  const resolve = useCallback((v: boolean) => {
    setState(prev => { prev?.resolve(v); return null; });
  }, []);

  useEffect(() => {
    if (!state) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); resolve(false); }
      else if (e.key === 'Enter') { e.preventDefault(); resolve(true); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [state, resolve]);

  return (
    <AnimatePresence>
      {state && (
        <motion.div
          className="fixed inset-0 z-[300] flex items-center justify-center bg-black/60 backdrop-blur-sm"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          onClick={() => resolve(false)}
        >
          {/* role + aria-modal: screen readers announce it, and window-level shortcuts
              (Ctrl+N new chat) can tell a modal owns the keyboard. */}
          <motion.div
            role="alertdialog"
            aria-modal="true"
            className="bg-[#0a0a0a] border border-slate-700 rounded-2xl shadow-2xl w-[340px] max-w-[90vw] p-5"
            initial={{ scale: 0.95, y: 10 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, y: 10 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-3 mb-4">
              <div className="shrink-0 w-9 h-9 rounded-full bg-red-500/15 border border-red-500/30 flex items-center justify-center">
                <AlertTriangle size={16} className="text-red-400" />
              </div>
              <p className="text-[13px] text-slate-200 leading-relaxed pt-1.5">{state.message}</p>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => resolve(false)}
                className="px-3.5 py-1.5 rounded-lg text-[12px] font-medium text-slate-300 bg-slate-800/60 hover:bg-slate-700/60 transition-colors"
              >
                {state.cancelLabel}
              </button>
              <button
                autoFocus
                onClick={() => resolve(true)}
                className="px-3.5 py-1.5 rounded-lg text-[12px] font-semibold text-white bg-red-600 hover:bg-red-500 transition-colors"
              >
                {state.confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
