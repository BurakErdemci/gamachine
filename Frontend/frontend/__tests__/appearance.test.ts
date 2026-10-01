import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { applyAppearance, applyZoom, DEFAULTS, FONT_STACKS, loadAppearance, saveAppearance } from '../renderer/lib/appearance'

beforeEach(() => { localStorage.clear() })
afterEach(() => {
  vi.unstubAllGlobals()
  applyAppearance(DEFAULTS)
})

describe('appearance preferences', () => {
  it('defaults with empty storage', () => {
    expect(loadAppearance(localStorage)).toEqual({ theme: 'arena', readingFont: 'theme', codeFont: 'theme', textSize: 'normal', intro: true })
  })

  it('invalid values fall back per key while valid values survive', () => {
    localStorage.setItem('app-theme', 'atolye')
    localStorage.setItem('app-font-reading', 'unknown')
    localStorage.setItem('app-font-code', 'fira')
    localStorage.setItem('app-text-size', 'huge')
    localStorage.setItem('app-intro', 'false')
    expect(loadAppearance(localStorage)).toEqual({ ...DEFAULTS, theme: 'atolye', codeFont: 'fira' })
  })

  it('a throwing storage gives all defaults and saving never throws', () => {
    const storage = { getItem: vi.fn(() => { throw new Error('denied') }), setItem: vi.fn(() => { throw new Error('denied') }) }
    expect(loadAppearance(storage)).toEqual(DEFAULTS)
    expect(() => saveAppearance(DEFAULTS, storage)).not.toThrow()
    expect(storage.setItem).toHaveBeenCalledTimes(5)
  })

  it('discards partial reads when a later storage access throws', () => {
    const storage = { getItem: vi.fn().mockReturnValueOnce('pafta').mockImplementation(() => { throw new Error('denied') }) }
    expect(loadAppearance(storage)).toEqual(DEFAULTS)
  })

  it('round trips every preference', () => {
    const appearance = { theme: 'pafta', readingFont: 'plex-sans', codeFont: 'geist-mono', textSize: 'small', intro: false } as const
    saveAppearance(appearance, localStorage)
    expect(localStorage.getItem('app-intro')).toBe('off')
    expect(loadAppearance(localStorage)).toEqual(appearance)
  })

  it('sets the theme and font overrides and removes overrides for theme defaults', () => {
    const root = document.documentElement
    applyAppearance({ ...DEFAULTS, theme: 'sade', readingFont: 'inter', codeFont: 'fira' }, root)
    expect(root.dataset.theme).toBe('sade')
    expect(root.style.getPropertyValue('--font-body')).toBe(FONT_STACKS.inter)
    expect(root.style.getPropertyValue('--font-mono')).toBe(FONT_STACKS.fira)
    applyAppearance(DEFAULTS, root)
    expect(root.style.getPropertyValue('--font-body')).toBe('')
    expect(root.style.getPropertyValue('--font-mono')).toBe('')
  })

  it('zoom is safe without Electron and passes the numeric factor when available', () => {
    vi.stubGlobal('ipc', undefined)
    expect(() => applyZoom('large')).not.toThrow()
    const invoke = vi.fn().mockResolvedValue(true)
    vi.stubGlobal('ipc', { invoke })
    applyZoom('large')
    expect(invoke).toHaveBeenCalledWith('app-zoom-set', 1.1)
  })

  it('ignores synchronous and asynchronous Electron failures', async () => {
    vi.stubGlobal('ipc', { invoke: vi.fn(() => { throw new Error('closed') }) })
    expect(() => applyZoom('normal')).not.toThrow()
    vi.stubGlobal('ipc', { invoke: vi.fn().mockRejectedValue(new Error('closed')) })
    applyZoom('small')
    await Promise.resolve()
  })
})
