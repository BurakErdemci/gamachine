// The web remote's phone layout (renderer/lib/mobile.ts, renderer/components/phone/): only a remote page at
// phone width enters it; the chat list opens a chat that the phone's Back closes; the waiting bar opens
// the first waiting chat; the composer rises above the on-screen keyboard; the fixed banner steps aside.
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'

const page = vi.hoisted(() => ({ remote: true }))
vi.mock('../renderer/lib/remote/transport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../renderer/lib/remote/transport')>()
  return { ...actual, isRemotePage: () => page.remote }
})

import { usePhoneLayout, usePhoneRoute } from '../renderer/lib/mobile'
import { PhoneShell, type PhoneShellProps } from '../renderer/components/phone/PhoneShell'
import { RemoteGate } from '../renderer/components/remote/RemotePairing'
import { AnimatedChatInput } from '../renderer/components/ui/animated-ai-chat'
import { aktifDilAyarla, translations } from '../renderer/lib/i18n'
import type { Conversation } from '../renderer/components/home/types'

const tr = translations.tr
let width = 390

beforeEach(() => {
  aktifDilAyarla('tr')
  page.remote = true
  width = 390
  // jsdom has no matchMedia; this one answers the phone query from `width`, live.
  vi.stubGlobal('matchMedia', (query: string) => ({
    get matches() { return query === '(max-width: 767px)' ? width < 768 : false },
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }))
  window.history.replaceState(null, '')
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const conv = (id: number, title: string, extra: Partial<Conversation> = {}): Conversation =>
  ({ id, title, created_at: '2026-11-01 10:00:00', updated_at: '2026-11-01 10:00:00', parent_id: null, ...extra })
const CHATS = [conv(1, 'Oyuncu hareketi'), conv(2, 'Kamera sarsıntısı'), conv(3, 'Envanter UI')]

function Harness(over: Partial<PhoneShellProps> & { onSelect?: (c: Conversation) => void } = {}) {
  const route = usePhoneRoute()
  const [active, setActive] = React.useState<number | null>(null)
  return (
    <div className="app" data-phone="">
      <PhoneShell
        route={route}
        conversations={over.conversations ?? CHATS}
        activeConvId={active}
        convStatus={{}}
        unknownGates={0}
        projectName="Platformer"
        onSwitchProject={vi.fn()}
        onOpenSettings={vi.fn()}
        selectConversation={(c) => { over.onSelect?.(c); setActive(c.id) }}
        createNewConversation={vi.fn()}
        session={null}
        chat={Harness.defaultChat}
        {...over}
      />
    </div>
  )
}
Harness.defaultChat = {
  thread: <div data-testid="thread">thread</div>,
  composer: <textarea aria-label="composer" />,
  contextUsage: { percent: 42, message_count: 3 } as any,
  menu: {
    models: { local: [], cloud: [], subscription: [] }, currentModel: { provider_type: 'subscription', model_name: 'x' },
    effortLevels: null, thinkingLevel: 'auto', onEffortChanged: vi.fn(), mode: 'step',
    loading: false, isCompacting: false, onCompact: vi.fn(), onStop: vi.fn(),
  },
} as PhoneShellProps['chat']

describe('phone mode is only the remote page at phone width', () => {
  it('remote + narrow: on; remote + wide: off; the desktop (not a remote page): off at any width', () => {
    expect(renderHook(() => usePhoneLayout()).result.current).toBe(true)
    width = 1024
    expect(renderHook(() => usePhoneLayout()).result.current).toBe(false)
    page.remote = false
    width = 390
    expect(renderHook(() => usePhoneLayout()).result.current).toBe(false)
  })

  it('follows a rotation live', () => {
    width = 1024
    const { result } = renderHook(() => usePhoneLayout())
    expect(result.current).toBe(false)
    width = 390
    act(() => { window.dispatchEvent(new Event('resize')) })
    expect(result.current).toBe(true)
    width = 900
    act(() => { window.dispatchEvent(new Event('resize')) })
    expect(result.current).toBe(false)
  })
})

describe('list -> chat -> Back -> list', () => {
  it("the phone's own Back (history) and the in-app back button return to the list; the URL never changes", async () => {
    const onSelect = vi.fn()
    const url = window.location.href
    render(<Harness onSelect={onSelect} />)
    expect(screen.getByText('Platformer')).toBeTruthy()
    fireEvent.click(screen.getByTestId('phone-row-2'))
    expect(onSelect).toHaveBeenCalledWith(CHATS[1])
    expect(screen.getByTestId('phone-menu')).toBeTruthy()
    expect(screen.getByText('Kamera sarsıntısı')).toBeTruthy()
    expect(screen.getByTestId('thread')).toBeTruthy()
    expect(window.location.href).toBe(url)
    expect((window.history.state as any)?.gmPhone).toBe('chat')

    act(() => { window.history.back() })
    await waitFor(() => expect(screen.getByTestId('phone-row-1')).toBeTruthy())
    expect(screen.queryByTestId('phone-menu')).toBeNull()

    fireEvent.click(screen.getByTestId('phone-row-3'))
    fireEvent.click(screen.getByRole('button', { name: tr['phone.back'] }))
    await waitFor(() => expect(screen.getByTestId('phone-row-1')).toBeTruthy())
    expect(window.location.href).toBe(url)
  })

  it('the chat tabs switch between the thread and the live screen', async () => {
    vi.stubGlobal('ipc', { invoke: vi.fn(async () => ({ error: 'x' })), on: vi.fn(() => () => {}) })
    render(<Harness />)
    fireEvent.click(screen.getByTestId('phone-row-1'))
    fireEvent.click(screen.getByRole('tab', { name: tr['phone.tabScreen'] }))
    // The chat stays mounted under the Ekran tab (its draft and scroll position live there), only hidden.
    expect(screen.getByTestId('thread').closest('[hidden]')).not.toBeNull()
    expect(screen.getByTestId('phone-composer').hasAttribute('hidden')).toBe(true)
    expect(await screen.findByText(tr['cu.live.off'])).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: tr['phone.tabChat'] }))
    expect(screen.getByTestId('thread').closest('[hidden]')).toBeNull()
    expect(screen.getByTestId('phone-composer').hasAttribute('hidden')).toBe(false)
    expect(screen.getByTestId('phone-memory').textContent).toBe(`${tr['strip.memory']} %42`)
  })
})

