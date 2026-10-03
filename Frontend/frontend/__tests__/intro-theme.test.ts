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

  it('Özel draws the intro in the user colours, never the status orange', () => {
    const rule = noComments(gm('theme-ozel.shell.css')).match(/\[data-theme="ozel"\] \.intro-overlay \{[^}]*\}/)?.[0] ?? ''
    expect(rule).toMatch(/--intro-accent: var\(--oz-acc\)/)
    expect(rule).toMatch(/--intro-accent-ink: var\(--oz-on-acc\)/)
    expect(rule).not.toMatch(/--accent\b|--st-/)
  })
})
