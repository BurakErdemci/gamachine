/** Guide audit fixes, 2 Oct 2026: tour, guide screen, drawer snapshot, profile hook, queue, AI config. */
import React from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'

vi.mock('axios', () => ({ default: { get: vi.fn(), put: vi.fn() } }))

import axios from 'axios'
import { useGuide, type GuideApi, type GuideHost } from '../renderer/hooks/home/useGuide'
import { GuideTour } from '../renderer/components/home/GuideTour'
import { GuideScreen } from '../renderer/components/home/GuideScreen'
import { useProfileStats } from '../renderer/hooks/home/useProfileStats'
import { useAIConfig } from '../renderer/hooks/home/useAIConfig'
import { useAchievementQueue } from '../renderer/lib/achievementQueue'
import { capabilities } from '../renderer/lib/guide/capabilities'
import { LangContext, ceviriUygula } from '../renderer/lib/i18n'
import { EMPTY } from './fixtures/profileStats'

const CTX = { caps: capabilities({ isGitRepo: true }) }
const src = (p: string) => readFileSync(resolve(__dirname, p), 'utf8')

function mockHost(): GuideHost & Record<string, any> {
  const f = () => vi.fn()
  return {
    screen: f(), settings: f(), 'workspace.tab': f(), 'workspace.width': f(), 'workspace.peek': f(),
    'preview.open': f(), drawer: f(), menu: f(),
    showGuideScreen: f(), snapshot: vi.fn(() => 'SNAP'), restore: f(), unprepare: f(), focusComposer: f(),
  } as any
}

let api: GuideApi
function Harness({ host, userName = '' }: { host: GuideHost; userName?: string }) {
  const g = useGuide({ ctx: CTX, host, userName, saveName: vi.fn() as any, appVersion: '3.2.0', frameReady: false })
  api = g
  return (
    <LangContext.Provider value={{ lang: 'en', setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }}>
      <div className="app">
        <GuideScreen
          open={g.guideOpen} topics={g.topics} query={g.query} onQuery={g.setQuery}
          isSeen={g.isSeen} isNew={g.isNew} coreSteps={g.coreSteps}
          onPlay={id => g.playTopic(id)} onTour={() => g.openTour(1)} onClose={g.closeGuide}
          focusTopic={g.focusTopic} onFocused={g.clearFocusTopic}
        />
        {g.tour && <GuideTour tour={g.tour} approvalMode="balanced" onNext={g.next} onBack={g.back} onSkip={g.skip} onNameDraft={g.setNameDraft} />}
      </div>
    </LangContext.Provider>
  )
}
const tick = (ms = 80) => act(async () => { await new Promise(r => setTimeout(r, ms)) })

beforeEach(() => { localStorage.clear(); vi.spyOn(console, 'info').mockImplementation(() => {}) })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); document.body.innerHTML = '' })

describe('1 Ctrl+S while a modal is open', () => {
  it('home.tsx returns before saving when the tour or an aria-modal dialog is on screen', () => {
    // home.tsx cannot be rendered in a test (workspace, xterm, auth); the handler is checked as source.
    const home = src('../renderer/pages/home.tsx')
    expect(home).toMatch(/e\.key === 's'\) \{\s*e\.preventDefault\(\);[\s\S]{0,260}querySelector\('\[data-testid="guide-tour"\], \[aria-modal="true"\]'\)\) return;\s*await fs\.saveFile\(\);/)
  })
  it('the tour dialog carries both markers the handler looks for', () => {
    render(<Harness host={mockHost()} />)
    act(() => api.playTopic('ask'))
    const tour = screen.getByTestId('guide-tour')
    expect(tour.getAttribute('aria-modal')).toBe('true')
    expect(document.querySelector('[data-testid="guide-tour"], [aria-modal="true"]')).toBe(tour)
  })
})

