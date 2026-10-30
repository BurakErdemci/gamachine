import React from 'react'
import { readFileSync } from 'node:fs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { translations } from '../renderer/lib/i18n'
import { LivePane } from '../renderer/components/home/LivePane'
import { Workspace } from '../renderer/components/home/Workspace'
import { LIVE_FEATURES_EVENT, useLiveFeatures } from '../renderer/lib/liveFeatures'
import { createLiveRevealTracker } from '../renderer/lib/computerLiveReveal'
import { shownSides, useLiveSide, viewSide } from '../renderer/lib/liveSide'
import { browserRunningIn, useBrowserActiveChats, useComputerRunning } from '../renderer/lib/liveRunning'
import { useOneSideColumnWhenNarrow, useSidebarFoldedByFocusWidth, useWorkspacePanel, type WsWidth } from '../renderer/lib/workspacePanel'

const tr = translations.tr
const BOTH = { bilgisayar: true, tarayici: true }
const IDLE = { bilgisayar: false, tarayici: false }

afterEach(() => { cleanup(); delete (window as any).ipc })

describe('LivePane', () => {
  const pane = (over: Partial<React.ComponentProps<typeof LivePane>> = {}) => (
    <LivePane side="bilgisayar" onPick={vi.fn()} running={IDLE} shown={BOTH} computer={<p>pc</p>} browser={<p>web</p>} {...over} />
  )

  it('draws both sub-tabs, keeps both panes mounted and hides the inactive one', () => {
    const { container } = render(pane())
    expect(screen.getAllByRole('tab').map(t => t.textContent)).toEqual([tr['ws.tabComputer'], tr['ws.tabBrowser']])
    expect(screen.getByRole('tab', { name: tr['ws.tabComputer'] }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByText('pc')).toBeTruthy()
    expect(screen.getByText('web')).toBeTruthy()
    expect(container.querySelector('[data-side="bilgisayar"].live-side')!.hasAttribute('hidden')).toBe(false)
    expect(container.querySelector('[data-side="tarayici"].live-side')!.hasAttribute('hidden')).toBe(true)
  })

  it('picks a side on click and with the arrow keys, without a modifier key', () => {
    const onPick = vi.fn()
    render(pane({ onPick }))
    fireEvent.click(screen.getByRole('tab', { name: tr['ws.tabBrowser'] }))
    expect(onPick).toHaveBeenLastCalledWith('tarayici')
    fireEvent.keyDown(screen.getAllByRole('tablist')[0], { key: 'ArrowRight' })
    expect(onPick).toHaveBeenLastCalledWith('tarayici')
    fireEvent.keyDown(screen.getAllByRole('tablist')[0], { key: 'ArrowLeft' })
    expect(onPick).toHaveBeenLastCalledWith('tarayici')
  })

  it('shows a running light only on the side that runs', () => {
    const { container } = render(pane({ running: { bilgisayar: false, tarayici: true } }))
    const dots = container.querySelectorAll('.live-dot[data-on]')
    expect(dots).toHaveLength(1)
    expect(dots[0].getAttribute('aria-label')).toBe(tr['ws.liveRunning'])
    expect(dots[0].closest('[role="tab"]')!.textContent).toBe(tr['ws.tabBrowser'])
  })

  it('draws no sub-tab bar and no pane for a side that is not shown', () => {
    render(pane({ shown: { bilgisayar: false, tarayici: true }, side: 'tarayici' }))
    expect(screen.queryByRole('tablist')).toBeNull()
    expect(screen.queryByText('pc')).toBeNull()
    expect(screen.getByText('web')).toBeTruthy()
  })
})

describe('Canlı tab and the manual pick (useLiveSide)', () => {
  it('keeps a running hand-picked side, then follows the next run once it stopped', () => {
    const { result } = renderHook(() => useLiveSide())
    act(() => { result.current.setRunning('bilgisayar', true); result.current.pick('bilgisayar') })
    let shown = true
    act(() => { shown = result.current.autoReveal('tarayici') })
    expect(shown).toBe(false)
    expect(result.current.side).toBe('bilgisayar')
    act(() => result.current.setRunning('bilgisayar', false))
    act(() => { shown = result.current.autoReveal('tarayici') })
    expect(shown).toBe(true)
    expect(result.current.side).toBe('tarayici')
  })
})

describe('useLiveFeatures and the Canlı tab', () => {
  const cuStatus = (over: Record<string, unknown> = {}, enabled = true) => ({
    available: true, running: false, platform: 'darwin',
    settings: { enabled, grants: [] }, permissions: { accessibility: true, screen: 'granted' }, ...over,
  })
  const setIpc = (computer: unknown, browser: unknown) => {
    (window as any).ipc = {
      invoke: vi.fn(async (channel: string, req: { op?: string }) => {
        if (channel === 'computer-use' && req.op === 'status') return computer
        if (channel === 'browser-live' && req.op === 'settings') return browser
        return { error: 'unexpected' }
      }),
    }
  }

  function Harness({ running = IDLE }: { running?: typeof IDLE }) {
    const features = useLiveFeatures()
    const shown = shownSides(features, running)
    const any = shown.bilgisayar || shown.tarayici
    const side = viewSide('bilgisayar', shown)
    const panes = {
      dosyalar: <p>files</p>,
      ...(any ? { canli: <LivePane side={side} onPick={vi.fn()} running={running} shown={shown} computer={<p>pc</p>} browser={<p>web</p>} /> } : {}),
    }
    return <Workspace open tab="dosyalar" onTab={vi.fn()} width="dar" onWidth={vi.fn()} onClose={vi.fn()} panes={panes} drawer={null} />
  }
  const liveTab = () => screen.queryByRole('tab', { name: tr['ws.tabLive'] })
  const settle = () => act(async () => { await new Promise(r => setTimeout(r, 0)) })

  beforeEach(() => { delete (window as any).ipc })

  it('has no Canlı tab while both features are off, or without ipc', async () => {
    render(<Harness />)
    await settle()
    expect(liveTab()).toBeNull()
    cleanup()
    setIpc(cuStatus({}, false), { enabled: false })
    render(<Harness />)
    await settle()
    expect(liveTab()).toBeNull()
  })

  it('treats an error or a malformed answer as off', async () => {
    setIpc({ error: 'boom' }, { enabled: 'yes' })
    render(<Harness />)
    await settle()
    expect(liveTab()).toBeNull()
  })

  it('shows the tab with both sub-tabs when both are on', async () => {
    setIpc(cuStatus(), { enabled: true })
    render(<Harness />)
    expect(await screen.findByRole('tab', { name: tr['ws.tabLive'] })).toBeTruthy()
    expect(screen.getByRole('tab', { name: tr['ws.tabComputer'] })).toBeTruthy()
    expect(screen.getByRole('tab', { name: tr['ws.tabBrowser'] })).toBeTruthy()
  })

  it('draws no sub-tab bar with one side on', async () => {
    setIpc(cuStatus({}, false), { enabled: true })
    render(<Harness />)
    expect(await screen.findByRole('tab', { name: tr['ws.tabLive'] })).toBeTruthy()
    expect(screen.queryByRole('tab', { name: tr['ws.tabComputer'] })).toBeNull()
    expect(screen.queryByRole('tab', { name: tr['ws.tabBrowser'] })).toBeNull()
    expect(screen.getByText('web')).toBeTruthy()
    expect(screen.queryByText('pc')).toBeNull()
  })

  it('hides Bilgisayar where computer use is unavailable (Windows)', async () => {
    setIpc(cuStatus({ available: false, platform: 'win32' }), { enabled: true })
    render(<Harness />)
    expect(await screen.findByRole('tab', { name: tr['ws.tabLive'] })).toBeTruthy()
    expect(screen.queryByText('pc')).toBeNull()
    expect(screen.queryByRole('tab', { name: tr['ws.tabBrowser'] })).toBeNull()
  })

  it('shows a side that runs even though its feature is off', async () => {
    render(<Harness running={{ bilgisayar: false, tarayici: true }} />)
    await settle()
    expect(liveTab()).toBeTruthy()
    expect(screen.getByText('web')).toBeTruthy()
  })

  it('re-reads both switches when the settings event fires', async () => {
    setIpc(cuStatus({}, false), { enabled: false })
    render(<Harness />)
    await settle()
    expect(liveTab()).toBeNull()
    setIpc(cuStatus(), { enabled: false })
    act(() => { window.dispatchEvent(new CustomEvent(LIVE_FEATURES_EVENT)) })
    await waitFor(() => expect(liveTab()).toBeTruthy())
  })

  it('reads the features once on mount', async () => {
    setIpc(cuStatus(), { enabled: true })
    const { result } = renderHook(() => useLiveFeatures())
    await waitFor(() => expect(result.current).toEqual({ computer: true, browser: true }))
  })
})

describe('terminal drawer in the Canlı tab', () => {
  it('marks the panel with data-tab and hides .term by CSS while keeping the drawer mounted', () => {
    render(<Workspace open tab="canli" onTab={vi.fn()} width="dar" onWidth={vi.fn()} onClose={vi.fn()}
      panes={{ dosyalar: <p>f</p>, canli: <p>c</p> }} drawer={<div className="term">drawer</div>} />)
    expect(screen.getByTestId('workspace').getAttribute('data-tab')).toBe('canli')
    expect(screen.getByText('drawer')).toBeTruthy()
    const css = readFileSync('renderer/styles/gm/workspace.css', 'utf8')
    expect(css).toMatch(/\.workspace\[data-tab="canli"\] \.term\[data-open="false"\] \{ display: none; \}/)
    expect(css).not.toMatch(/\.workspace\[data-tab="canli"\] \.term \{/)
    for (const theme of ['sade', 'atolye', 'ozel', 'pafta']) {
      const t = readFileSync(`renderer/styles/gm/theme-${theme}.workspace.css`, 'utf8')
      expect(t).not.toMatch(/data-tab="canli"[^{]*\.term[^{]*\{[^}]*display:\s*(flex|block|grid)/)
    }
  })
})

describe('sidebar and the widest panel', () => {
  function useBoth(initial: boolean) {
    const [open, setOpen] = React.useState(initial)
    const ws = useWorkspacePanel()
    useSidebarFoldedByFocusWidth(ws.width, { open, setOpen })
    return { ...ws, open, setOpen }
  }

  it('closes the sidebar for odak and reopens it when the width leaves odak', () => {
    const { result } = renderHook(() => useBoth(true))
    act(() => result.current.setWidth('odak'))
    expect(result.current.open).toBe(false)
    act(() => result.current.setWidth('yarim'))
    expect(result.current.open).toBe(true)
  })

  it('does not reopen a sidebar the user reopened or closed meanwhile', () => {
    const { result } = renderHook(() => useBoth(true))
    act(() => result.current.setWidth('odak'))
    act(() => result.current.setOpen(true))
    act(() => result.current.setWidth('dar'))
    expect(result.current.open).toBe(true)
    act(() => result.current.setWidth('odak'))
    act(() => result.current.setOpen(true))
    act(() => result.current.setOpen(false))
    act(() => result.current.setWidth('dar'))
    expect(result.current.open).toBe(false)
  })

  it('leaves a sidebar that was already closed alone', () => {
    const { result } = renderHook(() => useBoth(false))
    const widths: WsWidth[] = ['odak', 'dar']
    for (const w of widths) act(() => result.current.setWidth(w))
    expect(result.current.open).toBe(false)
  })
})

describe('sidebar, odak and a narrow window', () => {
  function useAll(narrow: boolean) {
    const [open, setOpen] = React.useState(true)
    const ws = useWorkspacePanel()
    useOneSideColumnWhenNarrow(narrow, { open, setOpen }, { open: ws.open, setOpen: ws.setOpen })
    useSidebarFoldedByFocusWidth(ws.width, { open, setOpen }, narrow)
    return { ...ws, sidebar: open }
  }

  it('keeps the panel open and the sidebar closed when odak is left after the window narrowed', () => {
    const { result, rerender } = renderHook(({ narrow }) => useAll(narrow), { initialProps: { narrow: false } })
    act(() => result.current.setWidth('odak'))
    expect(result.current.sidebar).toBe(false)
    rerender({ narrow: true })
    act(() => result.current.setWidth('yarim'))
    expect(result.current.open).toBe(true)
    expect(result.current.sidebar).toBe(false)
  })

  it('still brings the sidebar back when odak is left in a wide window', () => {
    const { result } = renderHook(() => useAll(false))
    act(() => result.current.setWidth('odak'))
    act(() => result.current.setWidth('yarim'))
    expect(result.current.sidebar).toBe(true)
    expect(result.current.open).toBe(true)
  })
})

describe('what counts as a running agent in the Canlı tab', () => {
  type Listener = (raw: unknown) => void
  function fakeIpc(status: (channel: string, req: any) => unknown = () => ({ error: 'none' })) {
    const listeners = new Map<string, Listener>()
    return {
      listeners,
      on: vi.fn((channel: string, fn: Listener) => { listeners.set(channel, fn); return () => { listeners.delete(channel) } }),
      invoke: vi.fn(async (channel: string, req: any) => status(channel, req)),
      emit: (channel: string, raw: unknown) => listeners.get(channel)!(raw),
    }
  }
  const settle = () => act(async () => { await new Promise(r => setTimeout(r, 0)) })

  it('needs both a turn in flight and a live tab in the open chat', () => {
    const chats = new Set([101])
    expect(browserRunningIn(chats, 101, true)).toBe(true)
    expect(browserRunningIn(chats, 101, false)).toBe(false)
    expect(browserRunningIn(chats, 202, true)).toBe(false)
    expect(browserRunningIn(chats, null, true)).toBe(false)
  })

  it('keeps one record per chat: another chat going active or idle changes only itself', async () => {
    const ipc = fakeIpc()
    const { result } = renderHook(() => useBrowserActiveChats(ipc, 101, vi.fn()))
    await settle()
    act(() => ipc.emit('browser-live', { active: true, conversationId: 101 }))
    act(() => ipc.emit('browser-live', { active: true, conversationId: 202 }))
    expect([...result.current].sort()).toEqual([101, 202])
    act(() => ipc.emit('browser-live', { active: false, conversationId: 202 }))
    expect([...result.current]).toEqual([101])
  })

  it('remembers a chat across A -> B -> A', async () => {
    const ipc = fakeIpc()
    const { result, rerender } = renderHook(({ id }) => useBrowserActiveChats(ipc, id, vi.fn()), { initialProps: { id: 101 } })
    await settle()
    act(() => ipc.emit('browser-live', { active: true, conversationId: 101 }))
    act(() => ipc.emit('browser-live', { active: true, conversationId: 202 }))
    rerender({ id: 202 })
    rerender({ id: 101 })
    await settle()
    expect(browserRunningIn(result.current, 101, true)).toBe(true)
  })

  it('goes off when the turn ends although the tab stays', async () => {
    const ipc = fakeIpc()
    const { result } = renderHook(() => useBrowserActiveChats(ipc, 101, vi.fn()))
    await settle()
    act(() => ipc.emit('browser-live', { active: true, conversationId: 101 }))
    expect(browserRunningIn(result.current, 101, true)).toBe(true)
    expect(browserRunningIn(result.current, 101, false)).toBe(false)
  })

  it('hydrates the open chat from its status read, and a status never overrides an event', async () => {
    const ipc = fakeIpc((channel, req) => channel === 'browser-live' && req.op === 'status' ? { active: true, conversationId: req.conversationId } : { error: 'none' })
    const { result } = renderHook(() => useBrowserActiveChats(ipc, 101, vi.fn()))
    await settle()
    expect([...result.current]).toEqual([101])
    expect(ipc.invoke).toHaveBeenCalledWith('browser-live', { op: 'status', conversationId: 101 })

    let release: (v: unknown) => void = () => {}
    const slow = fakeIpc(() => new Promise(r => { release = r }))
    const late = renderHook(() => useBrowserActiveChats(slow, 303, vi.fn()))
    act(() => slow.emit('browser-live', { active: false, conversationId: 303 }))
    await act(async () => { release({ active: true, conversationId: 303 }) })
    expect([...late.result.current]).toEqual([])
  })

  it('passes every browser event to the reveal callback', async () => {
    const ipc = fakeIpc()
    const onEvent = vi.fn()
    renderHook(() => useBrowserActiveChats(ipc, 101, onEvent))
    act(() => ipc.emit('browser-live', { active: true, conversationId: 101 }))
    expect(onEvent).toHaveBeenCalledWith({ active: true, conversationId: 101 })
  })

  it('hydrates the computer run on mount without calling the reveal callback, then follows events', async () => {
    const ipc = fakeIpc((channel, req) => channel === 'computer-use-live' && req.op === 'status' ? { active: true } : { error: 'none' })
    const onEvent = vi.fn()
    const { result } = renderHook(() => useComputerRunning(ipc, onEvent))
    await settle()
    expect(result.current).toBe(true)
    expect(onEvent).not.toHaveBeenCalled()
    act(() => ipc.emit('computer-use-live', { active: false }))
    expect(result.current).toBe(false)
    expect(onEvent).toHaveBeenCalledTimes(1)
  })
})

describe('useLiveFeatures ordering', () => {
  it('lets the latest started read win over an older, slower one', async () => {
    const pending: Array<(v: unknown) => void> = []
    ;(window as any).ipc = {
      invoke: vi.fn((channel: string, req: { op?: string }) => {
        if (channel === 'computer-use' && req.op === 'status') return Promise.resolve({ error: 'x' })
        return new Promise(resolve => { pending.push(resolve) })
      }),
    }
    const { result } = renderHook(() => useLiveFeatures())
    await act(async () => {})
    act(() => { window.dispatchEvent(new CustomEvent(LIVE_FEATURES_EVENT)) })
    await act(async () => {})
    expect(pending).toHaveLength(2)
    await act(async () => { pending[1]({ enabled: true }) })
    expect(result.current.browser).toBe(true)
    await act(async () => { pending[0]({ enabled: false }) })
    expect(result.current.browser).toBe(true)
    delete (window as any).ipc
  })
})

describe('Canlı reveal wiring in the page', () => {
  type Listener = (raw: unknown) => void
  const listeners = new Map<string, Listener>()
  const ipc = {
    invoke: async () => ({ active: false }),
    on: (channel: string, fn: Listener) => { listeners.set(channel, fn); return () => { listeners.delete(channel) } },
  }
  const emit = (channel: string, raw: unknown) => act(() => { listeners.get(channel)!(raw) })

  // The page's wiring: the reveal marks the side running first, then opens the tab; the fallback leaves Canlı when no side is shown.
  function usePage(chat: { activeConvId: number; loading: boolean }) {
    const ws = useWorkspacePanel()
    const live = useLiveSide()
    const features = { computer: false, browser: false }
    const shown = shownSides(features, live.running)
    const { setRunning, autoReveal } = live
    const { reveal, setTab } = ws
    const openChat = React.useRef(chat)
    openChat.current = chat
    const reveals = React.useRef(0)
    const computerTracker = React.useMemo(() => createLiveRevealTracker(() => {
      if (autoReveal('bilgisayar')) { setRunning('bilgisayar', true); reveals.current++; reveal('canli') }
    }), [autoReveal, setRunning, reveal])
    const computerRunning = useComputerRunning(ipc, computerTracker)
    React.useEffect(() => { setRunning('bilgisayar', computerRunning) }, [computerRunning, setRunning])
    const browserTracker = React.useMemo(() => createLiveRevealTracker(
      () => { if (autoReveal('tarayici')) { setRunning('tarayici', true); reveals.current++; reveal('canli') } },
      id => id === openChat.current.activeConvId && openChat.current.loading,
    ), [autoReveal, setRunning, reveal])
    const browserChats = useBrowserActiveChats(ipc, chat.activeConvId, browserTracker)
    const browserRunning = browserRunningIn(browserChats, chat.activeConvId, chat.loading)
    React.useEffect(() => {
      if (!chat.loading) browserTracker({ active: false, conversationId: chat.activeConvId })
    }, [chat.loading, chat.activeConvId, browserTracker])
    React.useEffect(() => { setRunning('tarayici', browserRunning) }, [browserRunning, setRunning])
    React.useEffect(() => {
      if (ws.tab === 'canli' && !(shown.bilgisayar || shown.tarayici)) setTab('dosyalar')
    }, [ws.tab, shown.bilgisayar, shown.tarayici, setTab])
    return { ws, live, shown, reveals }
  }

  afterEach(() => { listeners.clear() })

  it('keeps the revealed Canlı tab when a computer run arrives with its feature off', async () => {
    const { result } = renderHook(() => usePage({ activeConvId: 1, loading: true }))
    await act(async () => {})
    emit('computer-use-live', { active: true, conversationId: 1 })
    expect(result.current.ws.tab).toBe('canli')
    expect(result.current.shown.bilgisayar).toBe(true)
  })

  it('keeps the revealed Canlı tab when a browser run arrives with its feature off', async () => {
    const { result } = renderHook(() => usePage({ activeConvId: 1, loading: true }))
    await act(async () => {})
    emit('browser-live', { active: true, conversationId: 1 })
    expect(result.current.ws.tab).toBe('canli')
    expect(result.current.shown.tarayici).toBe(true)
  })

  it('reveals again on the next turn of the same chat while the tab stays', async () => {
    const { result, rerender } = renderHook(({ loading }) => usePage({ activeConvId: 1, loading }), { initialProps: { loading: true } })
    await act(async () => {})
    emit('browser-live', { active: true, conversationId: 1 })
    expect(result.current.reveals.current).toBe(1)
    rerender({ loading: false })
    rerender({ loading: true })
    emit('browser-live', { active: true, conversationId: 1 })
    expect(result.current.reveals.current).toBe(2)
  })
})