describe('the waiting bar', () => {
  it('counts waiting chats and chat-less requests, and opens the first waiting chat', () => {
    const onSelect = vi.fn()
    render(<Harness onSelect={onSelect} convStatus={{ 3: 'awaiting', 2: 'awaiting', 1: 'running' }} unknownGates={1} />)
    const bar = screen.getByTestId('phone-awaiting')
    expect(bar.textContent).toBe('⚠ 3 onay bekliyor')
    fireEvent.click(bar)
    expect(onSelect).toHaveBeenCalledWith(CHATS[1])
    expect(screen.getByTestId('phone-menu')).toBeTruthy()
  })

  it('is absent when nothing waits', () => {
    render(<Harness convStatus={{ 1: 'running' }} />)
    expect(screen.queryByTestId('phone-awaiting')).toBeNull()
  })
})

describe('the composer and the on-screen keyboard', () => {
  it('pads the composer by the visualViewport inset and follows its resize', () => {
    const vv = Object.assign(new EventTarget(), { height: 800, offsetTop: 0 })
    vi.stubGlobal('visualViewport', vv)
    vi.stubGlobal('innerHeight', 800)
    render(<Harness />)
    fireEvent.click(screen.getByTestId('phone-row-1'))
    const composer = screen.getByTestId('phone-composer')
    expect(composer.style.getPropertyValue('--phone-kb')).toBe('0px')
    act(() => { vv.height = 470; vv.dispatchEvent(new Event('resize')) })
    expect(composer.style.getPropertyValue('--phone-kb')).toBe('330px')
    // iOS scrolls the layout viewport up under the keyboard: offsetTop is part of the visible part.
    act(() => { vv.offsetTop = 30; vv.dispatchEvent(new Event('scroll')) })
    expect(composer.style.getPropertyValue('--phone-kb')).toBe('300px')
  })

  it('stays 0 where visualViewport is missing', () => {
    vi.stubGlobal('visualViewport', undefined)
    render(<Harness />)
    fireEvent.click(screen.getByTestId('phone-row-1'))
    expect(screen.getByTestId('phone-composer').style.getPropertyValue('--phone-kb')).toBe('0px')
  })
})

