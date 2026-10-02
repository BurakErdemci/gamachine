import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { INTRO_SAFETY_MS, IntroOverlay, pickIntroScene } from '../renderer/components/intro/IntroOverlay'
import { INTRO_MARKUP, renderIntroMarkup, type IntroTexts } from '../renderer/components/intro/introMarkup'
import { AppearanceContext, AppearanceProvider, DEFAULTS } from '../renderer/lib/appearance'
import { LangContext, ceviriUygula, type Lang } from '../renderer/lib/i18n'

const texts: IntroTexts = {
  skip: 'Geçmek için tıkla ya da Esc', quest: 'Arena hazır. İlk görevin seni bekliyor.', start: 'Başla',
  questTag: 'Görev', sprite: 'SPRITE · 14×12', loading: 'YÜKLENİYOR', ready: 'HAZIR',
  inserted: 'YERLEŞTİ', plus: '+1', brand: 'Gamachine',
}

const getAnimationsBefore = Object.getOwnPropertyDescriptor(document, 'getAnimations')

function mount(lang: Lang = 'en', strict = false) {
  const tree = (
    <LangContext.Provider value={{ lang, setLang: () => {}, t: (key, values) => ceviriUygula(lang, key, values) }}>
      <AppearanceProvider>
        <main className="app"><aside className="sidebar" /><textarea aria-label="Composer" /></main>
        <IntroOverlay />
      </AppearanceProvider>
    </LangContext.Provider>
  )
  return render(strict ? <React.StrictMode>{tree}</React.StrictMode> : tree)
}

function animationEnd(target: Element, name = 'i-end') {
  const event = new Event('animationend', { bubbles: true })
  Object.defineProperty(event, 'animationName', { value: name })
  fireEvent(target, event)
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })))
  Object.defineProperty(document, 'getAnimations', { configurable: true, value: vi.fn(() => []) })
})

afterEach(() => {
  cleanup()
  expect(vi.getTimerCount()).toBe(0)
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  if (getAnimationsBefore) Object.defineProperty(document, 'getAnimations', getAnimationsBefore)
  else Reflect.deleteProperty(document, 'getAnimations')
})

describe('intro scene rotation', () => {
  it('starts at scene 1 and records the pick', () => {
    expect(pickIntroScene()).toBe(1)
    expect(localStorage.getItem('app-intro-last')).toBe('1')
  })

  it('excludes the previous scene across 200 picks and can reach every other scene', () => {
    const random = vi.spyOn(Math, 'random')
    const seen = new Set<number>()
    for (let i = 0; i < 200; i++) {
      localStorage.setItem('app-intro-last', '3')
      random.mockReturnValue((i % 4) / 4)
      const scene = pickIntroScene()
      expect(scene).not.toBe(3)
      expect(scene).toBeGreaterThanOrEqual(1)
      expect(scene).toBeLessThanOrEqual(5)
      expect(localStorage.getItem('app-intro-last')).toBe(String(scene))
      seen.add(scene)
    }
    expect([...seen]).toEqual([1, 2, 4, 5])
  })

  it.each(['bad', '0', '6', '2.5'])('treats invalid stored scene %s as a first launch', value => {
    localStorage.setItem('app-intro-last', value)
    expect(pickIntroScene()).toBe(1)
  })

  it('tolerates denied storage reads and writes', () => {
    vi.spyOn(localStorage, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new Error('denied') })
    expect(pickIntroScene()).toBe(1)
  })
})

describe('intro markup', () => {
  it('copies exactly the approved overlay except for text placeholders', () => {
    const source = readFileSync(resolve(__dirname, 'fixtures/intro.approved.html'), 'utf8')
    const approved = source.split(/\r?\n/).slice(25, 136).join('\n')
    expect(renderIntroMarkup(texts)).toBe(approved)
    expect(INTRO_MARKUP).not.toContain('app-frame')
    expect(INTRO_MARKUP).not.toContain('id="scenes"')
  })

  it('escapes all HTML-sensitive characters without recursively replacing placeholders', () => {
    const markup = renderIntroMarkup({ ...texts, quest: '<script>"&\'</script>{{start}}' })
    expect(markup).toContain('&lt;script&gt;&quot;&amp;&#39;&lt;/script&gt;{{start}}')
    expect(markup).not.toContain('<script>')
  })
})

