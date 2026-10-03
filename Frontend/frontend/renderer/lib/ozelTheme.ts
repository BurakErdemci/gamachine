import { useSyncExternalStore } from 'react'
import { luminance } from './themeTokens'

/**
 * The fifth theme, "Özel" (mockup round 15 / 15b, KARAKTER 17): the user sets three colours per
 * mode (background, foreground, accent) and one interface font. theme-ozel.*.css derives every
 * surface, line and dim text from --u-bg / --u-fg / --u-accent with color-mix(in oklab); this
 * module writes those values on <html>, plus the facts CSS cannot compute (contrast):
 *   data-u-tone  dark | light       which fixed status-colour set reads on the ground
 *   data-u-mid   (present)          neither set reaches 4.5:1 as text: status words fall back to ink
 *   data-u-acc   full | mark | off  the accent's contrast on the ground (>= 4.5 / >= 3 / below)
 *   data-u-mono  (present)          answers are set in a monospace (76ch measure, 1.7 leading)
 *   data-u-bad   (present)          the readability guard is showing
 * Every one of them is removed when another theme is active.
 */

export type OzelPresetId = 'mono' | 'kagit' | 'gece'
export type OzelMode = 'system' | 'light' | 'dark'
export type OzelFont = 'default' | 'inter' | 'geist' | 'plex-sans' | 'jetbrains-mono' | 'geist-mono' | 'plex-mono'
export type OzelRead = 'ui' | 'read'
export type OzelPalette = { bg: string; fg: string; accent: string }
export type OzelPreset = {
  /** The name written into theme text (`ad=`). */
  name: string
  font: OzelFont
  read: OzelRead
  /** The preset's natural mode: picking it switches there unless the mode is System. */
  mode: 'light' | 'dark'
  light: OzelPalette
  dark: OzelPalette
}
export type OzelSettings = {
  /** null once any value was changed by hand (or the stored preset no longer exists). */
  preset: OzelPresetId | null
  name: string
  mode: OzelMode
  light: OzelPalette
  dark: OzelPalette
  font: OzelFont
  read: OzelRead
}
export type OzelFlags = { tone: 'light' | 'dark'; mid: boolean; acc: 'full' | 'mark' | 'off'; bad: boolean }

export const OZEL_PRESETS: Record<OzelPresetId, OzelPreset> = {
  mono: {
    name: 'Mono', font: 'geist-mono', read: 'ui', mode: 'dark',
    dark: { bg: '#000000', fg: '#FFFFFF', accent: '#000000' },
    light: { bg: '#FFFFFF', fg: '#000000', accent: '#FFFFFF' },
  },
  kagit: {
    name: 'Kâğıt', font: 'plex-mono', read: 'read', mode: 'light',
    dark: { bg: '#1F1D1A', fg: '#EAE5DA', accent: '#EAE5DA' },
    light: { bg: '#F3F0E8', fg: '#1F1D1A', accent: '#1F1D1A' },
  },
  gece: {
    name: 'Gece', font: 'inter', read: 'ui', mode: 'dark',
    dark: { bg: '#0D1117', fg: '#D6DEE8', accent: '#7AA2F7' },
    light: { bg: '#F5F7FA', fg: '#18202B', accent: '#2E5BD8' },
  },
}
export const OZEL_PRESET_IDS: readonly OzelPresetId[] = ['mono', 'kagit', 'gece']
export const OZEL_MODES: readonly OzelMode[] = ['system', 'light', 'dark']
export const OZEL_FONTS: readonly OzelFont[] = ['default', 'inter', 'geist', 'plex-sans', 'jetbrains-mono', 'geist-mono', 'plex-mono']
export const OZEL_FONT_STACKS: Record<Exclude<OzelFont, 'default'>, string> = {
  inter: '"Inter", system-ui, sans-serif',
  geist: '"Geist", system-ui, sans-serif',
  'plex-sans': '"IBM Plex Sans", system-ui, sans-serif',
  'jetbrains-mono': '"JetBrains Mono", ui-monospace, monospace',
  'geist-mono': '"Geist Mono", ui-monospace, monospace',
  'plex-mono': '"IBM Plex Mono", ui-monospace, monospace',
}
const DEFAULT_UI = OZEL_FONT_STACKS.geist
const DEFAULT_READ = OZEL_FONT_STACKS.geist
const DEFAULT_CODE = OZEL_FONT_STACKS['geist-mono']
export const isMonoFont = (font: OzelFont): boolean => /-mono$/.test(font)
export const ozelFontStack = (font: OzelFont): string => (font === 'default' ? DEFAULT_UI : OZEL_FONT_STACKS[font])