describe('connection: the line replaces the fixed banner only while the phone shell is on screen', () => {
  const session = (connection: 'ready' | 'offline', lastSeen: number | null) => ({
    view: { screen: 'app', paired: true, pairStep: 'idle', sas: null, message: null, connection, lastSeen, rejectReason: null },
    subscribe: () => () => {},
    onUnavailable: () => () => {},
    start: async () => {},
  }) as any

  it('the project picker (no shell) keeps the banner', () => {
    const { container } = render(<RemoteGate session={session('offline', null)}><div>picker</div></RemoteGate>)
    expect(container.querySelector('.gm-remote-banner')?.textContent).toBe(tr['remoteWeb.offline'])
  })

  it('with the shell: no banner, the line says offline with the last-seen time and marks what is shown', () => {
    const seen = new Date(2026, 10, 4, 14, 32).getTime()
    const s = session('offline', seen)
    const { container } = render(<RemoteGate session={s}><Harness session={s} /></RemoteGate>)
    expect(container.querySelector('.gm-remote-banner')).toBeNull()
    const line = screen.getByTestId('phone-conn')
    expect(line.getAttribute('data-state')).toBe('offline')
    expect(line.textContent).toContain(`${tr['remoteWeb.offline']} · son görülme 14:32`)
    expect(line.textContent).toContain(tr['phone.stale'])
    expect(container.querySelector('.phone-shell')!.hasAttribute('data-stale')).toBe(true)
  })

  it('connected reads "● Bağlı"', () => {
    render(<Harness session={session('ready', null)} />)
    expect(screen.getByTestId('phone-conn').textContent).toBe(tr['phone.connected'])
  })
})

// The real composer (AnimatedChatInput keeps what is typed in its own state) inside the shell.
function DraftHarness({ ids = [1, 2], mentions = false }: { ids?: number[]; mentions?: boolean }) {
  const route = usePhoneRoute()
  const [active, setActive] = React.useState<number | null>(null)
  const [v, setV] = React.useState('')
  const chats = ids.map(id => conv(id, `Sohbet ${id}`))
  return (
    <PhoneShell route={route} conversations={chats} activeConvId={active} convStatus={{}} unknownGates={0} projectName="P"
      onSwitchProject={vi.fn()} onOpenSettings={vi.fn()} selectConversation={(c) => setActive(c.id)} createNewConversation={vi.fn()} session={null}
      chat={{
        ...Harness.defaultChat,
        composer: <AnimatedChatInput value={v} setValue={setV} onSendMessage={vi.fn()} isLoading={false} currentChatId={active} chats={mentions ? chats : undefined} />,
      }} />
  )
}
const box = () => document.querySelector('textarea') as HTMLTextAreaElement

describe('the composer draft', () => {
  it('survives Sohbet -> Ekran -> Sohbet', () => {
    vi.stubGlobal('ipc', { invoke: vi.fn(async () => ({ error: 'x' })), on: vi.fn(() => () => {}) })
    render(<DraftHarness />)
    fireEvent.click(screen.getByTestId('phone-row-1'))
    fireEvent.change(box(), { target: { value: 'yarim kalan mesaj' } })
    fireEvent.click(screen.getByRole('tab', { name: tr['phone.tabScreen'] }))
    fireEvent.click(screen.getByRole('tab', { name: tr['phone.tabChat'] }))
    expect(box().value).toBe('yarim kalan mesaj')
  })

  it('survives Back to the list and reopening, and stays with its own chat', async () => {
    render(<DraftHarness />)
    fireEvent.click(screen.getByTestId('phone-row-1'))
    fireEvent.change(box(), { target: { value: 'birinci taslak' } })
    fireEvent.click(screen.getByRole('button', { name: tr['phone.back'] }))
    await waitFor(() => expect(screen.getByTestId('phone-row-1')).toBeTruthy())
    fireEvent.click(screen.getByTestId('phone-row-2'))
    expect(box().value).toBe('')
    fireEvent.change(box(), { target: { value: 'ikinci taslak' } })
    fireEvent.click(screen.getByRole('button', { name: tr['phone.back'] }))
    await waitFor(() => expect(screen.getByTestId('phone-row-1')).toBeTruthy())
    fireEvent.click(screen.getByTestId('phone-row-1'))
    expect(box().value).toBe('birinci taslak')
    fireEvent.click(screen.getByRole('button', { name: tr['phone.back'] }))
    await waitFor(() => expect(screen.getByTestId('phone-row-2')).toBeTruthy())
    fireEvent.click(screen.getByTestId('phone-row-2'))
    expect(box().value).toBe('ikinci taslak')
  })
})

