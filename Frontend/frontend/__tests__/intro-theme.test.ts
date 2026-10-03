import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const gm = (file: string) => readFileSync(resolve(__dirname, '../renderer/styles/gm', file), 'utf8')
const noComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '')

// The palette block is the only `.intro-overlay { ... }` rule that declares --intro-accent.
const PALETTE = /(^|\n)\.intro-overlay\s*\{[^}]*--intro-accent:[^}]*\}/

describe('intro palette (owner, 3 Oct: the intro followed Arena colours in every theme)', () => {
  const css = noComments(gm('intro.css'))
  const palette = css.match(PALETTE)?.[0] ?? ''

  it('keeps every literal colour inside the palette block', () => {
    expect(palette).not.toBe('')
    // Attribute selectors name the approved markup's own colours ([fill="#FF6B3D"]); they are matched, not painted.
    const rest = css.replace(PALETTE, '$1').replace(/\[[^\]]*\]/g, '[]')
    expect(rest.match(/#[0-9a-f]{3,8}\b/gi) ?? []).toEqual([])
    expect(rest.match(/\b(rgba?|hsla?)\(/gi) ?? []).toEqual([])
  })

  it('never reads the app accent, which is a status colour in Özel', () => {
    expect(css).not.toMatch(/var\(--accent[)-]/)
  })

  it('declares every --intro-* colour token it uses', () => {
    // --intro-fit is the stage scale IntroOverlay sets, not a colour.
    const used = new Set([...css.matchAll(/var\((--intro-[a-z0-9-]+)/g)].map(m => m[1]).filter(t => t !== '--intro-fit'))
    const declared = new Set([...palette.matchAll(/(--intro-[a-z0-9-]+):/g)].map(m => m[1]))
    expect(used.size).toBeGreaterThan(5)
    for (const token of used) expect(declared.has(token), token).toBe(true)
  })

  it.each(['sade', 'pafta', 'atolye', 'ozel'])('%s gives the intro its own palette', theme => {
    const shell = noComments(gm(`theme-${theme}.shell.css`))
    const rule = shell.match(new RegExp(`\\[data-theme="${theme}"\\] \\.intro-overlay \\{[^}]*\\}`))?.[0] ?? ''
    expect(rule).toContain('--intro-accent:')
    expect(rule).toContain('--intro-line:')
    expect(rule).toContain('--intro-shadow:')
  })

  it('reads the power colour through --intro-energy, which defaults to exactly var(--energy)', () => {
    // Arena (and any theme that does not set it) keeps its own --energy unchanged.
    expect(palette).toMatch(/--intro-energy: var\(--energy\);/)
    expect(css.replace(PALETTE, '$1')).not.toMatch(/var\(--energy\)/)
  })

  // WCAG relative luminance of a #rrggbb colour.
  const lum = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map(c => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const contrast = (a: string, b: string) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
    return (hi + 0.05) / (lo + 0.05)
  }
  // Last literal value of a token across the theme's :root[data-theme] blocks in tokens.css.
  const themeToken = (theme: string, token: string) => {
    const tokens = noComments(gm('tokens.css'))
    const blocks = [...tokens.matchAll(new RegExp(`:root\\[data-theme="${theme}"\\] \\{[^}]*\\}`, 'g'))].map(m => m[0])
    const values = blocks.flatMap(b => [...b.matchAll(new RegExp(`${token}:\\s*(#[0-9a-fA-F]{6})\\b`, 'g'))].map(m => m[1]))
    return values.at(-1) ?? ''
  }

  // Özel is left out: its ground and --energy are both mixed from the user's own two colours.
  it.each(['arena', 'sade', 'pafta', 'atolye'])('%s draws the intro power text at 4.5:1 or more on its intro ground', theme => {
    const shell = theme === 'arena' ? '' : noComments(gm(`theme-${theme}.shell.css`))
    const rule = shell.match(new RegExp(`\\[data-theme="${theme}"\\] \\.intro-overlay \\{[^}]*\\}`))?.[0] ?? ''
    // These themes leave --intro-bg at its default, var(--shell-bg).
    expect(rule).not.toContain('--intro-bg:')
    const ground = themeToken(theme, '--shell-bg')
    const energy = rule.match(/--intro-energy:\s*(#[0-9a-fA-F]{6})\b/)?.[1] ?? themeToken(theme, '--energy')
    expect(ground, 'ground').toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(energy, 'energy').toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(contrast(energy, ground)).toBeGreaterThanOrEqual(4.5)
  })

  it('Özel draws the intro in the user colours, never the status orange', () => {
    const rule = noComments(gm('theme-ozel.shell.css')).match(/\[data-theme="ozel"\] \.intro-overlay \{[^}]*\}/)?.[0] ?? ''
    expect(rule).toMatch(/--intro-accent: var\(--oz-acc\)/)
    expect(rule).toMatch(/--intro-accent-ink: var\(--oz-on-acc\)/)
    expect(rule).not.toMatch(/--accent\b|--st-/)
  })
})
