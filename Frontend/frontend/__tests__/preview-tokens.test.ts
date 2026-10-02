/**
 * The 3D stage takes its ground, grid and mannequin colours from the preview tokens instead of a
 * fixed near-black, so the paper themes get a paper stage (P3).
 */
import { describe, it, expect, afterEach } from 'vitest'
import { previewColors } from '../renderer/components/model-viewer/ModelPreviewPanel'

const root = document.documentElement
afterEach(() => { for (const k of ['--pv-bg', '--pv-grid', '--pv-ink']) root.style.removeProperty(k) })

describe('previewColors', () => {
  it('falls back to Arena without a stylesheet', () => {
    expect(previewColors().background).toBe('#141925')
  })

  it("reads Pafta's drafting stage from the tokens", () => {
    root.style.setProperty('--pv-bg', '#EEEAE0')
    root.style.setProperty('--pv-grid', '#CDD5DB')
    root.style.setProperty('--pv-ink', '#4A5866')
    const c = previewColors()
    expect(c.background).toBe('#eeeae0')
    expect(c.grid).toBe('#cdd5db')
    // the figure is the preview ink: dark on the light stage
    expect(c.figure).toBe('#4a5866')
    // the centre line sits between the ground and the ink
    expect(c.centerLine).not.toBe(c.background)
    expect(c.centerLine).not.toBe(c.figure)
  })
})