describe('2 focus after a topic', () => {
  it('lands on the topic card and stays there once the effects settle', async () => {
    render(<Harness host={mockHost()} />)
    act(() => api.openGuide())
    await tick()
    act(() => api.playTopic('ask'))
    await tick()
    act(() => api.skip())
    await tick()
    await tick()
    const card = document.querySelector('.gd-topic[data-topic="ask"]')
    expect(card).toBeTruthy()
    expect(document.activeElement).toBe(card)
  })
  it('focuses the search box when the topic is cleared before its frame runs', async () => {
    render(<Harness host={mockHost()} />)
    act(() => api.openGuide())
    await tick()
    act(() => api.playTopic('ask'))
    await tick()
    // F1 lands in the same frame the topic ended: focusTopic goes 'ask' -> null before the rAF.
    act(() => api.skip())
    act(() => api.openGuide())
    await tick(); await tick()
    expect(document.activeElement).toBe(screen.getByTestId('guide-search'))
  })
  it('opening the guide fresh still focuses the search box', async () => {
    render(<Harness host={mockHost()} />)
    act(() => api.openGuide())
    await tick(); await tick()
    expect(document.activeElement).toBe(screen.getByTestId('guide-search'))
  })
})

describe('3 typed name in the step text', () => {
  it.each(['$&', "$'", '$`', '$$'])('shows %s literally', name => {
    render(<Harness host={mockHost()} userName={name} />)
    act(() => api.playTopic('ask'))
    expect(screen.getByText(`${name}, a plain sentence is enough, like “Add a jump to the player”. I read the files, write the code and do it in Unity.`)).toBeTruthy()
  })
})

describe('4 spotlight target removed mid-step', () => {
  it('re-resolves and centres the card instead of keeping the old hole', async () => {
    const anchor = document.createElement('div')
    anchor.setAttribute('data-guide', 'composer')
    anchor.getBoundingClientRect = () => ({ left: 100, top: 100, width: 200, height: 50, right: 300, bottom: 150, x: 100, y: 100, toJSON() {} }) as DOMRect
    document.body.appendChild(anchor)
    render(<Harness host={mockHost()} />)
    act(() => api.playTopic('ask'))
    const hole = () => (document.querySelector('.tour-hole') as HTMLElement).style.cssText
    const before = hole()
    expect(before).toContain('94px')
    await act(async () => { anchor.remove(); await Promise.resolve(); await Promise.resolve() })
    expect(hole()).not.toBe(before)
  })
})

describe('5 drawer tab in the guide snapshot', () => {
  it('home.tsx snapshots the drawer tab and restore puts it back; the panel reports its tab', () => {
    // TerminalPanel (xterm) and the page cannot be rendered here; the snapshot/restore wiring is checked as source.
    const home = src('../renderer/pages/home.tsx')
    expect(home).toMatch(/terminal: isTerminalOpen, drawerTab: drawerTabRef\.current/)
    expect(home).toMatch(/restore: [\s\S]{0,400}setDrawerTabRequest\(\{ tab: snap\.drawerTab \}\)/)
    expect(home).toMatch(/onTabChange=\{tab => \{ drawerTabRef\.current = tab; \}\}/)
    const panel = src('../renderer/components/home/TerminalPanel.tsx')
    expect(panel).toMatch(/useEffect\(\(\) => \{ onTabChange\?\.\(tab\); \}, \[tab\]\)/)
  })
  it('the guide hands restore the snapshot it took before the first step', () => {
    const host = mockHost()
    render(<Harness host={host} />)
    act(() => api.playTopic('drawer'))
    act(() => api.skip())
    expect(host.snapshot).toHaveBeenCalledTimes(1)
    expect(host.restore).toHaveBeenCalledWith('SNAP', 'topic')
  })
})

describe('6 modified arrow keys', () => {
  it.each(['altKey', 'ctrlKey', 'metaKey'] as const)('%s + ArrowRight does not step the tour', mod => {
    render(<Harness host={mockHost()} />)
    act(() => api.playTopic('drawer'))
    const step = () => screen.getByTestId('guide-tour').getAttribute('data-step')
    expect(step()).toBe('1')
    act(() => { fireEvent.keyDown(window, { key: 'ArrowRight', [mod]: true }) })
    expect(step()).toBe('1')
    act(() => { fireEvent.keyDown(window, { key: 'ArrowRight' }) })
    expect(step()).toBe('2')
    act(() => { fireEvent.keyDown(window, { key: 'ArrowLeft', [mod]: true }) })
    expect(step()).toBe('2')
  })
})