describe('the waiting bar with a request of no chat', () => {
  it('with no chat at all, opens the chat screen where the tray shows', () => {
    render(<Harness conversations={[]} unknownGates={1} chat={{ ...Harness.defaultChat, tray: <div data-testid="tray">istek</div> }} />)
    fireEvent.click(screen.getByTestId('phone-awaiting'))
    expect(screen.getByTestId('tray')).toBeTruthy()
    expect(screen.getByTestId('thread')).toBeTruthy()
  })
})

describe('+ Yeni', () => {
  it('waits for the new chat: the list stays (busy) until it exists, then the chat opens', async () => {
    let done!: (id: number | null) => void
    const create = vi.fn(() => new Promise<number | null>(r => { done = r }))
    render(<Harness createNewConversation={create} />)
    fireEvent.click(screen.getByTestId('phone-new-chat'))
    expect(create).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('phone-menu')).toBeNull()
    expect(screen.getByTestId('phone-new-chat').hasAttribute('disabled')).toBe(true)
    expect(screen.getByTestId('phone-new-chat').textContent).toBe(tr['phone.creating'])
    await act(async () => { done(9) })
    expect(screen.getByTestId('phone-menu')).toBeTruthy()
  })

  it('a chat that could not be created leaves the user on the list', async () => {
    render(<Harness createNewConversation={() => Promise.resolve(null)} />)
    fireEvent.click(screen.getByTestId('phone-new-chat'))
    await waitFor(() => expect(screen.getByTestId('phone-new-chat').hasAttribute('disabled')).toBe(false))
    expect(screen.queryByTestId('phone-menu')).toBeNull()
  })
})

describe('the connection while a full-screen page covers the shell', () => {
  it('the banner is drawn for Settings and steps aside again with the shell', () => {
    const s = {
      view: { screen: 'app', paired: true, pairStep: 'idle', sas: null, message: null, connection: 'offline', lastSeen: null, rejectReason: null },
      subscribe: () => () => {}, onUnavailable: () => () => {}, start: async () => {},
    } as any
    const { container, rerender } = render(<RemoteGate session={s}><Harness session={s} covered /></RemoteGate>)
    expect(container.querySelector('.gm-remote-banner')?.textContent).toBe(tr['remoteWeb.offline'])
    rerender(<RemoteGate session={s}><Harness session={s} /></RemoteGate>)
    expect(container.querySelector('.gm-remote-banner')).toBeNull()
  })
})

describe('the waiting bar with a waiting branch', () => {
  const FAMILY = [conv(1, 'Kök'), conv(5, 'Dal', { parent_id: 1 })]

  it('opens the branch that is waiting, not its root, and a row of that family opens it too', async () => {
    const onSelect = vi.fn()
    render(<Harness onSelect={onSelect} conversations={FAMILY} convStatus={{ 5: 'awaiting' }} />)
    expect(screen.getByTestId('phone-awaiting').textContent).toBe('⚠ 1 onay bekliyor')
    fireEvent.click(screen.getByTestId('phone-awaiting'))
    expect(onSelect).toHaveBeenLastCalledWith(FAMILY[1])
    fireEvent.click(screen.getByRole('button', { name: tr['phone.back'] }))
    await waitFor(() => expect(screen.getByTestId('phone-row-1')).toBeTruthy())
    onSelect.mockClear()
    fireEvent.click(screen.getByTestId('phone-row-1'))
    expect(onSelect).toHaveBeenLastCalledWith(FAMILY[1])
  })

  it("the chat screen's title is the branch's own", () => {
    render(<Harness conversations={FAMILY} convStatus={{ 5: 'awaiting' }} />)
    fireEvent.click(screen.getByTestId('phone-awaiting'))
    expect(screen.getByRole('region', { name: 'Dal' })).toBeTruthy()
  })
})

