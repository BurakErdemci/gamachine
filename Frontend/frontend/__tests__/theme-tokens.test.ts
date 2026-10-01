import { describe, it, expect, afterEach } from 'vitest'
import {
  isLight, luminance, onThemeChange, parseComputedColor, readColorToken, readColorTokens,
} from '../renderer/lib/themeTokens'

describe('theme token reader', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.style.removeProperty('--ed-bg')
  })

  it('parses the colour forms a browser computes', () => {
    expect(parseComputedColor('#141925')).toBe('#141925')
    expect(parseComputedColor('#FFF')).toBe('#ffffff')
    expect(parseComputedColor('rgb(20, 25, 37)')).toBe('#141925')
    expect(parseComputedColor('rgba(240, 236, 227, 0.5)')).toBe('#f0ece3')
    // what Chromium returns for color-mix(in srgb …)
    expect(parseComputedColor('color(srgb 1 0.5 0)')).toBe('#ff8000')
    expect(parseComputedColor('var(--nope)')).toBe('')
  })

  it('tells a paper editor from a dark one', () => {
    expect(isLight('#F0ECE3'.toLowerCase())).toBe(true) // Pafta's editor sheet
    expect(isLight('#141925')).toBe(false) // Arena's editor plate
    expect(luminance('#ffffff')).toBeCloseTo(1)
  })

  it('reads a token set on :root and falls back when it is missing', () => {
    document.documentElement.style.setProperty('--ed-bg', '#123456')
    expect(readColorToken('--ed-bg')).toBe('#123456')
    expect(readColorTokens({ '--ed-bg': '#000000', '--missing': '#abcdef' })).toEqual({
      '--ed-bg': '#123456', '--missing': '#abcdef',
    })
  })

  it('reports a theme switch so Monaco, xterm and three can recolour without a reload', async () => {
    let calls = 0
    const stop = onThemeChange(() => { calls++ })
    document.documentElement.setAttribute('data-theme', 'pafta')
    await Promise.resolve()
    expect(calls).toBe(1)
    stop()
    document.documentElement.setAttribute('data-theme', 'sade')
    await Promise.resolve()
    expect(calls).toBe(1)
  })
})
