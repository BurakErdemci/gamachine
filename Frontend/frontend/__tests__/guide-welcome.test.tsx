/**
 * The guide on the welcome screen (no project open): the footer's ? opens the same GuideScreen in
 * place of the recent projects, it lists only the topics that play there (the Unity Hub topic),
 * that topic's tour spotlights the real "New project" button, and a project-only topic cannot be
 * started there. Composed the way home.tsx composes the welcome branch.
 */
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.hoisted(() => {
  ;(globalThis as any).window.ipc = { invoke: vi.fn(async () => null), registerDroppedFolder: vi.fn(), on: vi.fn(() => () => {}) }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))

import axios from 'axios'
import { WorkspaceScreen } from '../renderer/components/home/WorkspaceScreen'
import { GuideScreen } from '../renderer/components/home/GuideScreen'
import { GuideTour } from '../renderer/components/home/GuideTour'
import { useGuide, type GuideApi, type GuideHost } from '../renderer/hooks/home/useGuide'
import { capabilities } from '../renderer/lib/guide/capabilities'
import { listedTopics, topicScreen, unavailableReason, topicById, type GuideContext } from '../renderer/lib/guide/registry'
import { LangContext, ceviriUygula } from '../renderer/lib/i18n'

const WELCOME: GuideContext = { caps: capabilities({ isGitRepo: false }), screen: 'welcome' }
const PROJECT: GuideContext = { caps: capabilities({ isGitRepo: true }), screen: 'project' }

function mockHost() {
  const calls: string[] = []
  const rec = (name: string) => vi.fn((a?: unknown) => { calls.push(a === undefined ? name : `${name}:${a}`) })
  const host = {
    screen: rec('screen'), settings: rec('settings'), 'workspace.tab': rec('workspace.tab'),
    'workspace.width': rec('workspace.width'), 'workspace.peek': rec('workspace.peek'),
    'preview.open': rec('preview.open'), drawer: rec('drawer'), menu: rec('menu'),
    showGuideScreen: vi.fn(), snapshot: vi.fn(() => null), restore: vi.fn(), unprepare: vi.fn(), focusComposer: vi.fn(),
  }
  return { host: host as unknown as GuideHost, calls }
}

let api: GuideApi
function Welcome({ host }: { host: GuideHost }) {
  const g = useGuide({ ctx: WELCOME, host, userName: 'Burak', saveName: vi.fn(), appVersion: '3.2.0', frameReady: false })
  api = g
  const value = { lang: 'en' as const, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }
  return (
    <LangContext.Provider value={value}>
      <WorkspaceScreen
        api="http://backend" user={{ id: 7, name: 'Burak', sessionToken: 'tok' } as any} userName="Burak"
        onOpenFolder={vi.fn(async () => null)} onSelectWorkspace={vi.fn()} onLogout={vi.fn()} showToast={vi.fn()}
        guideOpen={g.guideOpen} onOpenGuide={() => g.openGuide()}
        guideScreen={
          <GuideScreen
            open={g.guideOpen} topics={g.topics} query={g.query} onQuery={g.setQuery}
            isSeen={g.isSeen} isNew={g.isNew} coreSteps={g.coreSteps}
            onPlay={id => g.playTopic(id)} onTour={() => g.openTour(1)} onClose={g.closeGuide}
            focusTopic={g.focusTopic} onFocused={g.clearFocusTopic}
          />
        }
        guideTour={g.tour && <GuideTour tour={g.tour} approvalMode="balanced" onNext={g.next} onBack={g.back} onSkip={g.skip} onNameDraft={g.setNameDraft} />}
      />
    </LangContext.Provider>
  )
}