describe('the Sohbet tab shown again after the Ekran tab', () => {
  it('tells the page once, and only when coming back from Ekran', () => {
    vi.stubGlobal('ipc', { invoke: vi.fn(async () => ({ error: 'x' })), on: vi.fn(() => () => {}) })
    const onThreadShown = vi.fn()
    render(<Harness chat={{ ...Harness.defaultChat, onThreadShown }} />)
    fireEvent.click(screen.getByTestId('phone-row-1'))
    expect(onThreadShown).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('tab', { name: tr['phone.tabScreen'] }))
    expect(onThreadShown).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('tab', { name: tr['phone.tabChat'] }))
    expect(onThreadShown).toHaveBeenCalledTimes(1)
  })
})

describe('the composer draft set from outside the typing path', () => {
  it('a picked @mention stays with its chat: the next chat is empty, the first one has it back', async () => {
    render(<DraftHarness mentions />)
    fireEvent.click(screen.getByTestId('phone-row-1'))
    fireEvent.change(box(), { target: { value: '@' } })
    fireEvent.click(await screen.findByTestId('mention-option-2'))
    expect(box().value).toBe('@2 ')
    fireEvent.click(screen.getByRole('button', { name: tr['phone.back'] }))
    await waitFor(() => expect(screen.getByTestId('phone-row-2')).toBeTruthy())
    fireEvent.click(screen.getByTestId('phone-row-2'))
    expect(box().value).toBe('')
    fireEvent.click(screen.getByRole('button', { name: tr['phone.back'] }))
    await waitFor(() => expect(screen.getByTestId('phone-row-1')).toBeTruthy())
    fireEvent.click(screen.getByTestId('phone-row-1'))
    expect(box().value).toBe('@2 ')
  })
})

describe('a chat opened again starts at its end', () => {
  it('asks the page to follow the thread every time a chat screen opens', async () => {
    const onThreadOpened = vi.fn()
    render(<Harness chat={{ ...Harness.defaultChat, onThreadOpened }} />)
    expect(onThreadOpened).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('phone-row-1'))
    expect(onThreadOpened).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: tr['phone.back'] }))
    await waitFor(() => expect(screen.getByTestId('phone-row-1')).toBeTruthy())
    fireEvent.click(screen.getByTestId('phone-row-1'))
    // A waiting card is drawn at the end of the thread: a reopened chat must not start at its top.
    expect(onThreadOpened).toHaveBeenCalledTimes(2)
  })
})

describe('a phone draft across a rotation to a wide layout', () => {
  // home.tsx owns the drafts above the phone branch; rotating unmounts only the shell.
  function RotateHarness({ phone }: { phone: boolean }) {
    const route = usePhoneRoute()
    const drafts = React.useRef(new Map()).current
    const [active, setActive] = React.useState<number | null>(null)
    const [v, setV] = React.useState('')
    const chats = [conv(1, 'Sohbet 1')]
    if (!phone) return <div data-testid="wide" />
    return (
      <PhoneShell route={route} drafts={drafts} conversations={chats} activeConvId={active} convStatus={{}} unknownGates={0} projectName="P"
        onSwitchProject={vi.fn()} onOpenSettings={vi.fn()} selectConversation={(c) => setActive(c.id)} createNewConversation={vi.fn()} session={null}
        chat={{ ...Harness.defaultChat, composer: <AnimatedChatInput value={v} setValue={setV} onSendMessage={vi.fn()} isLoading={false} currentChatId={active} /> }} />
    )
  }

  it('keeps the half-written message when the phone shell unmounts and comes back', () => {
    const { rerender } = render(<RotateHarness phone />)
    fireEvent.click(screen.getByTestId('phone-row-1'))
    fireEvent.change(box(), { target: { value: 'dönerken kaybolmasın' } })
    rerender(<RotateHarness phone={false} />)
    expect(screen.getByTestId('wide')).toBeTruthy()
    rerender(<RotateHarness phone />)
    expect(box().value).toBe('dönerken kaybolmasın')
  })
})
