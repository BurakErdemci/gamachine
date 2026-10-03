import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook } from '@testing-library/react'
import { applyAppearance, DEFAULTS, FONT_STACKS, THEMES } from '../renderer/lib/appearance'
import {
  OZEL_DEFAULTS, OZEL_PRESETS, OZEL_STATUS, OZEL_STORAGE_KEY, clearOzel, contrast, fixReadable, getOzel, isReadable, loadOzel, mixOklab, normHex,
  ozelFlags, parseThemeText, presetSettings, readability, saveOzel, setOzel, themeText, useOzel,
} from '../renderer/lib/ozelTheme'

const root = document.documentElement
const U_PROPS = ['--u-bg', '--u-fg', '--u-accent', '--u-font-ui', '--u-font-read', '--u-font-code']
const U_ATTRS = ['data-u-tone', 'data-u-mid', 'data-u-acc', 'data-u-mono', 'data-u-bad']

/** A controllable prefers-color-scheme query. */
function stubScheme(dark: boolean) {
  const listeners = new Set<() => void>()
  const mq = {
    matches: dark, media: '(prefers-color-scheme: dark)',
    addEventListener: (_: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
  }
  vi.stubGlobal('matchMedia', () => mq)
  return { set(next: boolean) { mq.matches = next; for (const l of [...listeners]) l() }, listeners }
}

beforeEach(() => {
  localStorage.clear()
  setOzel(OZEL_DEFAULTS)
  localStorage.clear()
})
afterEach(() => {
  cleanup()
  applyAppearance(DEFAULTS)
  vi.unstubAllGlobals()
})

describe('Özel presets', () => {
  it('carries the approved mockup values (Mono, Kâğıt, Gece; light + dark each)', () => {
    expect(Object.keys(OZEL_PRESETS)).toEqual(['mono', 'kagit', 'gece'])
    expect(OZEL_PRESETS.mono).toEqual({
      name: 'Mono', font: 'geist-mono', read: 'ui', mode: 'dark',
      dark: { bg: '#000000', fg: '#FFFFFF', accent: '#000000' }, light: { bg: '#FFFFFF', fg: '#000000', accent: '#FFFFFF' },
    })
    expect(OZEL_PRESETS.kagit).toEqual({
      name: 'Kâğıt', font: 'plex-mono', read: 'read', mode: 'light',
      dark: { bg: '#1F1D1A', fg: '#EAE5DA', accent: '#EAE5DA' }, light: { bg: '#F3F0E8', fg: '#1F1D1A', accent: '#1F1D1A' },
    })
    expect(OZEL_PRESETS.gece).toEqual({
      name: 'Gece', font: 'inter', read: 'ui', mode: 'dark',
      dark: { bg: '#0D1117', fg: '#D6DEE8', accent: '#7AA2F7' }, light: { bg: '#F5F7FA', fg: '#18202B', accent: '#2E5BD8' },
    })
  })

  it('defaults to Mono in System mode', () => {
    expect(OZEL_DEFAULTS).toMatchObject({ preset: 'mono', mode: 'system', font: 'geist-mono', read: 'ui' })
    expect(loadOzel(localStorage)).toEqual(OZEL_DEFAULTS)
  })

  it('every preset reads in both modes (no guard on a preset)', () => {
    for (const p of Object.values(OZEL_PRESETS)) {
      expect(isReadable(p.dark.fg, p.dark.bg)).toBe(true)
      expect(isReadable(p.light.fg, p.light.bg)).toBe(true)
    }
  })
})

describe('contrast and flags', () => {
  it('computes WCAG contrast on known pairs', () => {
    expect(contrast('#000000', '#FFFFFF')).toBeCloseTo(21, 5)
    expect(contrast('#FFFFFF', '#FFFFFF')).toBeCloseTo(1, 5)
    expect(contrast('#767676', '#FFFFFF')).toBeCloseTo(4.54, 2)
    expect(contrast('#777777', '#888888')).toBeLessThan(1.5)
  })

  it('Mono dark: dark status set, accent stands in by the foreground', () => {
    expect(ozelFlags(OZEL_PRESETS.mono.dark)).toEqual({ tone: 'dark', mid: false, acc: 'off', bad: false })
  })

  it('Kâğıt light: light status set, ink accent and status words read in full', () => {
    expect(ozelFlags(OZEL_PRESETS.kagit.light)).toEqual({ tone: 'light', mid: false, acc: 'full', bad: false })
  })

  it('all six presets retain status hues, readable on every text surface', () => {
    for (const preset of Object.values(OZEL_PRESETS)) {
      for (const tone of ['light', 'dark'] as const) {
        const p = preset[tone]
        expect(ozelFlags(p)).toMatchObject({ tone, mid: false, bad: false })
        const srgb = (fraction: number) => '#' + [1, 3, 5].map(i =>
          Math.round(parseInt(p.fg.slice(i, i + 2), 16) * fraction + parseInt(p.bg.slice(i, i + 2), 16) * (1 - fraction))
            .toString(16).padStart(2, '0')).join('')
        const surfaces = [p.bg, ...(tone === 'light'
          ? [0.035, 0.06, 0.10].map(f => mixOklab(p.fg, p.bg, f))
          : [0.07, 0.09, 0.13].map(srgb))]
        expect(readability(p.fg, p.bg)).toEqual({
          main: Math.min(...surfaces.map(s => contrast(p.fg, s))),
          faint: Math.min(...surfaces.map(s => contrast(mixOklab(p.fg, p.bg, 0.72), s))),
        })
        for (const color of Object.values(OZEL_STATUS[tone])) {
          for (const surface of surfaces) expect(contrast(color, surface), `${tone}: ${color} on ${surface}`).toBeGreaterThanOrEqual(4.5)
        }
      }
    }
  })

  it('rejects dark text that passed on the old OKLab surfaces but fails on the srgb active row', () => {
    const fg = '#BABABA', bg = '#101010', faint = mixOklab(fg, bg, 0.72)
    expect(Math.min(...[0, 0.06, 0.10].map(f => contrast(faint, mixOklab(fg, bg, f))))).toBeGreaterThanOrEqual(4.5)
    expect(ozelFlags({ fg, bg, accent: fg }).tone).toBe('dark')
    expect(readability(fg, bg).faint).toBeLessThan(4.5)
    expect(isReadable(fg, bg)).toBe(false)
  })

  it('falls back to ink when a custom light active row makes a status colour unreadable', () => {
    const p = { bg: '#F0F0F0', fg: '#000000', accent: '#000000' }
    expect(contrast(OZEL_STATUS.light.wait, mixOklab(p.fg, p.bg, 0.06))).toBeGreaterThanOrEqual(4.5)
    expect(contrast(OZEL_STATUS.light.wait, mixOklab(p.fg, p.bg, 0.10))).toBeLessThan(4.5)
    expect(ozelFlags(p)).toMatchObject({ tone: 'light', mid: true })
  })

  it('a mid grey ground (#808080 / #FFFFFF) has no readable status set and fails the guard', () => {
    const f = ozelFlags({ bg: '#808080', fg: '#FFFFFF', accent: '#FFFFFF' })
    expect(f.mid).toBe(true)
    expect(f.bad).toBe(true)
  })

  it('a neon accent on black stands off in full', () => {
    expect(ozelFlags({ bg: '#000000', fg: '#FFFFFF', accent: '#39FF14' }).acc).toBe('full')
  })

  it('a dim accent that only reaches 3:1 is a mark', () => {
    const f = ozelFlags({ bg: '#000000', fg: '#FFFFFF', accent: '#666666' })
    expect(contrast('#666666', '#000000')).toBeGreaterThanOrEqual(3)
    expect(f.acc).toBe('mark')
  })
})

describe('the readability fix', () => {
  it('moves the text colour only when that is enough, and lands >= 4.5 for every text pair', () => {
    // the mockup's guard shot (ozel-gorunum-uyari): bg #1A1A1A, fg #5A5A5A
    const before = { bg: '#1A1A1A', fg: '#5A5A5A', accent: '#123456' }
    expect(isReadable(before.fg, before.bg)).toBe(false)
    const after = fixReadable(before)
    expect(after.bg).toBe('#1A1A1A')
    expect(after.accent).toBe('#123456')
    expect(after.fg).not.toBe(before.fg)
    const r = readability(after.fg, after.bg)
    expect(r.main).toBeGreaterThanOrEqual(4.5)
    expect(r.faint).toBeGreaterThanOrEqual(4.5)
  })

  it.each([
    ['#888888', '#777777'],
    ['#808080', '#FFFFFF'],
  ])('on a mid grey ground (%s) the text goes to the far extreme first, then the ground moves away', (bg, fg) => {
    const after = fixReadable({ bg, fg, accent: '#000000' })
    // both grounds are lighter than luminance 0.179, so the far extreme is black
    expect(after.fg).toBe('#000000')
    expect(after.bg).not.toBe(bg)
    expect(isReadable(after.fg, after.bg)).toBe(true)
  })
})

describe('theme text codec', () => {
  it('round-trips a palette and font', () => {
    const s = presetSettings('gece', 'dark')
    const txt = themeText(s, true)
    expect(txt).toBe('gm-tema:1;ad=Gece;bg=#0D1117;fg=#D6DEE8;vurgu=#7AA2F7;yazi=inter')
    expect(parseThemeText(txt)).toEqual({
      ok: true, value: { name: 'Gece', bg: '#0D1117', fg: '#D6DEE8', accent: '#7AA2F7', font: 'inter' },
    })
  })

  it('accepts an unknown preset name (the removed Kömür) as plain colours, and short hex', () => {
    const r = parseThemeText('gm-tema:1;ad=Kömür;bg=#1c1c1c;fg=EEE;vurgu=#39ff14', 'plex-mono')
    expect(r).toEqual({ ok: true, value: { name: 'Kömür', bg: '#1C1C1C', fg: '#EEEEEE', accent: '#39FF14', font: 'plex-mono' } })
  })

  it('rejects malformed text without touching the live theme or storage', () => {
    setOzel(presetSettings('kagit'))
    const stored = localStorage.getItem(OZEL_STORAGE_KEY)
    const before = JSON.stringify(getOzel())
    const bad = [
      ['', 'empty'],
      ['ad=Mono;bg=#000000', 'prefix'],
      ['gm-tema:1;bg=#000000;fg', 'pair'],
      ['gm-tema:1;bg=#000000', 'missing'],
      ['gm-tema:1;bg=#000000;fg=#FFFFFF;vurgu=#39FF1G', 'color'],
      ['gm-tema:1;bg=#000000;fg=#FFFFFF;vurgu=#39FF14;yazi=comic-sans', 'font'],
    ] as const
    for (const [txt, code] of bad) {
      const r = parseThemeText(txt)
      expect(r.ok).toBe(false)
      expect((r as { error: { code: string } }).error.code).toBe(code)
    }
    expect((parseThemeText('gm-tema:1;bg=#000000;fg=#FFFFFF;vurgu=#39FF1G') as { error: unknown }).error)
      .toEqual({ code: 'color', field: 'vurgu', value: '#39FF1G' })
    expect(JSON.stringify(getOzel())).toBe(before)
    expect(localStorage.getItem(OZEL_STORAGE_KEY)).toBe(stored)
  })

  it('normalises hex input', () => {
    expect(normHex('#abc')).toBe('#AABBCC')
    expect(normHex('39ff14')).toBe('#39FF14')
    expect(normHex('#39FF1G')).toBeNull()
    expect(normHex('')).toBeNull()
  })
})

describe('persistence', () => {
  it('validates per field: a removed preset keeps its colours, bad values fall back', () => {
    localStorage.setItem(OZEL_STORAGE_KEY, JSON.stringify({
      preset: 'komur', name: 'Kömür', mode: 'dark', font: 'wingdings', read: 'read',
      dark: { bg: '#1C1C1C', fg: 'nope', accent: '#39FF14' }, light: null,
    }))
    expect(loadOzel(localStorage)).toEqual({
      preset: null, name: 'Kömür', mode: 'dark', font: OZEL_DEFAULTS.font, read: 'read',
      dark: { bg: '#1C1C1C', fg: OZEL_DEFAULTS.dark.fg, accent: '#39FF14' }, light: OZEL_DEFAULTS.light,
    })
  })

  it('tolerates broken JSON and refusing storage', () => {
    localStorage.setItem(OZEL_STORAGE_KEY, '{not json')
    expect(loadOzel(localStorage)).toEqual(OZEL_DEFAULTS)
    const denied = { getItem: () => { throw new Error('denied') }, setItem: () => { throw new Error('denied') } }
    expect(loadOzel(denied)).toEqual(OZEL_DEFAULTS)
    expect(() => saveOzel(OZEL_DEFAULTS, denied)).not.toThrow()
  })

  it('survives a reload of the module', async () => {
    setOzel({ ...presetSettings('gece', 'dark'), preset: null, name: 'Benim', dark: { bg: '#101010', fg: '#F0F0F0', accent: '#39FF14' } })
    vi.resetModules()
    const fresh = await import('../renderer/lib/ozelTheme')
    expect(fresh.getOzel()).toMatchObject({ preset: null, name: 'Benim', mode: 'dark', dark: { bg: '#101010', fg: '#F0F0F0', accent: '#39FF14' } })
  })
})

describe('applying Özel on <html>', () => {
  it('lists Özel last among the themes', () => {
    expect(THEMES).toEqual(['arena', 'sade', 'pafta', 'atolye', 'ozel'])
  })

  it('writes the variables and flags for ozel and removes all of them when switching back to arena', () => {
    setOzel({ ...presetSettings('mono', 'dark') })
    applyAppearance({ ...DEFAULTS, theme: 'ozel', readingFont: 'inter', codeFont: 'fira' }, root)
    expect(root.dataset.theme).toBe('ozel')
    expect(root.style.getPropertyValue('--u-bg')).toBe('#000000')
    expect(root.style.getPropertyValue('--u-fg')).toBe('#FFFFFF')
    expect(root.style.getPropertyValue('--u-accent')).toBe('#000000')
    expect(root.getAttribute('data-u-tone')).toBe('dark')
    expect(root.getAttribute('data-u-acc')).toBe('off')
    expect(root.hasAttribute('data-u-mono')).toBe(true)
    expect(root.hasAttribute('data-u-mid')).toBe(false)
    expect(root.hasAttribute('data-u-bad')).toBe(false)
    // Özel derives the font roles itself: no inline --font-body / --font-mono, the picks feed --u-font-*
    expect(root.style.getPropertyValue('--font-body')).toBe('')
    expect(root.style.getPropertyValue('--font-mono')).toBe('')
    expect(root.style.getPropertyValue('--u-font-ui')).toBe('"Geist Mono", ui-monospace, monospace')
    expect(root.style.getPropertyValue('--u-font-read')).toBe('"Geist Mono", ui-monospace, monospace')
    expect(root.style.getPropertyValue('--u-font-code')).toBe(FONT_STACKS.fira)

    applyAppearance({ ...DEFAULTS, theme: 'arena', readingFont: 'inter' }, root)
    expect(root.dataset.theme).toBe('arena')
    for (const p of U_PROPS) expect(root.style.getPropertyValue(p)).toBe('')
    for (const a of U_ATTRS) expect(root.hasAttribute(a)).toBe(false)
    expect(root.style.getPropertyValue('--font-body')).toBe(FONT_STACKS.inter)
  })

  it('leaves no Özel trace on any of the four character themes', () => {
    setOzel({ ...presetSettings('gece', 'dark'), dark: { bg: '#808080', fg: '#FFFFFF', accent: '#39FF14' } })
    for (const theme of ['arena', 'sade', 'pafta', 'atolye'] as const) {
      applyAppearance({ ...DEFAULTS, theme: 'ozel' }, root)
      expect(root.style.length).toBeGreaterThan(0)
      applyAppearance({ ...DEFAULTS, theme }, root)
      expect(root.style.length).toBe(0)
      expect(root.getAttributeNames().filter(n => n.startsWith('data-u-'))).toEqual([])
    }
    // An Özel change while a character theme is shown paints nothing.
    setOzel(presetSettings('kagit'))
    expect(root.style.length).toBe(0)
  })

  it('"Okuma yazı tipi" puts answers in the reading font and drops the mono measure', () => {
    setOzel({ ...presetSettings('mono', 'dark'), read: 'read' })
    applyAppearance({ ...DEFAULTS, theme: 'ozel', readingFont: 'plex-sans' }, root)
    expect(root.style.getPropertyValue('--u-font-read')).toBe(FONT_STACKS['plex-sans'])
    expect(root.hasAttribute('data-u-mono')).toBe(false)
  })

  it('repaints live when the settings change', () => {
    applyAppearance({ ...DEFAULTS, theme: 'ozel' }, root)
    setOzel({ ...getOzel(), mode: 'dark', preset: null, dark: { bg: '#888888', fg: '#777777', accent: '#000000' } })
    expect(root.style.getPropertyValue('--u-bg')).toBe('#888888')
    expect(root.hasAttribute('data-u-bad')).toBe(true)
  })

  it('Mode Sistem follows prefers-color-scheme live', () => {
    const scheme = stubScheme(false)
    setOzel(presetSettings('gece', 'system'))
    applyAppearance({ ...DEFAULTS, theme: 'ozel' }, root)
    expect(root.style.getPropertyValue('--u-bg')).toBe('#F5F7FA')
    expect(root.getAttribute('data-u-tone')).toBe('light')
    scheme.set(true)
    expect(root.style.getPropertyValue('--u-bg')).toBe('#0D1117')
    expect(root.getAttribute('data-u-tone')).toBe('dark')
    // A fixed mode ignores the system.
    setOzel({ ...getOzel(), mode: 'light' })
    scheme.set(false)
    scheme.set(true)
    expect(root.style.getPropertyValue('--u-bg')).toBe('#F5F7FA')
  })

  it('removes the system listener on exit and adds exactly one on re-entry', () => {
    const scheme = stubScheme(false)
    setOzel(presetSettings('mono', 'system'))
    applyAppearance({ ...DEFAULTS, theme: 'ozel' }, root)
    expect(scheme.listeners.size).toBe(1)
    const consumer = renderHook(() => useOzel())
    clearOzel(root)
    expect(scheme.listeners.size).toBe(0)
    applyAppearance({ ...DEFAULTS, theme: 'ozel' }, root)
    applyAppearance({ ...DEFAULTS, theme: 'ozel' }, root)
    expect(scheme.listeners.size).toBe(1)
    consumer.unmount()
  })

  it('keeps one listener until the last useOzel consumer unmounts', () => {
    const scheme = stubScheme(false)
    applyAppearance({ ...DEFAULTS, theme: 'ozel' }, root)
    const first = renderHook(() => useOzel()), second = renderHook(() => useOzel())
    expect(scheme.listeners.size).toBe(1)
    first.unmount()
    expect(scheme.listeners.size).toBe(1)
    second.unmount()
    expect(scheme.listeners.size).toBe(0)
    applyAppearance({ ...DEFAULTS, theme: 'ozel' }, root)
    expect(scheme.listeners.size).toBe(1)
  })
})

describe('theme-ozel CSS stays inside Özel', () => {
  it('keeps status hexes in CSS and the contrast model in sync', () => {
    const css = readFileSync(resolve(__dirname, '../renderer/styles/gm/theme-ozel.shell.css'), 'utf8')
    const light = css.match(/:root\[data-theme="ozel"\]\[data-u-tone="light"\]\s*\{([^}]+)\}/)![1]
    const dark = css.match(/\/\* ================= status:[\s\S]+?:root\[data-theme="ozel"\]\s*\{([^}]+)\}/)![1]
    for (const tone of ['light', 'dark'] as const) {
      for (const [status, color] of Object.entries(OZEL_STATUS[tone])) {
        expect(tone === 'light' ? light : dark).toContain(`--st-${status}: ${color}`)
      }
    }
  })

  it('uses status red/warning and the user accent for the specified signal roles', () => {
    const shell = readFileSync(resolve(__dirname, '../renderer/styles/gm/theme-ozel.shell.css'), 'utf8')
    const thread = readFileSync(resolve(__dirname, '../renderer/styles/gm/theme-ozel.thread.css'), 'utf8')
    for (const rule of [
      '.side-menu-item.is-danger { color: var(--st-err-text); }',
      '.status-err { background: var(--st-err); }',
      '.oz-guard { border-color: var(--st-warn); }',
      '.oz-guard > .ic { color: var(--st-warn-text); }',
      '.oz-st-i[data-st="err"] i { background: var(--oz-err); }',
    ]) expect(shell).toContain(`[data-theme="ozel"] ${rule}`)
    expect(shell).toContain('--use-hot: var(--st-warn)')
    expect(shell).toContain('--tour-ring: var(--oz-acc)')
    expect(shell).toContain('--tour-dot-on: var(--oz-acc)')
    expect(thread).toContain('[data-theme="ozel"] .composer:focus-within { border-color: var(--oz-acc); }')
    expect(shell.match(/:root\[data-theme="ozel"\]\[data-u-tone="dark"\]\s*\{([^}]+)\}/)![1]).toContain('--f-bg: var(--oz-2)')
    const allToneBlocks = [...shell.matchAll(/:root\[data-theme="ozel"\]\s*\{([^}]+)\}/g)].map(m => m[1]).join('\n')
    expect(allToneBlocks).toContain('--f-bg: var(--oz-1)')
    expect(allToneBlocks).not.toContain('--f-bg: var(--oz-2)')
  })

  /** Every style-rule selector (keyframe steps excluded) of a stylesheet. */
  function selectors(css: string): string[] {
    const out: string[] = []
    const text = css.replace(/\/\*[\s\S]*?\*\//g, '')
    const stack: string[] = []
    let start = 0
    for (let i = 0; i < text.length; i++) {
      const c = text[i]
      if (c === '{') {
        const prelude = text.slice(start, i).trim()
        const inKeyframes = stack.some(p => p.startsWith('@keyframes'))
        if (!prelude.startsWith('@') && !inKeyframes) out.push(prelude)
        stack.push(prelude)
        start = i + 1
      } else if (c === '}') {
        stack.pop()
        start = i + 1
      } else if (c === ';' && stack.length === 0) {
        start = i + 1
      }
    }
    return out
  }

  it.each(['theme-ozel.shell.css', 'theme-ozel.thread.css', 'theme-ozel.workspace.css'])('%s scopes every selector to [data-theme="ozel"]', file => {
    const css = readFileSync(resolve(__dirname, '../renderer/styles/gm', file), 'utf8')
    const sels = selectors(css).flatMap(s => s.split(','))
    expect(sels.length).toBeGreaterThan(0)
    for (const s of sels) expect(s, s).toContain('[data-theme="ozel"]')
    expect(css).not.toContain('fonts.googleapis.com')
  })

  it('is imported by the app after every base stylesheet', () => {
    const app = readFileSync(resolve(__dirname, '../renderer/pages/_app.tsx'), 'utf8')
    const imports = [...app.matchAll(/import '\.\.\/styles\/gm\/([^']+)'/g)].map(m => m[1])
    expect(imports.slice(-3)).toEqual(['theme-ozel.shell.css', 'theme-ozel.thread.css', 'theme-ozel.workspace.css'])
  })
})
