import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import type { RemoteSession, RemoteView } from '../../lib/remote/bootstrap'
import { useLang, type TKey } from '../../lib/i18n'
import { ToastContainer, useToast } from '../ui/Toast'

function useRemoteView(session: RemoteSession): RemoteView {
  return useSyncExternalStore(
    (cb) => session.subscribe(cb),
    () => session.view,
    () => session.view,
  )
}

/** Pairing screen of the web remote; copy follows relay/public (the v1 phone page). */
export function RemotePairing({ session, view }: { session: RemoteSession; view: RemoteView }) {
  const { t } = useLang()
  const [link, setLink] = useState('')
  const message = view.message ? t(view.message.key as TKey, view.message.values) : null
  const working = view.pairStep === 'working'

  return (
    <main className="gm-remote">
      <section className="gm-remote-card" aria-live="polite">
        <h1 className="gm-remote-title">{working ? t('remoteWeb.pairing') : t('remoteWeb.title')}</h1>
        {view.screen === 'loading' && <p className="gm-remote-dim">{t('remoteWeb.connectingPc')}</p>}

        {view.screen === 'pairing' && working && (
          <>
            <p className="gm-remote-dim">{view.sas ? t('remoteWeb.awaitingApproval') : t('remoteWeb.connectingPc')}</p>
            {view.sas && (
              <div className="gm-remote-sas">
                <p>{t('remoteWeb.sasPrompt')}</p>
                <div className="gm-remote-code">{view.sas}</div>
                <p className="gm-remote-dim">{t('remoteWeb.sasFollow')}</p>
              </div>
            )}
          </>
        )}

        {view.screen === 'pairing' && !working && view.paired && (
          <>
            <p className="gm-remote-dim">
              {view.connection === 'offline'
                ? t('remoteWeb.offline') + (view.lastSeen ? t('remoteWeb.lastSeen', { time: new Date(view.lastSeen).toLocaleString() }) : '')
                : view.connection === 'rejected'
                  ? t('remoteWeb.rejected')
                  : t('remoteWeb.connectingPc')}
            </p>
            {view.connection === 'rejected' && (
              <p className="gm-remote-error">{t(view.rejectReason === 'clock' ? 'remoteWeb.clock' : 'remoteWeb.unregistered')}</p>
            )}
            <button type="button" className="gm-remote-btn gm-remote-btn-ghost" onClick={() => void session.forget()}>
              {t('remoteWeb.forget')}
            </button>
          </>
        )}

        {view.screen === 'pairing' && !working && !view.paired && (
          <form
            className="gm-remote-form"
            onSubmit={(e) => {
              e.preventDefault()
              void session.pairFromText(link)
            }}
          >
            <p>{t('remoteWeb.intro')}</p>
            <label className="gm-remote-label" htmlFor="gm-remote-link">{t('remoteWeb.pasteLabel')}</label>
            <input
              id="gm-remote-link"
              className="gm-remote-input"
              value={link}
              onChange={(e) => setLink(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            <button type="submit" className="gm-remote-btn" disabled={!link.trim()}>
              {t('remoteWeb.pair')}
            </button>
          </form>
        )}

        {message && (
          <p className={view.pairStep === 'failed' ? 'gm-remote-error' : 'gm-remote-note'}>
            {message}
          </p>
        )}
      </section>
    </main>
  )
}

/** Shows pairing until a session is up, then the app with a connection banner and toasts. */
export function RemoteGate({ session, children }: { session: RemoteSession; children?: React.ReactNode }) {
  const view = useRemoteView(session)
  const { t } = useLang()
  const { toasts, showToast, dismissToast } = useToast()
  const told = useRef(new Set<string>())

  useEffect(() => {
    void session.start()
  }, [session])

  // One toast per channel: a panel that polls a desktop-only channel must not flood the screen.
  useEffect(
    () =>
      session.onUnavailable((channel) => {
        if (told.current.has(channel)) return
        told.current.add(channel)
        showToast(t('remoteWeb.desktopOnly'), 'warning')
      }),
    [session, showToast, t],
  )

  if (view.screen !== 'app') return <RemotePairing session={session} view={view} />
  return (
    <>
      {view.connection !== 'ready' && (
        <div className="gm-remote-banner" role="status">
          {view.connection === 'offline'
            ? t('remoteWeb.offline')
            : view.connection === 'rejected'
              ? t('remoteWeb.rejected')
              : t('remoteWeb.connectionLost')}
        </div>
      )}
      {children}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </>
  )
}
