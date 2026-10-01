import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('bundled fonts and theme tokens', () => {
  it('has a local font file for every bundled font URL', () => {
    const css = readFileSync(resolve(__dirname, '../renderer/styles/gm/fonts.css'), 'utf8')
    const urls = [...css.matchAll(/url\(\s*['"]?\/fonts\/([^)'"\s]+)['"]?\s*\)/g)]
    expect(urls.length).toBe(40)
    for (const [, filename] of urls) {
      expect(existsSync(resolve(__dirname, '../renderer/public/fonts', filename)), filename).toBe(true)
    }
  })

  it('does not request Google Fonts from global CSS', () => {
    const css = readFileSync(resolve(__dirname, '../renderer/styles/globals.css'), 'utf8')
    expect(css).not.toContain('fonts.googleapis.com')
    expect(css).not.toContain('fonts.gstatic.com')
  })

  it.each(['arena', 'sade', 'pafta', 'atolye'])('defines tokens for %s', theme => {
    const css = readFileSync(resolve(__dirname, '../renderer/styles/gm/tokens.css'), 'utf8')
    expect(css).toContain(`:root[data-theme="${theme}"]`)
  })
})