/** Status colours are not user colours: fixed pairs, the set that reads on the ground is picked. */
export const OZEL_STATUS = {
  dark: { wait: '#FF9D5C', run: '#6CB2FF', ok: '#5CCF8C', err: '#FF7A7A' },
  light: { wait: '#B0500C', run: '#1760C2', ok: '#1D7A42', err: '#C02A2A' },
} as const

export function presetSettings(id: OzelPresetId, mode?: OzelMode): OzelSettings {
  const p = OZEL_PRESETS[id]
  return { preset: id, name: p.name, mode: mode ?? p.mode, light: { ...p.light }, dark: { ...p.dark }, font: p.font, read: p.read }
}
export const OZEL_DEFAULTS: OzelSettings = presetSettings('mono', 'system')

/* ---------------- colour math (sRGB <-> OKLab, WCAG contrast) ---------------- */

/** `#RRGGBB` from `#rgb` / `rrggbb` / `#rrggbb`; null when the text is not a colour. */
export function normHex(value: unknown): string | null {
  let h = String(value ?? '').trim().replace(/^#/, '')
  if (/^[0-9a-f]{3}$/i.test(h)) h = h.replace(/./g, c => c + c)
  return /^[0-9a-f]{6}$/i.test(h) ? `#${h.toUpperCase()}` : null
}

/** WCAG contrast ratio of two `#rrggbb` colours (1 .. 21). */
export function contrast(a: string, b: string): number {
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

const rgb = (h: string) => { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255] }
const lin = (v: number) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
const delin = (v: number) => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055) * 255
const toHex = (c: number[]) => `#${c.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('').toUpperCase()}`
function toLab(h: string): number[] {
  const [r, g, b] = rgb(h).map(lin)
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
  ]
}
function fromLab([L, A, B]: number[]): string {
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
  const s = (L - 0.0894841775 * A - 1.2914855480 * B) ** 3
  return toHex([
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
  ].map(delin))
}
/** = CSS `color-mix(in oklab, a p, b)` with p in 0..1. */
export function mixOklab(a: string, b: string, p: number): string {
  const x = toLab(a), y = toLab(b)
  return fromLab([0, 1, 2].map(i => x[i] * p + y[i] * (1 - p)))
}

/* ---------------- facts derived from a palette ---------------- */

/** The text pairs the theme actually draws (same percentages as theme-ozel.shell.css). */
export function readability(fg: string, bg: string): { main: number; faint: number } {
  const raised = mixOklab(fg, bg, 0.06), active = mixOklab(fg, bg, 0.10), faint = mixOklab(fg, bg, 0.72)
  return { main: contrast(fg, bg), faint: Math.min(contrast(faint, bg), contrast(faint, raised), contrast(faint, active)) }
}
export const isReadable = (fg: string, bg: string): boolean => {
  const r = readability(fg, bg)
  return r.main >= 4.5 && r.faint >= 4.5
}

export function ozelFlags(p: OzelPalette): OzelFlags {
  const raised = mixOklab(p.fg, p.bg, 0.06)
  const worst = (set: Record<string, string>) =>
    Math.min(...Object.values(set).map(c => Math.min(contrast(c, p.bg), contrast(c, raised))))
  const d = worst(OZEL_STATUS.dark), l = worst(OZEL_STATUS.light)
  const ac = Math.min(contrast(p.accent, p.bg), contrast(p.accent, mixOklab(p.fg, p.bg, 0.10)))
  return {
    tone: l > d ? 'light' : 'dark',
    mid: Math.max(d, l) < 4.5,
    acc: ac >= 4.5 ? 'full' : ac >= 3 ? 'mark' : 'off',
    bad: !isReadable(p.fg, p.bg),
  }
}

/**
 * "Okunur tona çek": move the foreground toward the far extreme until every text pair reads; a
 * mid-grey ground may still fail at pure black / white, then the ground moves away too (the
 * smallest step that works). The accent is never touched.
 */
export function fixReadable(p: OzelPalette): OzelPalette {
  let fg = p.fg, bg = p.bg
  const far = luminance(p.bg) < 0.179 ? '#FFFFFF' : '#000000'
  const away = far === '#FFFFFF' ? '#000000' : '#FFFFFF'
  for (let i = 0; i <= 50; i++) { fg = mixOklab(far, p.fg, i / 50); if (isReadable(fg, bg)) break }
  if (!isReadable(fg, bg)) {
    fg = far
    for (let i = 0; i <= 50; i++) { bg = mixOklab(away, p.bg, i / 50); if (isReadable(fg, bg)) break }
  }
  return { ...p, fg, bg }
}

/* ---------------- mode ---------------- */

