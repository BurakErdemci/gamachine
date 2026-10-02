import React from 'react'
import type { AppProps } from 'next/app'

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
import '../styles/gm/theme-sade.workspace.css'
import '../styles/gm/theme-pafta.workspace.css'
import '../styles/gm/theme-atolye.workspace.css'
import '../styles/gm/settings.css'
import '../styles/gm/intro.css'
import { IntroOverlay } from '../components/intro/IntroOverlay'
import { ConfirmDialogHost } from '../components/ui/ConfirmDialog'
import { ErrorBoundary } from '../components/ui/ErrorBoundary'
import { AppearanceProvider } from '../lib/appearance'

function MyApp({ Component, pageProps }: AppProps) {
  return (
    <AppearanceProvider>
      {/* Sayfa ağacındaki bir render hatası eskiden TÜM pencereyi boşaltıyordu
          ve uygulamayı kapatıp açmak gerekiyordu. */}
      <ErrorBoundary>
        <Component {...pageProps} />
      </ErrorBoundary>
      <IntroOverlay />
      {/* Native confirm() yerine uygulama-içi onay (Electron focus-kilit bug fix).
          ⚠️ BİLEREK sınırın DIŞINDA: global bir singleton ve sınırın içine
          alınsaydı bir render hatası onu da unmount edip `confirmDialog()`'u
          sessizce native `confirm()`e düşürürdü — yani düzeltildiği bilinen
          Electron focus-kilit arızasına geri dönerdi. */}
      <ConfirmDialogHost />
    </AppearanceProvider>
  )
}

export default MyApp
