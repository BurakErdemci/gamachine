/**
 * The guide and the tour as the user drives them: playing a topic (deep link to step N runs the
 * prepare actions of steps 1..N), Esc / Geç / arrow keys, "watched" only at a topic's last step,
 * the name step saving through the page's display-name API, the core tour opening by itself once,
 * and the entry points (F1, the sidebar's ?, /rehber in the composer, Settings > General).
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import { useGuide, type GuideApi, type GuideHost } from '../renderer/hooks/home/useGuide'
import { GuideTour } from '../renderer/components/home/GuideTour'
import { GuideScreen } from '../renderer/components/home/GuideScreen'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { AnimatedChatInput } from '../renderer/components/ui/animated-ai-chat'
import { AppearanceProvider } from '../renderer/lib/appearance'
import { capabilities } from '../renderer/lib/guide/capabilities'
import { listedTopics, topicById } from '../renderer/lib/guide/registry'
import { KEYS } from '../renderer/lib/guide/storage'
import { LangContext, ceviriUygula, type Lang } from '../renderer/lib/i18n'

const CTX = { caps: capabilities({ isGitRepo: true }) }
const langValue = (lang: Lang) => ({ lang, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula(lang, k, v) })

function mockHost() {
  const calls: string[] = []
  const rec = (name: string) => vi.fn((a?: unknown) => { calls.push(a === undefined ? name : `${name}:${a}`) })
  const host = {
    screen: rec('screen'), settings: rec('settings'), 'workspace.tab': rec('workspace.tab'),
    'workspace.width': rec('workspace.width'), 'workspace.peek': rec('workspace.peek'),
    'preview.open': rec('preview.open'), drawer: rec('drawer'), menu: rec('menu'),
    showGuideScreen: vi.fn(), snapshot: vi.fn(() => 'SNAP'), restore: vi.fn(), unprepare: vi.fn(), focusComposer: vi.fn(),
  }
  return { host: host as unknown as GuideHost & typeof host, calls }
}

let api: GuideApi
function Harness({ host, saveName = vi.fn(), frameReady = false, userName = '', lang = 'en' }: {
  host: GuideHost; saveName?: (n: string) => unknown; frameReady?: boolean; userName?: string; lang?: Lang
}) {
  const g = useGuide({ ctx: CTX, host, userName, saveName: saveName as any, appVersion: '3.2.0', frameReady })
  api = g
  return (
    <LangContext.Provider value={langValue(lang)}>
      <div className="app">
        <div data-testid="other" />
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

const key = (k: string, target: Window | Element = window) => act(() => { fireEvent.keyDown(target, { key: k }) })
const tourStep = () => screen.getByTestId('guide-tour').getAttribute('data-step')

beforeEach(() => { localStorage.clear(); vi.spyOn(console, 'info').mockImplementation(() => {}) })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

describe('playing a topic', () => {
  it('a deep link to step 2 runs the prepare actions of steps 1 and 2, in order', () => {
    const { host, calls } = mockHost()
    render(<Harness host={host} />)
    act(() => api.playTopic('drawer', 2))
    expect(tourStep()).toBe('2')
    expect(calls).toEqual(['screen:chat', 'workspace.width:half', 'drawer:terminal', 'drawer:problems'])
    expect(host.unprepare).toHaveBeenLastCalledWith(['screen:chat', 'workspace.width:half', 'drawer:terminal', 'drawer:problems'])
    expect(host.snapshot).toHaveBeenCalledTimes(1)
  })

  it('Back lands on the same state as stepping forward: step 1 again runs only step 1', () => {
    const { host, calls } = mockHost()
    render(<Harness host={host} />)
    act(() => api.playTopic('unity-states', 2))
    calls.length = 0
    fireEvent.click(screen.getByTestId('tour-back'))
    expect(tourStep()).toBe('1')
    expect(calls).toEqual(['screen:chat'])
  })

  it('arrow keys step; the right arrow on the last step does not close the topic', () => {
    const { host } = mockHost()
    render(<Harness host={host} />)
    act(() => api.playTopic('model'))
    key('ArrowRight'); expect(tourStep()).toBe('2')
    key('ArrowRight'); expect(tourStep()).toBe('3')
    key('ArrowRight'); expect(tourStep()).toBe('3')
    key('ArrowLeft'); expect(tourStep()).toBe('2')
  })

  it('Esc leaves the topic, gives back what it opened and returns to the guide on that topic', () => {
    const { host } = mockHost()
    render(<Harness host={host} />)
    act(() => api.openGuide())
    act(() => api.playTopic('model'))
    expect(screen.queryByTestId('guide-screen')).toBeNull()
    key('Escape')
    expect(screen.queryByTestId('guide-tour')).toBeNull()
    expect(host.restore).toHaveBeenCalledWith('SNAP', 'topic')
    expect(screen.getByTestId('guide-screen')).toBeTruthy()
    expect(api.focusTopic).toBe('model')
    // skipped before its last step: not watched
    expect(JSON.parse(localStorage.getItem(KEYS.seen) || '{}')).toEqual({})
  })

  it('Geç does the same as Esc', () => {
    const { host } = mockHost()
    render(<Harness host={host} />)
    act(() => api.playTopic('chats'))
    fireEvent.click(screen.getByTestId('tour-skip'))
    expect(screen.queryByTestId('guide-tour')).toBeNull()
    expect(host.restore).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('guide-screen')).toBeTruthy()
  })

  it('reaching the last step marks the topic watched; the guide then shows it as watched', () => {
    const { host } = mockHost()
    render(<Harness host={host} />)
    act(() => api.playTopic('chats'))
    fireEvent.click(screen.getByTestId('tour-next'))
    expect(JSON.parse(localStorage.getItem(KEYS.seen) || '{}')).toEqual({ chats: 1 })
    fireEvent.click(screen.getByTestId('tour-next'))   // "Back to guide"
    const tile = document.querySelector('.gd-topic[data-topic="chats"]')!
    expect(tile.classList.contains('is-seen')).toBe(true)
    expect(tile.textContent).toContain('Watched')
  })

  it('the rest of the frame is inert while the tour is open, and given back after', () => {
    const { host } = mockHost()
    render(<Harness host={host} />)
    act(() => api.playTopic('ask'))
    expect(screen.getByTestId('other').hasAttribute('inert')).toBe(true)
    key('Escape')
    expect(screen.getByTestId('other').hasAttribute('inert')).toBe(false)
  })
})

describe('the core tour', () => {
  it('six steps; continuing past the name step saves it through the page\'s name API', () => {
    const { host, calls } = mockHost()
    const saveName = vi.fn()
    render(<Harness host={host} saveName={saveName} />)
    act(() => api.openTour(1))
    expect(screen.getByTestId('guide-tour').getAttribute('data-id')).toBe('name')
    expect(calls).toEqual(['screen:new_chat'])
    fireEvent.change(screen.getByTestId('tour-name'), { target: { value: '  Burak ' } })
    fireEvent.click(screen.getByTestId('tour-next'))
    expect(saveName).toHaveBeenCalledWith('Burak')
    expect(tourStep()).toBe('2')
    // the next step greets by the typed name
    expect(screen.getByText(/^Burak, a plain sentence/)).toBeTruthy()
  })

  it('skipping the name step leaves the name as it was (empty on a first launch)', () => {
    const { host } = mockHost()
    const saveName = vi.fn()
    render(<Harness host={host} saveName={saveName} />)
    act(() => api.openTour(1))
    fireEvent.change(screen.getByTestId('tour-name'), { target: { value: 'Typed' } })
    key('Escape')
    expect(saveName).not.toHaveBeenCalled()
    expect(localStorage.getItem(KEYS.tourDone)).toBe('1')
    expect(host.restore).toHaveBeenCalledWith('SNAP', 'core')
    expect(host.focusComposer).toHaveBeenCalled()
  })

  it('an unchanged name is not saved again', () => {
    const { host } = mockHost()
    const saveName = vi.fn()
    render(<Harness host={host} saveName={saveName} userName="Burak" />)
    act(() => api.openTour(1))
    expect((screen.getByTestId('tour-name') as HTMLInputElement).value).toBe('Burak')
    fireEvent.click(screen.getByTestId('tour-next'))
    expect(saveName).not.toHaveBeenCalled()
  })

  it('watching it marks its one-step topics as watched (not the core-only steps)', () => {
    const { host } = mockHost()
    render(<Harness host={host} />)
    act(() => api.openTour(1))
    for (let i = 0; i < 5; i++) fireEvent.click(screen.getByTestId('tour-next'))
    expect(screen.getByTestId('tour-next').textContent).toBe("Let's go")
    fireEvent.click(screen.getByTestId('tour-next'))
    expect(JSON.parse(localStorage.getItem(KEYS.seen) || '{}')).toEqual({ ask: 1, 'unity-switch': 1, approvals: 1, workspace: 1 })
    expect(localStorage.getItem(KEYS.tourDone)).toBe('1')
  })

  it('opens by itself once on the first launch, and never again', () => {
    vi.useFakeTimers()
    const { host } = mockHost()
    const first = render(<Harness host={host} frameReady />)
    expect(screen.queryByTestId('guide-tour')).toBeNull()
    act(() => { vi.advanceTimersByTime(3300) })
    expect(screen.getByTestId('guide-tour').getAttribute('data-mode')).toBe('core')
    key('Escape')
    first.unmount()
    render(<Harness host={mockHost().host} frameReady />)
    act(() => { vi.advanceTimersByTime(10_000) })
    expect(screen.queryByTestId('guide-tour')).toBeNull()
  })

  it('waits for the opening animation to end', () => {
    vi.useFakeTimers()
    document.documentElement.setAttribute('data-intro', '')
    try {
      render(<Harness host={mockHost().host} frameReady />)
      act(() => { vi.advanceTimersByTime(5000) })
      expect(screen.queryByTestId('guide-tour')).toBeNull()
      act(() => { window.dispatchEvent(new CustomEvent('gm-intro-end', { detail: { skip: true } })) })
      act(() => { vi.advanceTimersByTime(460) })
      expect(screen.getByTestId('guide-tour')).toBeTruthy()
    } finally {
      document.documentElement.removeAttribute('data-intro')
    }
  })

  it('does not open while the app frame is not on screen', () => {
    vi.useFakeTimers()
    render(<Harness host={mockHost().host} frameReady={false} />)
    act(() => { vi.advanceTimersByTime(10_000) })
    expect(screen.queryByTestId('guide-tour')).toBeNull()
  })
})

describe('the guide screen', () => {
  it('F1 opens it; Esc clears the search first, then goes back', () => {
    const { host } = mockHost()
    render(<Harness host={host} />)
    key('F1')
    expect(screen.getByTestId('guide-screen')).toBeTruthy()
    expect(host.showGuideScreen).toHaveBeenCalled()
    const search = screen.getByTestId('guide-search')
    fireEvent.change(search, { target: { value: 'phone' } })
    key('Escape', search)
    expect(screen.getByTestId('guide-screen')).toBeTruthy()
    expect((screen.getByTestId('guide-search') as HTMLInputElement).value).toBe('')
    key('Escape', screen.getByTestId('guide-search'))
    expect(screen.queryByTestId('guide-screen')).toBeNull()
  })

  it('opens on a search ("/rehber telefon"), lists the matches and counts them', () => {
    const { host } = mockHost()
    render(<Harness host={host} lang="tr" />)
    act(() => api.openGuide('telefon'))
    const tiles = Array.from(document.querySelectorAll('.gd-topic')).map(b => b.getAttribute('data-topic'))
    expect(tiles).toEqual(['phone-pair', 'phone-approve'])
    expect(screen.getByTestId('guide-stat').textContent).toBe('2 konu bulundu')
    expect(document.querySelectorAll('mark.gd-hit').length).toBeGreaterThan(0)
  })

  it('without a search: groups in order, an empty group not drawn, the count line', () => {
    const { host } = mockHost()
    render(<Harness host={host} />)
    act(() => api.openGuide())
    const groups = Array.from(document.querySelectorAll('.gd-group')).map(s => s.getAttribute('data-group'))
    expect(groups).toEqual(['basics', 'unity', 'workspace', 'phone', 'lesser', 'you'])
    expect(screen.getByTestId('guide-stat').textContent).toBe(`${listedTopics(CTX).length} topics · 0 watched`)
  })

  it('"New arrivals" on top and the New mark, from the registry rule', () => {
    const topics = listedTopics(CTX)
    render(
      <LangContext.Provider value={langValue('en')}>
        <GuideScreen open topics={topics} query="" onQuery={() => {}} isSeen={t => t.id === 'ask'} isNew={t => t.id === 'themes'}
          coreSteps={6} onPlay={() => {}} onTour={() => {}} onClose={() => {}} focusTopic={null} onFocused={() => {}} />
      </LangContext.Provider>,
    )
    const first = document.querySelector('.gd-group')!
    expect(first.getAttribute('data-group')).toBe('new')
    expect(first.querySelector('.gd-topic')!.getAttribute('data-topic')).toBe('themes')
    expect(first.querySelector('.gd-new')!.textContent).toBe('New')
    expect(document.querySelector('.gd-topic[data-topic="ask"]')!.classList.contains('is-seen')).toBe(true)
  })

  it('picking a topic plays it; "Watch again" replays the core tour from the guide', () => {
    const { host } = mockHost()
    render(<Harness host={host} />)
    act(() => api.openGuide())
    fireEvent.click(document.querySelector('.gd-topic[data-topic="dictation"]')!)
    expect(screen.getByTestId('guide-tour').getAttribute('data-mode')).toBe('topic')
    key('Escape')
    fireEvent.click(screen.getByTestId('guide-core-replay'))
    expect(screen.getByTestId('guide-tour').getAttribute('data-mode')).toBe('core')
    act(() => api.go(6))
    // started from the guide, the core tour ends back on it
    expect(screen.getByTestId('tour-next').textContent).toBe('Back to guide')
    fireEvent.click(screen.getByTestId('tour-next'))
    expect(screen.getByTestId('guide-screen')).toBeTruthy()
  })
})

describe('entry points', () => {
  const noop = () => {}
  const sidebarProps = (over: Record<string, unknown> = {}) => ({
    ...({} as any),
    isSidebarOpen: true, setSidebarTab: noop,
    conversations: [], activeConvId: null, convStatus: {},
    selectConversation: noop, createNewConversation: noop, deleteConversation: noop,
    editingId: null, setEditingId: noop, tempTitle: '', setTempTitle: noop, saveRename: noop,
    user: { id: 1, name: 'B', sessionToken: 't' }, setShowSettings: noop, handleLogout: noop,
    workspacePath: null, closeWorkspace: noop, ...over,
  })

  it('the sidebar foot ? opens the guide and is marked while it shows', () => {
    const onOpenGuide = vi.fn()
    const { rerender } = render(<LangContext.Provider value={langValue('tr')}><Sidebar {...sidebarProps({ onOpenGuide })} /></LangContext.Provider>)
    const btn = screen.getByTestId('sidebar-guide')
    expect(btn.getAttribute('aria-label')).toBe('Rehber')
    expect(btn.getAttribute('title')).toBe('Rehber · F1')
    fireEvent.click(btn)
    expect(onOpenGuide).toHaveBeenCalledTimes(1)
    rerender(<LangContext.Provider value={langValue('tr')}><Sidebar {...sidebarProps({ onOpenGuide, guideOpen: true })} /></LangContext.Provider>)
    expect(screen.getByTestId('sidebar-guide').classList.contains('is-active')).toBe(true)
  })

  it('Settings > General > Tanıtım: "Rehberi aç" and "Turu yeniden izle"', () => {
    ;(window as any).ipc = { invoke: vi.fn(async () => ({ ok: true, data: {} })) }
    const onOpenGuide = vi.fn(), onReplayTour = vi.fn()
    render(
      <AppearanceProvider>
        <LangContext.Provider value={langValue('tr')}>
          <SettingsScreen {...({
            open: true, page: 'genel', onPageChange: noop, aiConfig: { provider_type: 'subscription', model_name: 'x', api_key: '' },
            availableModels: { local: [], subscription: [], cloud: [] }, providersWithKeys: [], onClose: noop, onLogout: noop,
            onDeleteKey: async () => {}, unityMcpStatus: 'off', unityMcpToggling: false, onToggleUnityMcp: noop, lang: 'tr', onLangChange: noop,
            onOpenGuide, onReplayTour, tourSteps: 6,
          } as any)} />
        </LangContext.Provider>
      </AppearanceProvider>,
    )
    expect(screen.getByText('Tanıtım')).toBeTruthy()
    fireEvent.click(screen.getByTestId('set-guide-open'))
    fireEvent.click(screen.getByTestId('set-tour-replay'))
    expect(onOpenGuide).toHaveBeenCalledTimes(1)
    expect(onReplayTour).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('set-guide-open').textContent).toContain('Rehberi aç')
    expect(screen.getByText(/İlk açılışta bir kez kendiliğinden açılan kısa tur: 6 adım/)).toBeTruthy()
    delete (window as any).ipc
  })

  it('the existing opening-animation row stays where it was (Görünüm)', () => {
    const src = readFileSync(resolve(__dirname, '../renderer/components/home/settings/SettingsPages.tsx'), 'utf8')
    expect(src).toMatch(/AppearancePage[\s\S]*settings\.appearance\.intro/)
  })

  describe('the composer', () => {
    const mount = (onCommand: (c: string) => boolean) => {
      const onSendMessage = vi.fn()
      render(
        <LangContext.Provider value={langValue('tr')}>
          <AnimatedChatInput value="" setValue={vi.fn()} onSendMessage={onSendMessage} isLoading={false} onCommand={onCommand} />
        </LangContext.Provider>,
      )
      return { onSendMessage, box: screen.getByRole('textbox') as HTMLTextAreaElement }
    }

    it('"/rehber telefon" + Enter goes to the page\'s command handler and sends nothing', () => {
      const onCommand = vi.fn(() => true)
      const { onSendMessage, box } = mount(onCommand)
      fireEvent.change(box, { target: { value: '/rehber telefon' } })
      fireEvent.keyDown(box, { key: 'Enter' })
      expect(onCommand).toHaveBeenCalledWith('/rehber telefon')
      expect(onSendMessage).not.toHaveBeenCalled()
      expect(box.value).toBe('')
    })

    it('"/rehber" + Enter opens it at once from the command list (Tab still only completes)', () => {
      const onCommand = vi.fn(() => true)
      const { onSendMessage, box } = mount(onCommand)
      fireEvent.change(box, { target: { value: '/rehber' } })
      expect(document.querySelector('.composer-pop')?.textContent).toContain('/rehber')
      fireEvent.keyDown(box, { key: 'Tab' })
      expect(box.value).toBe('/rehber ')
      expect(onCommand).not.toHaveBeenCalled()
      fireEvent.change(box, { target: { value: '/rehber' } })
      fireEvent.keyDown(box, { key: 'Enter' })
      expect(onCommand).toHaveBeenCalledWith('/rehber')
      expect(onSendMessage).not.toHaveBeenCalled()
    })

    it('home.tsx turns the command into the guide with its search, saves the name through useDisplayName', () => {
      const home = readFileSync(resolve(__dirname, '../renderer/pages/home.tsx'), 'utf8')
      expect(home).toMatch(/parseGuideCommand\(cmd\)[\s\S]{0,120}guide\.openGuide\(guideQuery\); return true;/)
      expect(home).toMatch(/useGuide\(\{[\s\S]{0,120}saveName: me\.saveName/)
      expect(home).toContain('const me = useDisplayName(API, !auth.isLoading);')
    })
  })
})

it('every listed topic plays from its first step to its last without a missing step', () => {
  const { host } = mockHost()
  render(<Harness host={host} />)
  for (const t of listedTopics(CTX)) {
    act(() => api.playTopic(t.id))
    for (let i = 1; i < t.steps.length; i++) fireEvent.click(screen.getByTestId('tour-next'))
    expect(tourStep()).toBe(String(topicById(t.id)!.steps.length))
    fireEvent.click(screen.getByTestId('tour-next'))
    expect(screen.queryByTestId('guide-tour')).toBeNull()
  }
  expect(Object.keys(JSON.parse(localStorage.getItem(KEYS.seen) || '{}'))).toHaveLength(listedTopics(CTX).length)
})
