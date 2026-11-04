import React from 'react'
import type { AppProps } from 'next/app'
import Head from 'next/head'

import '../styles/gm/fonts.css'
import '../styles/gm/tokens.css'
import '../styles/gm/shell.css'
import '../styles/gm/theme-arena.shell.css'
import '../styles/gm/theme-sade.shell.css'
import '../styles/gm/theme-pafta.shell.css'
import '../styles/gm/theme-atolye.shell.css'
import '../styles/globals.css'
// After globals.css on purpose: the thread styles replace the old slate chat rules (.chat-prose,
// .chat-table) that globals.css still carries for the not-yet-ported surfaces.
import '../styles/gm/thread.css'
import '../styles/gm/theme-arena.thread.css'
import '../styles/gm/theme-sade.thread.css'
import '../styles/gm/theme-pafta.thread.css'
import '../styles/gm/theme-atolye.thread.css'
import '../styles/gm/workspace.css'
import '../styles/gm/scene-editor.css'
import '../styles/gm/theme-sade.workspace.css'
import '../styles/gm/theme-pafta.workspace.css'
import '../styles/gm/theme-atolye.workspace.css'
import '../styles/gm/settings.css'
import '../styles/gm/computer-use.css'
import '../styles/gm/profile.css'
import '../styles/gm/guide.css'
import '../styles/gm/welcome.css'
import '../styles/gm/intro.css'
import '../styles/gm/toast.css'
import '../styles/gm/computer-live.css'
import '../styles/gm/browser-panel.css'
import '../styles/gm/remote.css'
// The web remote's phone layout, after every base and per-theme file but Özel's (which stays last, see
// below): all of it is under .app[data-phone], which outranks Özel's own selectors, and its Özel block
// carries the extra [data-theme="ozel"].
import '../styles/gm/phone.css'
// Last on purpose: Özel's rules (all under [data-theme="ozel"]) override base rules from every file
// above, the way each Sade override follows its base rule inside the same file.
import '../styles/gm/theme-ozel.shell.css'
import '../styles/gm/theme-ozel.thread.css'
import '../styles/gm/theme-ozel.workspace.css'
import { IntroOverlay } from '../components/intro/IntroOverlay'
import { CopyOnSelect } from '../components/ui/CopyOnSelect'
import { ConfirmDialogHost } from '../components/ui/ConfirmDialog'
import { ErrorBoundary } from '../components/ui/ErrorBoundary'
import { AppearanceProvider } from '../lib/appearance'
import { withRemoteMode } from '../lib/remote/bootstrap'

// Only the REMOTE_WEB=1 export sets the base path (next.config.js): the desktop document keeps Next's
// own viewport tag. On a phone, viewport-fit=cover lets the safe-area insets (phone.css) take effect.
const REMOTE_BUILD = !!process.env.NEXT_PUBLIC_BASE_PATH

function MyApp({ Component, pageProps, router }: AppProps) {
  // The computer-use indicator is its own tiny always-on-top window: the intro, copy and
  // confirm hosts belong to the main window and must not appear over the user's apps.
  if (router?.pathname === '/computer-live') {
    return (
      // The 320x64 window must keep its own zoom: text size would clip it.
      <AppearanceProvider applyZoom={false}>
        <ErrorBoundary>
          <Component {...pageProps} />
        </ErrorBoundary>
      </AppearanceProvider>
    )
  }
  return (
    <AppearanceProvider>
      {REMOTE_BUILD && (
        <Head>
          <meta name="viewport" key="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
        </Head>
      )}
      {/* Sayfa ağacındaki bir render hatası eskiden TÜM pencereyi boşaltıyordu
          ve uygulamayı kapatıp açmak gerekiyordu. */}
      <ErrorBoundary>
        <Component {...pageProps} />
      </ErrorBoundary>
      <IntroOverlay />
      <CopyOnSelect />
      {/* Native confirm() yerine uygulama-içi onay (Electron focus-kilit bug fix).
          ⚠️ BİLEREK sınırın DIŞINDA: global bir singleton ve sınırın içine
          alınsaydı bir render hatası onu da unmount edip `confirmDialog()`'u
          sessizce native `confirm()`e düşürürdü — yani düzeltildiği bilinen
          Electron focus-kilit arızasına geri dönerdi. */}
      <ConfirmDialogHost />
    </AppearanceProvider>
  )
}

// Remote mode (relay-served page, no Electron): installs the remote shims and shows pairing
// until a session is up. On the desktop it returns MyApp unchanged.
export default withRemoteMode(MyApp)
