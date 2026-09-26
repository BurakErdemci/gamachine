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