// jsdom lays nothing out: give the Hub button a box, everything else stays 0x0 (not on screen).
const HUB = { left: 200, top: 300, width: 180, height: 42 }
beforeEach(() => {
  localStorage.clear()
  vi.spyOn(console, 'info').mockImplementation(() => {})
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { workspaces: [] } })
  const orig = Element.prototype.getBoundingClientRect
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    if (this.getAttribute('data-guide') === 'welcome-new-project') {
      return { ...HUB, right: HUB.left + HUB.width, bottom: HUB.top + HUB.height, x: HUB.left, y: HUB.top, toJSON() {} } as DOMRect
    }
    // the overlay's own frame (the welcome screen, fixed to the window)
    if (this.classList.contains('tour')) return { left: 0, top: 0, width: 1280, height: 800, right: 1280, bottom: 800, x: 0, y: 0, toJSON() {} } as DOMRect
    return orig.call(this)
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('registry: which screen a topic plays on', () => {
  it('the Unity Hub topic is listed only on the welcome screen, every other topic only with a project', () => {
    const welcome = listedTopics(WELCOME).map(t => t.id)
    expect(welcome).toEqual(['new-project'])
    expect(listedTopics(PROJECT).map(t => t.id)).not.toContain('new-project')
    expect(topicScreen(topicById('new-project')!)).toBe('welcome')
    expect(unavailableReason(topicById('ask')!, WELCOME)).toBe('screen')
    expect(unavailableReason(topicById('new-project')!, PROJECT)).toBe('screen')
  })
})

describe('the guide on the welcome screen', () => {
  it('the footer ? opens the guide in place of the recent projects; Esc gives them back', async () => {
    render(<Welcome host={mockHost().host} />)
    await waitFor(() => expect(axios.get).toHaveBeenCalled())
    const entry = screen.getByTestId('welcome-guide')
    expect(entry.className).toContain('foot-help')
    fireEvent.click(entry)
    expect(screen.getByTestId('welcome').getAttribute('data-screen')).toBe('rehber')
    expect(entry.getAttribute('aria-current')).toBe('page')
    const guide = screen.getByTestId('guide-screen')
    expect(guide.closest('.welcome')).toBe(screen.getByTestId('welcome'))
    expect([...guide.querySelectorAll('.gd-topic')].map(el => el.getAttribute('data-topic'))).toEqual(['new-project'])
    // the core tour has no step that can show here, so the guide does not offer it
    expect(screen.queryByTestId('guide-core-replay')).toBeNull()
    act(() => { fireEvent.keyDown(window, { key: 'Escape' }) })
    expect(screen.queryByTestId('guide-screen')).toBeNull()
    expect(screen.getByTestId('welcome').hasAttribute('data-screen')).toBe(false)
  })

  it('F1 opens it too', () => {
    render(<Welcome host={mockHost().host} />)
    act(() => { fireEvent.keyDown(window, { key: 'F1' }) })
    expect(screen.getByTestId('guide-screen')).toBeTruthy()
  })

  it('the new-project tour spotlights the Unity Hub button and returns to the guide', () => {
    const { host, calls } = mockHost()
    render(<Welcome host={host} />)
    fireEvent.click(screen.getByTestId('welcome-guide'))
    fireEvent.click(document.querySelector('.gd-topic[data-topic="new-project"]')!)
    const tour = screen.getByTestId('guide-tour')
    expect(tour.getAttribute('data-id')).toBe('new-project')
    expect(calls).toEqual(['screen:welcome'])
    // the overlay lives in the welcome frame and quiets the rest of it
    expect(tour.parentElement).toBe(screen.getByTestId('welcome'))
    expect(document.querySelector('.wl-side')!.hasAttribute('inert')).toBe(true)
    // the hole is the Hub button's box plus the 6px spotlight margin, the card on its right
    expect(screen.getByTestId('welcome-new').getAttribute('data-guide')).toBe('welcome-new-project')
    expect(tour.dataset.side).toBe('right')
    const hole = (document.querySelector('.tour-hole') as HTMLElement).style
    expect([hole.left, hole.top, hole.width, hole.height]).toEqual(['194px', '294px', '192px', '54px'])
    fireEvent.click(screen.getByTestId('tour-next'))
    expect(screen.queryByTestId('guide-tour')).toBeNull()
    expect(screen.getByTestId('guide-screen')).toBeTruthy()
    expect(document.querySelector('.wl-side')!.hasAttribute('inert')).toBe(false)
  })

  it('a project-only topic is not started on the welcome screen', () => {
    const { host, calls } = mockHost()
    render(<Welcome host={host} />)
    act(() => api.playTopic('ask'))
    act(() => api.playTopic('drawer', 2))
    expect(screen.queryByTestId('guide-tour')).toBeNull()
    expect(calls).toEqual([])
    expect(host.snapshot).not.toHaveBeenCalled()
    // and searching for one finds nothing to play
    act(() => api.openGuide('chat'))
    expect(document.querySelectorAll('.gd-topic')).toHaveLength(0)
    expect(screen.getByTestId('guide-none')).toBeTruthy()
  })
})
