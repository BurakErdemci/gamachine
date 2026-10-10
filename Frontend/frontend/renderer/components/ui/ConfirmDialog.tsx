import React, { useState, useEffect, useCallback, useId } from 'react';
import { AnimatePresence, motion, useIsPresent } from 'framer-motion';
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

/**
 * role + aria-modal: screen readers announce it, and window-level shortcuts (Ctrl+S save,
 * Ctrl+N new chat) can tell a modal owns the keyboard. AnimatePresence keeps the node through
 * its exit animation, so aria-modal is dropped once it is leaving; otherwise Ctrl+S pressed
 * right after answering was swallowed without saving.
 */
function ConfirmCard({ labelId, children }: { labelId: string; children: React.ReactNode }) {
  const present = useIsPresent();
  return (
    <motion.div
      role="alertdialog"
      aria-modal={present ? 'true' : undefined}
      aria-labelledby={labelId}
      className="confirm"
      initial={{ scale: 0.97, y: 8 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.97, y: 8 }}
      transition={{ duration: 0.16 }}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </motion.div>
  );
}

export function ConfirmDialogHost() {
  const [state, setState] = useState<ConfirmState | null>(null);
  const msgId = useId();

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
          className="confirm-veil"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          transition={{ duration: 0.16 }}
          onClick={() => resolve(false)}
        >
          {/* Look lives in thread.css (.confirm) plus each theme's thread file, next to the
              approval card it is a sibling of. .confirm-stub is a theme hook (Arena's offset
              shadow, Atolye's tear-off stub); it draws nothing in the base theme. */}
          <ConfirmCard labelId={msgId}>
            <span className="confirm-stub" aria-hidden="true" />
            <div className="confirm-body">
              <span className="confirm-mark" aria-hidden="true">
                <AlertTriangle size={16} strokeWidth={2.25} />
              </span>
              <p id={msgId} className="confirm-msg">{state.message}</p>
            </div>
            <div className="confirm-actions">
              <button type="button" className="btn btn-ghost confirm-no" onClick={() => resolve(false)}>
                {state.cancelLabel}
              </button>
              <button type="button" autoFocus className="btn btn-primary confirm-go" onClick={() => resolve(true)}>
                {state.confirmLabel}
              </button>
            </div>
          </ConfirmCard>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
