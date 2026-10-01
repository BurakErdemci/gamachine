import { createContext, createElement, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

export type Theme = 'arena' | 'sade' | 'pafta' | 'atolye'
export type ReadingFont = 'theme' | 'inter' | 'geist' | 'plex-sans' | 'figtree' | 'atkinson'
export type CodeFont = 'theme' | 'jetbrains' | 'geist-mono' | 'plex-mono' | 'fira' | 'cascadia'
export type TextSize = 'small' | 'normal' | 'large'
export type Appearance = { theme: Theme; readingFont: ReadingFont; codeFont: CodeFont; textSize: TextSize; intro: boolean }

export const DEFAULTS: Appearance = { theme: 'arena', readingFont: 'theme', codeFont: 'theme', textSize: 'normal', intro: true }
export const FONT_STACKS = {
  inter: '"Inter", system-ui, sans-serif',
  geist: '"Geist", system-ui, sans-serif',
  'plex-sans': '"IBM Plex Sans", system-ui, sans-serif',
  figtree: '"Figtree", system-ui, sans-serif',
  atkinson: '"Atkinson Hyperlegible", system-ui, sans-serif',
  jetbrains: '"JetBrains Mono", ui-monospace, monospace',
  'geist-mono': '"Geist Mono", ui-monospace, monospace',
  'plex-mono': '"IBM Plex Mono", ui-monospace, monospace',
  fira: '"Fira Code", ui-monospace, monospace',
  cascadia: '"Cascadia Code", ui-monospace, monospace',
} as const
export const ZOOM_FACTORS: Record<TextSize, number> = { small: 0.9, normal: 1, large: 1.1 }

export const THEMES: readonly Theme[] = ['arena', 'sade', 'pafta', 'atolye']
export const READING_FONTS: readonly ReadingFont[] = ['theme', 'inter', 'geist', 'plex-sans', 'figtree', 'atkinson']
export const CODE_FONTS: readonly CodeFont[] = ['theme', 'jetbrains', 'geist-mono', 'plex-mono', 'fira', 'cascadia']
export const TEXT_SIZES: readonly TextSize[] = ['small', 'normal', 'large']

type Reader = Pick<Storage, 'getItem'>
type Writer = Pick<Storage, 'setItem'>
function validValue<T extends string>(value: string | null, values: readonly T[], fallback: T): T {
  return values.find(candidate => candidate === value) ?? fallback
}

export function loadAppearance(storage?: Reader): Appearance {
  try {
    const source = storage ?? localStorage
    const theme = source.getItem('app-theme')
    const readingFont = source.getItem('app-font-reading')
    const codeFont = source.getItem('app-font-code')
    const textSize = source.getItem('app-text-size')
    const intro = source.getItem('app-intro')
    return {
      theme: validValue(theme, THEMES, DEFAULTS.theme),
      readingFont: validValue(readingFont, READING_FONTS, DEFAULTS.readingFont),
      codeFont: validValue(codeFont, CODE_FONTS, DEFAULTS.codeFont),
      textSize: validValue(textSize, TEXT_SIZES, DEFAULTS.textSize),
      intro: intro === 'off' ? false : DEFAULTS.intro,
    }
  } catch {
    return { ...DEFAULTS }
  }
}

export function saveAppearance(a: Appearance, storage?: Writer): void {
  const entries = [
    ['app-theme', a.theme], ['app-font-reading', a.readingFont],
    ['app-font-code', a.codeFont], ['app-text-size', a.textSize], ['app-intro', a.intro ? 'on' : 'off'],
  ]
  for (const [key, value] of entries) {
    try { (storage ?? localStorage).setItem(key, value) } catch { /* A denied key must not prevent saving the others. */ }
  }
}

export function applyAppearance(a: Appearance, root = document.documentElement): void {
  root.dataset.theme = a.theme
  if (a.readingFont === 'theme') root.style.removeProperty('--font-body')
  else root.style.setProperty('--font-body', FONT_STACKS[a.readingFont])
  if (a.codeFont === 'theme') root.style.removeProperty('--font-mono')
  else root.style.setProperty('--font-mono', FONT_STACKS[a.codeFont])
}

export function applyZoom(textSize: TextSize): void {
  try {
    void Promise.resolve(window.ipc?.invoke('app-zoom-set', ZOOM_FACTORS[textSize])).catch(() => {})
  } catch { /* Browsers and unavailable Electron windows need no zoom fallback. */ }
}

export const AppearanceContext = createContext<{
  appearance: Appearance
  setAppearance: (partial: Partial<Appearance>) => void
}>({ appearance: DEFAULTS, setAppearance: () => {} })

export const useAppearance = () => useContext(AppearanceContext)

export function AppearanceProvider({ children }: { children: ReactNode }) {
  // SSR and the first client render must agree before storage is available.
  const [appearance, setState] = useState<Appearance>({ ...DEFAULTS })
  const current = useRef(appearance)
  useEffect(() => {
    const stored = loadAppearance()
    current.current = stored
    setState(stored)
    applyAppearance(stored)
    applyZoom(stored.textSize)
  }, [])

  const setAppearance = useCallback((partial: Partial<Appearance>) => {
    const previous = current.current
    const next = { ...previous, ...partial }
    current.current = next
    setState(next)
    saveAppearance(next)
    applyAppearance(next)
    if (next.textSize !== previous.textSize) applyZoom(next.textSize)
  }, [])

  return createElement(AppearanceContext.Provider, { value: { appearance, setAppearance } }, children)
}