describe('7 superseded profile answer', () => {
  it('still announces the unlock it carries, once', async () => {
    const withNew = { ...EMPTY, achievements: EMPTY.achievements.map(a => (a.id === 'first_task' ? { ...a, unlocked: true, new: true } : a)) }
    let release!: (v: { data: unknown }) => void
    const late = new Promise<{ data: unknown }>(r => { release = r })
    const get = vi.fn()
      .mockResolvedValueOnce({ data: EMPTY })
      .mockReturnValueOnce(late)
      .mockResolvedValue({ data: EMPTY })
    const http = { get }
    const { result } = renderHook(() => useProfileStats({ api: 'a', http }))
    await waitFor(() => expect(result.current.latest).not.toBeNull())
    let refreshing!: Promise<void>
    act(() => { refreshing = result.current.refresh() })
    act(() => { result.current.setRange('all') })
    await waitFor(() => expect(get).toHaveBeenCalledTimes(3))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.unlocked).toBeNull()
    await act(async () => { release({ data: withNew }); await refreshing })
    expect(result.current.unlocked?.ids).toEqual(['first_task'])
    const first = result.current.unlocked
    get.mockResolvedValue({ data: withNew })
    await act(async () => { await result.current.refresh() })
    expect(result.current.unlocked).toBe(first)
  })

  it('two answers settling in one batch announce both unlocks, once each', async () => {
    const unlock = (id: string) => ({ ...EMPTY, achievements: EMPTY.achievements.map(a => (a.id === id ? { ...a, unlocked: true, new: true } : a)) })
    const both = { ...EMPTY, achievements: EMPTY.achievements.map(a => (a.id === 'first_task' || a.id === 'night_owl' ? { ...a, unlocked: true, new: true } : a)) }
    let releaseA!: (v: { data: unknown }) => void
    let releaseB!: (v: { data: unknown }) => void
    const get = vi.fn()
      .mockResolvedValueOnce({ data: EMPTY })
      .mockReturnValueOnce(new Promise<{ data: unknown }>(r => { releaseA = r }))
      .mockReturnValueOnce(new Promise<{ data: unknown }>(r => { releaseB = r }))
      .mockResolvedValue({ data: both })
    const http = { get }
    const { result } = renderHook(() => useProfileStats({ api: 'a', http }))
    await waitFor(() => expect(result.current.latest).not.toBeNull())
    let a!: Promise<void>
    let b!: Promise<void>
    act(() => { a = result.current.refresh(); b = result.current.refresh() })
    // Guide fix verify (2 Oct 2026): both settle before React renders.
    await act(async () => { releaseB({ data: unlock('night_owl') }); releaseA({ data: unlock('first_task') }); await Promise.all([a, b]) })
    expect(result.current.unlocked?.ids).toEqual(['first_task', 'night_owl'])
    const event = result.current.unlocked
    await act(async () => { await result.current.refresh() })
    expect(result.current.unlocked).toBe(event)
  })
})

describe('8 provider key list per user', () => {
  it('goes back to unloaded when the user id changes', async () => {
    vi.mocked(axios.get).mockImplementation((async (url: string) => (
      String(url).includes('/api-keys/') ? { data: { providers_with_keys: ['x'] } } : { data: { status: 'off' } })) as any)
    const { result, rerender } = renderHook(({ id }) => useAIConfig('http://b', { id, name: 'n', sessionToken: 't' } as any, vi.fn()), { initialProps: { id: 1 } })
    await act(async () => { await result.current.fetchProvidersWithKeys(1) })
    expect(result.current.providersWithKeysLoaded).toBe(true)
    rerender({ id: 2 })
    expect(result.current.providersWithKeysLoaded).toBe(false)
    await act(async () => { await result.current.fetchProvidersWithKeys(2) })
    expect(result.current.providersWithKeysLoaded).toBe(true)
  })
})

describe('9 gains without a turn band', () => {
  it('keeps each gain silent, including repeated ones', () => {
    vi.useFakeTimers()
    const turn = { seq: 1 }
    const h = renderHook(({ gain }) => ({ turn, gain, band: useAchievementQueue(null, null) }), { initialProps: { gain: { seq: 1, xp: 40 } as { seq: number; xp: number } } })
    expect(h.result.current.band).toBeNull()
    h.rerender({ gain: { seq: 2, xp: 10 } })
    expect(h.result.current.band).toBeNull()
    h.rerender({ gain: { seq: 3, xp: 20 } })
    expect(h.result.current.band).toBeNull()
    h.rerender({ gain: { seq: 3, xp: 20 } })
    expect(h.result.current.band).toBeNull()
    h.rerender({ gain: { seq: 2, xp: 10 } })
    expect(h.result.current.band).toBeNull()
  })
})
