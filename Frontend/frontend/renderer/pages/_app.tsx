import React from 'react'
import type { AppProps } from 'next/app'

import '../styles/gm/fonts.css'
import '../styles/gm/tokens.css'
import '../styles/globals.css'
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
