import React, { useState, useCallback, useRef } from 'react'
import { X, CheckCircle2, XCircle, AlertTriangle, Info } from 'lucide-react'
import { useLang } from '../../lib/i18n'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

/** One button on the toast that does the thing its message is about; a press also closes the toast. */
export interface ToastAction {
  label: string
  onClick: () => void
}

export interface ToastOptions {
  action?: ToastAction
}

/**
 * Shows a toast and returns the function that takes it down early (a "starting..." toast whose
 * outcome arrived). Callers that only show a message ignore the return.
 */
export type ShowToast = (message: string, type: ToastType, duration?: number, options?: ToastOptions) => (() => void) | void

export interface Toast {
  id: number
  message: string
  type: ToastType
  /** Lifetime in ms; drives the life bar. */
  duration?: number
  action?: ToastAction
}

const ICONS: Record<ToastType, React.ReactNode> = {
  success: <CheckCircle2 size={16} />,
  error:   <XCircle size={16} />,
  warning: <AlertTriangle size={16} />,
  info:    <Info size={16} />,
}

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const counter = useRef(0)

  const showToast = useCallback((message: string, type: ToastType = 'info', duration = 4000, options?: ToastOptions) => {
    const id = ++counter.current
    const drop = () => setToasts(prev => prev.filter(t => t.id !== id))
    setToasts(prev => [...prev, { id, message, type, duration, ...(options?.action ? { action: options.action } : {}) }])
    setTimeout(drop, duration)
    return drop
  }, [])

  const dismissToast = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  return { toasts, showToast, dismissToast }
}

interface ToastContainerProps {
  toasts: Toast[]
  onDismiss: (id: number) => void
}

/**
 * The v4 toast: the achievement band's surface (toast.css), with the status carried by an icon,
 * the kicker word and an accent from the per-theme `--toast-<status>` tokens.
 */
export function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  const { t } = useLang()
  return (
    <div className="gm-toasts">
      {toasts.map(toast => (
        <div
          key={toast.id}
          className="gm-toast"
          data-status={toast.type}
          role={toast.type === 'error' ? 'alert' : 'status'}
          style={{ '--toast-life': `${toast.duration ?? 4000}ms` } as React.CSSProperties}
        >
          <span className="gm-toast-ic" aria-hidden="true">{ICONS[toast.type]}</span>
          <span className="gm-toast-text">
            <span className="gm-toast-k">{t(`toast.${toast.type}`)}</span>
            <span className="gm-toast-msg">{toast.message}</span>
            {toast.action && (
              <button
                type="button"
                className="gm-toast-act"
                onClick={() => { onDismiss(toast.id); toast.action!.onClick() }}
              >
                {toast.action.label}
              </button>
            )}
          </span>
          <button
            type="button"
            className="gm-toast-x"
            aria-label={t('toast.close')}
            onClick={() => onDismiss(toast.id)}
          >
            <X size={13} aria-hidden="true" />
          </button>
          <span className="gm-toast-life" aria-hidden="true" />
        </div>
      ))}
    </div>
  )
}