const DARK_QUERY = '(prefers-color-scheme: dark)'
function darkQuery(): MediaQueryList | null {
  try { return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(DARK_QUERY) : null } catch { return null }
}
export const systemPrefersDark = (): boolean => !!darkQuery()?.matches
export function activeMode(s: OzelSettings, prefersDark = systemPrefersDark()): 'light' | 'dark' {
  return s.mode === 'system' ? (prefersDark ? 'dark' : 'light') : s.mode
}
export const activePalette = (s: OzelSettings, prefersDark?: boolean): OzelPalette => s[activeMode(s, prefersDark)]

/* ---------------- theme text: gm-tema:1;ad=..;bg=..;fg=..;vurgu=..;yazi=.. ---------------- */

export type ThemeTextValue = { name: string; bg: string; fg: string; accent: string; font: OzelFont }
export type ThemeTextField = 'bg' | 'fg' | 'vurgu'
export type ThemeTextError =
  | { code: 'empty' }
  | { code: 'prefix' }
  | { code: 'pair'; text: string }
  | { code: 'missing'; fields: ThemeTextField[] }
  | { code: 'color'; field: ThemeTextField; value: string }
  | { code: 'font'; value: string }
export type ThemeTextResult = { ok: true; value: ThemeTextValue } | { ok: false; error: ThemeTextError }

export function themeText(s: OzelSettings, prefersDark?: boolean): string {
  const p = activePalette(s, prefersDark)
  const name = s.preset ? OZEL_PRESETS[s.preset].name : s.name || 'Özel'
  return `gm-tema:1;ad=${name};bg=${p.bg};fg=${p.fg};vurgu=${p.accent};yazi=${s.font}`
}

/** Validate a theme text. Pure: a malformed text is an error and nothing else happens. */
export function parseThemeText(input: string, currentFont: OzelFont = 'default'): ThemeTextResult {
  const txt = String(input ?? '').trim()
  if (!txt) return { ok: false, error: { code: 'empty' } }
  const head = /^gm-tema:1\s*;/i
  if (!head.test(txt)) return { ok: false, error: { code: 'prefix' } }
  const out: Record<string, string> = {}
  for (const part of txt.replace(head, '').split(';')) {
    const kv = part.trim()
    if (!kv) continue
    const j = kv.indexOf('=')
    if (j < 0) return { ok: false, error: { code: 'pair', text: kv } }
    out[kv.slice(0, j).trim().toLowerCase()] = kv.slice(j + 1).trim()
  }
  const fields: ThemeTextField[] = ['bg', 'fg', 'vurgu']
  const missing = fields.filter(k => !out[k])
  if (missing.length) return { ok: false, error: { code: 'missing', fields: missing } }
  for (const k of fields) if (!normHex(out[k])) return { ok: false, error: { code: 'color', field: k, value: out[k] } }
  if (out.yazi && !OZEL_FONTS.includes(out.yazi as OzelFont)) return { ok: false, error: { code: 'font', value: out.yazi } }
  // An unknown name (e.g. the removed preset "Kömür") is only a label: the colours apply as the user's own.
  return {
    ok: true,
    value: {
      name: out.ad || 'Özel', bg: normHex(out.bg)!, fg: normHex(out.fg)!, accent: normHex(out.vurgu)!,
      font: (out.yazi as OzelFont) || currentFont,
    },
  }
}

/* ---------------- persistence ---------------- */

export const OZEL_STORAGE_KEY = 'app-ozel'
type Reader = Pick<Storage, 'getItem'>
type Writer = Pick<Storage, 'setItem'>

function validPalette(value: unknown, fallback: OzelPalette): OzelPalette {
  const v = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  return { bg: normHex(v.bg) ?? fallback.bg, fg: normHex(v.fg) ?? fallback.fg, accent: normHex(v.accent) ?? fallback.accent }
}
const pick = <T extends string>(value: unknown, values: readonly T[], fallback: T): T =>
  values.find(v => v === value) ?? fallback

export function loadOzel(storage?: Reader): OzelSettings {
  try {
    const raw = (storage ?? localStorage).getItem(OZEL_STORAGE_KEY)
    if (!raw) return clone(OZEL_DEFAULTS)
    const v = JSON.parse(raw) as Record<string, unknown>
    if (!v || typeof v !== 'object') return clone(OZEL_DEFAULTS)
    return {
      // A preset that no longer exists keeps its colours as the user's own.
      preset: OZEL_PRESET_IDS.find(id => id === v.preset) ?? null,
      name: typeof v.name === 'string' && v.name.trim() ? v.name.trim().slice(0, 40) : OZEL_DEFAULTS.name,
      mode: pick(v.mode, OZEL_MODES, OZEL_DEFAULTS.mode),
      light: validPalette(v.light, OZEL_DEFAULTS.light),
      dark: validPalette(v.dark, OZEL_DEFAULTS.dark),
      font: pick(v.font, OZEL_FONTS, OZEL_DEFAULTS.font),
      read: pick(v.read, ['ui', 'read'] as const, OZEL_DEFAULTS.read),
    }
  } catch {
    return clone(OZEL_DEFAULTS)
  }
}

