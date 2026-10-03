import React, { useState, useCallback, useRef } from 'react'
import { X, CheckCircle2, XCircle, AlertTriangle, Info } from 'lucide-react'
import { useLang } from '../../lib/i18n'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface Toast {
  id: number
  message: string
  type: ToastType
  /** Lifetime in ms; drives the life bar. */
  duration?: number
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

  const showToast = useCallback((message: string, type: ToastType = 'info', duration = 4000) => {
    const id = ++counter.current
    setToasts(prev => [...prev, { id, message, type, duration }])
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), duration)
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
