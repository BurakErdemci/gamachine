import { Html, Head, Main, NextScript } from 'next/document'

// The default theme is on <html> in the served markup, so the first frame already paints Arena
// instead of the grey base tokens. A stored theme replaces it on mount (lib/appearance.ts); an
// inline script could apply it earlier, but the CSP forbids inline scripts.
export default function Document() {
  return (
    <Html lang="en" data-theme="arena">
      <Head />
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  )
}
