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

  it('parses computed OKLab and OKLCH colours, ignoring alpha and clamping gamut', () => {
    for (const value of ['oklab(0.5 0 0)', 'oklab(50% 0 0 / 0.2)', 'oklch(50% 0 none)', 'oklch(0.5 0 120deg / 50%)']) {
      const hex = parseComputedColor(value)
      expect(hex).toMatch(/^#[0-9a-f]{6}$/)
      for (const channel of [1, 3, 5]) expect(Math.abs(parseInt(hex.slice(channel, channel + 2), 16) - 99)).toBeLessThanOrEqual(1)
    }
    expect(parseComputedColor('oklab(0.839694 -0.004317 -0.0153978)')).toBe('#c4cbd5')
    expect(parseComputedColor('oklch(0 0 0)')).toBe('#000000')
    expect(parseComputedColor('oklch(0.5 0.4 30)')).toBe('#fd0000')
    expect(parseComputedColor('oklab(1 0 0)')).toBe('#ffffff')
    expect(parseComputedColor('oklab(nope 0 0)')).toBe('')
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