describe('IntroOverlay', () => {
  it('reads the stored off setting before the provider effect can apply it', () => {
    localStorage.setItem('app-intro', 'off')
    const root = document.documentElement
    const setAttribute = vi.spyOn(root, 'setAttribute')
    const view = mount()
    expect(view.container.querySelector('#ov')).toBeNull()
    expect(root.hasAttribute('data-intro')).toBe(false)
    expect(root.hasAttribute('data-scene')).toBe(false)
    expect(view.container.querySelector('.app')!.hasAttribute('inert')).toBe(false)
    expect(setAttribute.mock.calls.some(([name]) => name === 'data-intro')).toBe(false)
    expect(localStorage.getItem('app-intro-last')).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('also respects a disabled appearance context', () => {
    const view = render(
      <AppearanceContext.Provider value={{ appearance: { ...DEFAULTS, intro: false }, setAppearance: () => {} }}>
        <IntroOverlay />
      </AppearanceContext.Provider>,
    )
    expect(view.container.innerHTML).toBe('')
    expect(document.documentElement.hasAttribute('data-intro')).toBe(false)
  })

  it('does nothing for reduced motion', () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
    const view = mount()
    expect(view.container.querySelector('#ov')).toBeNull()
    expect(document.documentElement.hasAttribute('data-intro')).toBe(false)
    expect(localStorage.getItem('app-intro-last')).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('shows scene 1 with the English strings and starts the entrance immediately', () => {
    const view = mount()
    const root = document.documentElement
    expect(view.container.querySelector('#ov')).toBeTruthy()
    expect(view.container.querySelector('.intro-overlay')!.getAttribute('aria-hidden')).toBe('true')
    expect(root.dataset.intro).toBeDefined()
    expect(root.dataset.scene).toBe('1')
    expect(root.classList.contains('run')).toBe(true)
    expect(root.classList.contains('intro-run')).toBe(true)
    expect(view.container.querySelector('.pr-text')!.textContent).toBe('The arena is ready. Your first quest awaits.')
    expect(view.container.querySelector('.pr-btn')!.textContent).toBe('START')
    expect(view.container.querySelector('.s5-lbl')!.textContent).toBe('LOADING')
  })

  it('starts scene 2 entrance only after its 600 ms offset', () => {
    localStorage.setItem('app-intro-last', '1')
    vi.spyOn(Math, 'random').mockReturnValue(0)
    mount()
    const root = document.documentElement
    expect(root.dataset.scene).toBe('2')
    expect(root.classList.contains('run2')).toBe(true)
    expect(root.classList.contains('intro-run')).toBe(false)
    act(() => vi.advanceTimersByTime(599))
    expect(root.classList.contains('intro-run')).toBe(false)
    act(() => vi.advanceTimersByTime(1))
    expect(root.classList.contains('intro-run')).toBe(true)
  })

  it('renders the exact Turkish quest and translated scene labels', () => {
    const view = mount('tr')
    expect(view.container.querySelector('.pr-text')!.textContent).toBe(texts.quest)
    expect(view.container.querySelector('.pr-tag')!.textContent).toBe(texts.questTag)
    expect(view.container.querySelector('.s3-tag')!.textContent).toBe(texts.inserted)
    expect(view.container.querySelector('.pr-btn')!.textContent).toBe('BAŞLA')
  })

  it('does not restart or rotate again on a rerender', () => {
    const view = render(<IntroOverlay />)
    fireEvent.keyDown(window, { key: 'Escape' })
    view.rerender(<IntroOverlay />)
    expect(view.container.querySelector('#ov')).toBeNull()
    expect(localStorage.getItem('app-intro-last')).toBe('1')
  })

  it('keeps one pick and one listener when StrictMode replays mount effects', () => {
    const random = vi.spyOn(Math, 'random')
    const view = mount('en', true)
    expect(view.container.querySelectorAll('#ov')).toHaveLength(1)
    expect(localStorage.getItem('app-intro-last')).toBe('1')
    expect(random).not.toHaveBeenCalled()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(view.container.querySelector('#ov')).toBeNull()
  })

  it('makes the app inert, removes existing composer focus, and restores inert on skip', () => {
    const app = document.createElement('main')
    app.className = 'app'
    const composer = document.createElement('textarea')
    app.append(composer)
    document.body.append(app)
    composer.focus()
    expect(document.activeElement).toBe(composer)
    const view = render(<IntroOverlay />)
    expect(app.hasAttribute('inert')).toBe(true)
    expect(document.activeElement).not.toBe(composer)
    composer.focus()
    expect(document.activeElement).not.toBe(composer)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(view.container.querySelector('#ov')).toBeNull()
    expect(app.hasAttribute('inert')).toBe(false)
    // jsdom queues selection events when a textarea gains or loses focus.
    act(() => vi.advanceTimersByTime(0))
    app.remove()
  })

  it('restores a pre-existing inert attribute on natural completion', () => {
    const app = document.createElement('main')
    app.className = 'app'
    app.setAttribute('inert', 'existing')
    document.body.append(app)
    const view = render(<IntroOverlay />)
    animationEnd(view.container.querySelector('#ov')!)
    expect(app.getAttribute('inert')).toBe('existing')
    app.remove()
  })

  it('blocks every Next app sibling without making the overlay inert', () => {
    const next = document.createElement('div')
    next.id = '__next'
    document.body.append(next)
    const view = render(<><main /><div role="dialog" /><IntroOverlay /></>, { container: next })
    const wrapper = next.querySelector('.intro-overlay')!
    expect(wrapper.hasAttribute('inert')).toBe(false)
    expect(next.querySelector('main')!.hasAttribute('inert')).toBe(true)
    expect(next.querySelector('[role="dialog"]')!.hasAttribute('inert')).toBe(true)
    fireEvent.click(wrapper)
    expect(next.querySelector('main')!.hasAttribute('inert')).toBe(false)
    view.unmount()
    next.remove()
  })

  it('Escape finishes only overlay and app intro keyframes, including delayed entrance animations', () => {
    localStorage.setItem('app-intro-last', '1')
    vi.spyOn(Math, 'random').mockReturnValue(0)
    const view = mount()
    const overlayAnimation = { effect: { target: view.container.querySelector('.m-pos') }, animationName: 's2-hx', finish: vi.fn() }
    const entranceAnimation = { effect: { target: view.container.querySelector('.sidebar') }, animationName: 'intro-left', finish: vi.fn() }
    const unrelatedAnimation = { effect: { target: view.container.querySelector('.sidebar') }, animationName: 'run-pulse', finish: vi.fn() }
    const outsideAnimation = { effect: { target: document.body }, animationName: 'intro-left', finish: vi.fn() }
    const targetlessAnimation = { effect: null, finish: vi.fn() }
    vi.mocked(document.getAnimations).mockImplementation(() => {
      expect(document.documentElement.classList.contains('intro-run')).toBe(true)
      return [overlayAnimation, entranceAnimation, unrelatedAnimation, outsideAnimation, targetlessAnimation] as unknown as Animation[]
    })
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(overlayAnimation.finish).toHaveBeenCalledOnce()
    expect(entranceAnimation.finish).toHaveBeenCalledOnce()
    expect(unrelatedAnimation.finish).not.toHaveBeenCalled()
    expect(outsideAnimation.finish).not.toHaveBeenCalled()
    expect(targetlessAnimation.finish).not.toHaveBeenCalled()
    expect(view.container.querySelector('#ov')).toBeNull()
    expect(document.documentElement.classList.contains('intro-run')).toBe(true)
    act(() => vi.advanceTimersByTime(1000))
    expect(document.documentElement.hasAttribute('data-intro')).toBe(false)
  })

  it('removes the overlay when clicked, even if an animation cannot finish', () => {
    const view = mount()
    vi.mocked(document.getAnimations).mockReturnValue([
      { effect: { target: view.container.querySelector('.m-pos') }, finish: () => { throw new Error('cancelled') } },
    ] as unknown as Animation[])
    fireEvent.click(view.container.querySelector('.m-pos')!)
    expect(view.container.querySelector('#ov')).toBeNull()
    expect(view.container.querySelector('.app')!.hasAttribute('inert')).toBe(false)
  })

  it('ignores other animation ends and cleans root state after the entrance finishes', () => {
    const view = mount()
    const root = document.documentElement
    animationEnd(view.container.querySelector('.m-pos')!, 'i-end')
    animationEnd(view.container.querySelector('#ov')!, 'i-fade')
    expect(view.container.querySelector('#ov')).toBeTruthy()
    act(() => vi.advanceTimersByTime(7301))
    animationEnd(view.container.querySelector('#ov')!)
    expect(view.container.querySelector('#ov')).toBeNull()
    expect(root.hasAttribute('data-intro')).toBe(true)
    expect(view.container.querySelector('.app')!.hasAttribute('inert')).toBe(false)
    act(() => vi.advanceTimersByTime(999))
    expect(root.hasAttribute('data-intro')).toBe(true)
    act(() => vi.advanceTimersByTime(1))
    expect(root.hasAttribute('data-intro')).toBe(false)
    expect(root.hasAttribute('data-scene')).toBe(false)
    expect(root.classList.contains('run')).toBe(false)
    expect(root.classList.contains('intro-run')).toBe(false)
  })

  it('removes listeners, delayed timers and root attributes on unmount', () => {
    localStorage.setItem('app-intro-last', '1')
    vi.spyOn(Math, 'random').mockReturnValue(0)
    const removeWindow = vi.spyOn(window, 'removeEventListener')
    const removeDocument = vi.spyOn(document, 'removeEventListener')
    const view = mount()
    const wrapper = view.container.querySelector('.intro-overlay')!
    const removeOverlay = vi.spyOn(wrapper, 'removeEventListener')
    // scene 2's offset timer + the INTRO_SAFETY_MS dismissal timer
    expect(vi.getTimerCount()).toBe(2)
    view.unmount()
    expect(removeWindow).toHaveBeenCalledWith('keydown', expect.any(Function), true)
    expect(removeDocument).toHaveBeenCalledWith('focusin', expect.any(Function), true)
    expect(removeOverlay).toHaveBeenCalledWith('animationend', expect.any(Function))
    expect(removeOverlay).toHaveBeenCalledWith('click', expect.any(Function))
    expect(vi.getTimerCount()).toBe(0)
    expect(document.documentElement.hasAttribute('data-intro')).toBe(false)
    expect(document.documentElement.hasAttribute('data-scene')).toBe(false)
    act(() => vi.advanceTimersByTime(10000))
    expect(document.documentElement.classList.contains('intro-run')).toBe(false)
  })

  it('fits the 1600x1000 stage into the window', () => {
    vi.stubGlobal('innerWidth', 2560)
    vi.stubGlobal('innerHeight', 1343)
    const view = mount()
    const wrapper = view.container.querySelector<HTMLElement>('.intro-overlay')!
    expect(wrapper.style.getPropertyValue('--intro-fit')).toBe(String(1343 / 1000))
  })

  it('dismisses itself after INTRO_SAFETY_MS even when no scene ever ends', () => {
    const view = mount()
    expect(view.container.querySelector('#ov')).not.toBeNull()
    act(() => vi.advanceTimersByTime(INTRO_SAFETY_MS))
    expect(view.container.querySelector('#ov')).toBeNull()
    act(() => vi.advanceTimersByTime(1000))
    expect(document.documentElement.hasAttribute('data-intro')).toBe(false)
  })
})