export function saveOzel(s: OzelSettings, storage?: Writer): void {
  try { (storage ?? localStorage).setItem(OZEL_STORAGE_KEY, JSON.stringify(s)) } catch { /* A denied key keeps the live theme. */ }
}

function clone(s: OzelSettings): OzelSettings {
  return { ...s, light: { ...s.light }, dark: { ...s.dark } }
}

/* ---------------- the live store + applying it to <html> ---------------- */

export type OzelFontPicks = { reading: string | null; code: string | null }
type Snapshot = { settings: OzelSettings; prefersDark: boolean }

let snapshot: Snapshot | null = null
const SERVER_SNAPSHOT: Snapshot = { settings: OZEL_DEFAULTS, prefersDark: false }
const listeners = new Set<() => void>()
let applied: { root: HTMLElement; fonts: OzelFontPicks } | null = null
let watched: MediaQueryList | null = null

function current(): Snapshot {
  if (!snapshot) snapshot = { settings: loadOzel(), prefersDark: systemPrefersDark() }
  return snapshot
}
export const getOzel = (): OzelSettings => current().settings

function emit(): void { for (const l of listeners) l() }

function onSchemeChange(): void {
  snapshot = { settings: current().settings, prefersDark: systemPrefersDark() }
  if (snapshot.settings.mode === 'system' && applied && applied.root.dataset.theme === 'ozel') applyOzel(applied.root, applied.fonts)
  emit()
}
function watchScheme(): void {
  const mq = darkQuery()
  if (mq === watched) return
  try { watched?.removeEventListener?.('change', onSchemeChange) } catch { /* old list gone */ }
  watched = mq
  try { mq?.addEventListener?.('change', onSchemeChange) } catch { /* no live updates */ }
}

/** Replace the Özel settings: persists, notifies the settings UI and repaints when Özel is shown. */
export function setOzel(next: OzelSettings): void {
  snapshot = { settings: clone(next), prefersDark: current().prefersDark }
  saveOzel(snapshot.settings)
  if (applied && applied.root.dataset.theme === 'ozel') applyOzel(applied.root, applied.fonts)
  emit()
}

const U_PROPS = ['--u-bg', '--u-fg', '--u-accent', '--u-font-ui', '--u-font-read', '--u-font-code'] as const
const U_ATTRS = ['data-u-tone', 'data-u-mid', 'data-u-acc', 'data-u-mono', 'data-u-bad'] as const
const toggleAttr = (root: HTMLElement, name: string, on: boolean) => { if (on) root.setAttribute(name, ''); else root.removeAttribute(name) }

/**
 * Paint Özel on `root`. `fonts` are the Görünüm reading / code font picks (null = theme default):
 * the reading font serves "Okuma yazı tipi" for long answers, the code font overrides code.
 */
export function applyOzel(root: HTMLElement, fonts: OzelFontPicks): void {
  applied = { root, fonts }
  watchScheme()
  const s = current().settings
  const p = activePalette(s)
  const f = ozelFlags(p)
  root.style.setProperty('--u-bg', p.bg)
  root.style.setProperty('--u-fg', p.fg)
  root.style.setProperty('--u-accent', p.accent)
  root.setAttribute('data-u-tone', f.tone)
  root.setAttribute('data-u-acc', f.acc)
  toggleAttr(root, 'data-u-mid', f.mid)
  toggleAttr(root, 'data-u-bad', f.bad)
  // Özel sets every font role from --u-font-*; an inline --font-body / --font-mono would override them.
  root.style.removeProperty('--font-body')
  root.style.removeProperty('--font-mono')
  const ui = ozelFontStack(s.font)
  root.style.setProperty('--u-font-ui', ui)
  root.style.setProperty('--u-font-read', s.read === 'ui' ? ui : fonts.reading ?? DEFAULT_READ)
  root.style.setProperty('--u-font-code', fonts.code ?? (isMonoFont(s.font) ? ui : DEFAULT_CODE))
  toggleAttr(root, 'data-u-mono', s.read === 'ui' && isMonoFont(s.font))
}

/** Remove every Özel property and attribute (the character themes must not inherit any). */
export function clearOzel(root: HTMLElement): void {
  if (applied?.root === root) applied = null
  for (const name of U_PROPS) root.style.removeProperty(name)
  for (const name of U_ATTRS) root.removeAttribute(name)
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  watchScheme()
  return () => { listeners.delete(cb) }
}

/** The Özel settings for the settings UI, re-rendered on every change and on a system scheme change. */
export function useOzel(): Snapshot {
  return useSyncExternalStore(subscribe, current, () => SERVER_SNAPSHOT)
}
