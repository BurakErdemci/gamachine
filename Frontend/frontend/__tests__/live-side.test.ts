import { describe, expect, it } from 'vitest'
import { createLiveSideState, shownSides, viewSide } from '../renderer/lib/liveSide'

describe('live side state', () => {
  it('starts on Bilgisayar with nothing running or picked', () => {
    expect(createLiveSideState().snapshot()).toEqual({ side: 'bilgisayar', manual: null, running: { bilgisayar: false, tarayici: false } })
  })

  it('pick sets the side and remembers the manual choice', () => {
    const s = createLiveSideState()
    s.pick('tarayici')
    expect(s.snapshot()).toMatchObject({ side: 'tarayici', manual: 'tarayici' })
  })

  it('setRunning flips one side and ignores a repeat', () => {
    const s = createLiveSideState()
    let n = 0
    s.subscribe(() => { n++ })
    s.setRunning('tarayici', true)
    s.setRunning('tarayici', true)
    expect(s.snapshot().running).toEqual({ bilgisayar: false, tarayici: true })
    expect(n).toBe(1)
  })

  it('autoReveal switches to the side and clears the manual pick', () => {
    const s = createLiveSideState()
    s.pick('bilgisayar')
    expect(s.autoReveal('tarayici')).toBe(true)
    expect(s.snapshot()).toMatchObject({ side: 'tarayici', manual: null })
  })

  it('autoReveal keeps a running hand-picked other side', () => {
    const s = createLiveSideState()
    s.setRunning('bilgisayar', true)
    s.pick('bilgisayar')
    expect(s.autoReveal('tarayici')).toBe(false)
    expect(s.snapshot()).toMatchObject({ side: 'bilgisayar', manual: 'bilgisayar' })
  })

  it('autoReveal takes over from a hand-picked side that is not running', () => {
    const s = createLiveSideState()
    s.pick('bilgisayar')
    expect(s.autoReveal('tarayici')).toBe(true)
    expect(s.snapshot().side).toBe('tarayici')
  })

  it('clears the manual pick when that side stops, so the next run switches', () => {
    const s = createLiveSideState()
    s.setRunning('bilgisayar', true)
    s.pick('bilgisayar')
    s.setRunning('bilgisayar', false)
    expect(s.snapshot().manual).toBeNull()
    expect(s.autoReveal('tarayici')).toBe(true)
  })

  it('reports true when the side is already shown', () => {
    const s = createLiveSideState()
    expect(s.autoReveal('bilgisayar')).toBe(true)
  })

  it('shows a side when its feature is on or it runs, and views the other when the chosen one is hidden', () => {
    const idle = { bilgisayar: false, tarayici: false }
    expect(shownSides({ computer: false, browser: false }, idle)).toEqual({ bilgisayar: false, tarayici: false })
    expect(shownSides({ computer: false, browser: false }, { ...idle, tarayici: true })).toEqual({ bilgisayar: false, tarayici: true })
    expect(viewSide('bilgisayar', { bilgisayar: false, tarayici: true })).toBe('tarayici')
    expect(viewSide('bilgisayar', { bilgisayar: true, tarayici: true })).toBe('bilgisayar')
    expect(viewSide('bilgisayar', { bilgisayar: false, tarayici: false })).toBe('bilgisayar')
  })
})
