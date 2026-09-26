// @vitest-environment node
/**
 * The "awaiting approval" dot was in the DOM but invisible (owner report,
 * 26 Sep 2026). `tailwind.config.js` replaces the whole colour palette and
 * `amber` was not in it, so `bg-amber-400` produced no CSS: a transparent 6px
 * span. Every DOM test was green because the element existed. The status
 * classes also live in `lib/convFamily.ts`, which the content globs did not
 * scan. This compiles the real config and looks for the rules themselves.
 */
import { describe, it, expect } from 'vitest'
import path from 'node:path'
import { createRequire } from 'node:module'
import postcss from 'postcss'
import tailwind from 'tailwindcss'
import { STATUS_DOT } from '../renderer/lib/convFamily'

const root = path.resolve(__dirname, '..')
const require_ = createRequire(import.meta.url)
const config = require_(path.join(root, 'renderer/tailwind.config.js'))

// Globs are cwd-relative, and the app is built from the frontend root.
const compile = async () => {
  const content = (config.content as string[]).map(g => path.resolve(root, g))
  const out = await postcss([tailwind({ ...config, content })]).process('@tailwind utilities;', { from: undefined })
  return out.css
}

const hasRule = (css: string, cls: string) =>
  new RegExp(`\\.${cls.replace(/[.:/[\]]/g, m => `\\${m}`)}\\s*\\{`).test(css)

describe('status dot colours are generated', () => {
  it('amber is in the palette', () => {
    expect(Object.keys(config.theme.colors)).toContain('amber')
  })

  it('every STATUS_DOT class has a CSS rule', async () => {
    const css = await compile()
    const classes = Object.values(STATUS_DOT).flatMap(d => d.className.split(/\s+/))
    const missing = classes.filter(c => !hasRule(css, c))
    expect(missing).toEqual([])
  }, 30000)
})

// The same bug hid more than the dot: rose (file delete/create cards), sky,
// neutral, fuchsia, teal and pink were used in components but never
// generated. Any colour family a source file names must be in the palette.
describe('every colour family used in the renderer is in the palette', () => {
  const srcRoot = path.join(root, 'renderer')
  const fs = require_('node:fs') as typeof import('node:fs')
  const families = Object.keys(require_('tailwindcss/colors')).filter(k => /^[a-z]+$/.test(k))
  const pattern = new RegExp(`-(${families.join('|')})-(?:50|[1-9]00|950)(?![0-9])`, 'g')

  const walk = (dir: string): string[] => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => {
    if (e.name.startsWith('.') || e.name === 'node_modules') return []
    const p = path.join(dir, e.name)
    return e.isDirectory() ? walk(p) : /\.(tsx?|jsx?)$/.test(e.name) ? [p] : []
  })

  it('no source file names a colour family the palette lacks', () => {
    const palette = new Set(Object.keys(config.theme.colors))
    const missing = new Map<string, string>()
    for (const file of walk(srcRoot)) {
      for (const m of fs.readFileSync(file, 'utf8').matchAll(pattern)) {
        if (!palette.has(m[1]) && !missing.has(m[1])) missing.set(m[1], path.relative(root, file))
      }
    }
    expect(Object.fromEntries(missing)).toEqual({})
  })
})
